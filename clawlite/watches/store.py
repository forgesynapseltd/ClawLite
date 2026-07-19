"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

watches/store.py — Persistencia de "watches" (cron en lenguaje natural por evento)

Un WATCH es el tercer concepto del sistema de autonomía, distinto de los dos
existentes:

  • reminder  → dispara por TIEMPO (a las 9:00, en 2 horas). One-shot.
  • job       → trabajo pesado en background. One-shot (queued→running→done).
  • watch     → suscripción PERSISTENTE a una CONDICIÓN del mundo
                ("cuando llegue un correo de ...", "cuando aparezca ...").
                Se evalúa periódicamente y puede disparar muchas veces.

Por eso vive en su propia tabla y no reusa async_jobs: la semántica es
recurrente y con estado de "qué ya vi", no un ciclo de vida que termina.

Diseño espejo de JobStore / DeepMemory: conexión por operación (SQLite maneja
el locking), misma DB local, sin dependencias hacia el core.

Modelo de datos:
  source       → qué fuente de evento vigilar (ej: "gmail_match"). El catálogo
                 de fuentes vive en watches/sources.py (registro plugin-style).
  params_json  → parámetros de la condición, dependientes de la fuente
                 (ej: {"from_contains": "...", "subject_contains": "..."}).
  action       → qué hacer al dispararse. Hoy solo "notify" (avisar al usuario).
                 Campo explícito para no cerrar la puerta a acciones futuras.
  state_json   → estado de evaluación de la fuente entre ciclos, opaco al store
                 (ej: ids ya vistos para no re-notificar). Lo gestiona la fuente.

Estados del watch:
  active     → vigilando
  paused     → el usuario lo pausó (no se evalúa, pero se conserva)
  cancelled  → el usuario lo eliminó (soft-delete: trazable, no se evalúa)
"""

import json
import sqlite3
from datetime import datetime
from loguru import logger


VALID_STATUSES = {"active", "paused", "cancelled"}
VALID_ACTIONS = {"notify"}


class WatchStore:
    """
    Persiste watches en SQLite. Una conexión por operación.
    No conoce al core ni a las fuentes concretas: solo guarda y devuelve filas.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS event_watches (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         TEXT NOT NULL,
                    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_checked_at DATETIME,
                    last_fired_at   DATETIME,
                    status          TEXT NOT NULL DEFAULT 'active',
                    description     TEXT NOT NULL,
                    source          TEXT NOT NULL,
                    params_json     TEXT,
                    action          TEXT NOT NULL DEFAULT 'notify',
                    state_json      TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_watches_user_status
                    ON event_watches(user_id, status);
                CREATE INDEX IF NOT EXISTS idx_watches_status
                    ON event_watches(status);
            """)

    # ── CREACIÓN ─────────────────────────────────────────────────────────────

    def create(
        self,
        user_id: str,
        description: str,
        source: str,
        params: dict | None = None,
        action: str = "notify",
    ) -> int:
        """
        Crea un watch en estado 'active'. Devuelve el id asignado.

        No valida `source` contra el catálogo a propósito: el catálogo vive en
        otra capa (watches/sources.py) y el store no debe acoplarse a él. La
        validación de fuente la hace quien crea el watch (el handler del core),
        que es quien conoce las fuentes disponibles.
        """
        if action not in VALID_ACTIONS:
            raise ValueError(f"action inválida: {action}. Válidas: {VALID_ACTIONS}")

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO event_watches
                       (user_id, description, source, params_json, action, state_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    description[:300],
                    source,
                    json.dumps(params or {}),
                    action,
                    json.dumps({}),
                ),
            )
            watch_id = cursor.lastrowid

        logger.info(f"👁️ Watch #{watch_id} creado [{source}] para {user_id}: {description[:60]}")
        return watch_id

    # ── LECTURA ──────────────────────────────────────────────────────────────

    def get(self, watch_id: int) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM event_watches WHERE id = ?", (watch_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_active_for_user(self, user_id: str) -> list[dict]:
        """Watches que se están evaluando ahora para un usuario."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM event_watches
                   WHERE user_id = ? AND status = 'active'
                   ORDER BY created_at ASC""",
                (user_id,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_by_user(self, user_id: str, include_cancelled: bool = False) -> list[dict]:
        """
        Lista los watches del usuario para mostrarlos (comando /watches).
        Por defecto oculta los cancelados (soft-deleted).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if include_cancelled:
                rows = conn.execute(
                    """SELECT * FROM event_watches
                       WHERE user_id = ? ORDER BY created_at DESC""",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM event_watches
                       WHERE user_id = ? AND status != 'cancelled'
                       ORDER BY created_at DESC""",
                    (user_id,),
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── ACTUALIZACIÓN DE ESTADO DE EVALUACIÓN ────────────────────────────────

    def mark_checked(self, watch_id: int):
        """Sella la hora de la última evaluación (haya disparado o no)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE event_watches SET last_checked_at = ? WHERE id = ?",
                (datetime.now().isoformat(), watch_id),
            )

    def mark_fired(self, watch_id: int):
        """Sella la hora del último disparo efectivo (hubo evento que notificar)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE event_watches SET last_fired_at = ? WHERE id = ?",
                (datetime.now().isoformat(), watch_id),
            )

    def update_state(self, watch_id: int, state: dict):
        """
        Persiste el estado de evaluación que la fuente necesita entre ciclos
        (ej: ids de correo ya notificados). El store trata `state` como opaco.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE event_watches SET state_json = ? WHERE id = ?",
                (json.dumps(state), watch_id),
            )

    # ── CICLO DE VIDA (SOBERANÍA DEL USUARIO) ────────────────────────────────

    def pause(self, watch_id: int, user_id: str) -> bool:
        """Pausa un watch activo. Reversible con resume()."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """UPDATE event_watches SET status = 'paused'
                   WHERE id = ? AND user_id = ? AND status = 'active'""",
                (watch_id, user_id),
            )
        ok = cur.rowcount > 0
        if ok:
            logger.info(f"⏸️ Watch #{watch_id} pausado por {user_id}")
        return ok

    def resume(self, watch_id: int, user_id: str) -> bool:
        """Reactiva un watch pausado."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """UPDATE event_watches SET status = 'active'
                   WHERE id = ? AND user_id = ? AND status = 'paused'""",
                (watch_id, user_id),
            )
        ok = cur.rowcount > 0
        if ok:
            logger.info(f"▶️ Watch #{watch_id} reactivado por {user_id}")
        return ok

    def cancel(self, watch_id: int, user_id: str) -> bool:
        """
        Elimina un watch (soft-delete). Soberanía de datos: el usuario es dueño
        de sus vigilancias. Funciona en cualquier estado salvo ya-cancelado.
        """
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """UPDATE event_watches SET status = 'cancelled'
                   WHERE id = ? AND user_id = ? AND status != 'cancelled'""",
                (watch_id, user_id),
            )
        ok = cur.rowcount > 0
        if ok:
            logger.info(f"🚫 Watch #{watch_id} cancelado por {user_id}")
        return ok

    # ── INTERNO ──────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row) -> dict:
        if row is None:
            return {}
        d = dict(row)
        try:
            d["params"] = json.loads(d.get("params_json") or "{}")
        except Exception:
            d["params"] = {}
        try:
            d["state"] = json.loads(d.get("state_json") or "{}")
        except Exception:
            d["state"] = {}
        return d
