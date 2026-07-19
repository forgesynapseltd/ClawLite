"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

jobs/store.py — Persistencia de jobs asíncronos largos
Cada job representa una tarea que corre en background:
investigación profunda, generación de proyectos de código, calendarios mensuales, etc.

Estados:
  queued     → recién creado, esperando slot del runner
  running    → un worker lo está ejecutando ahora
  completed  → terminó OK, hay resultado
  failed     → terminó con error, hay traza
  cancelled  → el usuario lo canceló antes de empezar
"""

import json
import sqlite3
from datetime import datetime, timedelta
from loguru import logger


VALID_STATUSES = {"queued", "running", "completed", "failed", "cancelled"}
VALID_TYPES = {"research", "coding", "brand_calendar"}


class JobStore:
    """
    Persiste jobs en SQLite. Diseñado para ser thread-safe usando
    una conexión por operación (SQLite maneja el locking internamente).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS async_jobs (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      TEXT NOT NULL,
                    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                    started_at   DATETIME,
                    finished_at  DATETIME,
                    status       TEXT NOT NULL DEFAULT 'queued',
                    title        TEXT NOT NULL,
                    request      TEXT NOT NULL,
                    job_type     TEXT NOT NULL,
                    config_json  TEXT,
                    progress     TEXT,
                    result       TEXT,
                    error        TEXT,
                    attempts     INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_user_status
                    ON async_jobs(user_id, status);
                CREATE INDEX IF NOT EXISTS idx_jobs_status
                    ON async_jobs(status);
            """)

            # Migración idempotente para DBs creadas antes de la robustez 24/7.
            cols = [r[1] for r in conn.execute("PRAGMA table_info(async_jobs)").fetchall()]
            if "attempts" not in cols:
                conn.execute("ALTER TABLE async_jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            if "max_attempts" not in cols:
                conn.execute("ALTER TABLE async_jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3")

    # ── CREACIÓN ─────────────────────────────────────────────────────────────

    def create(
        self,
        user_id: str,
        title: str,
        request: str,
        job_type: str,
        config: dict | None = None,
    ) -> int:
        """Crea un job en estado 'queued'. Devuelve el id asignado."""
        if job_type not in VALID_TYPES:
            raise ValueError(f"job_type inválido: {job_type}. Válidos: {VALID_TYPES}")

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO async_jobs (user_id, title, request, job_type, config_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, title[:200], request, job_type, json.dumps(config or {})),
            )
            job_id = cursor.lastrowid

        logger.info(f"📋 Job #{job_id} creado [{job_type}] para {user_id}: {title[:60]}")
        return job_id

    # ── LECTURA ──────────────────────────────────────────────────────────────

    def get(self, job_id: int) -> dict | None:
        """Devuelve un job completo por id, o None si no existe."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM async_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_by_user(
        self,
        user_id: str,
        include_active: bool = True,
        include_finished: bool = True,
        finished_limit: int = 5,
    ) -> list[dict]:
        """
        Lista jobs del usuario.
        Activos (queued/running) primero, luego los N últimos finalizados.
        """
        results = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if include_active:
                rows = conn.execute(
                    """SELECT * FROM async_jobs
                       WHERE user_id = ? AND status IN ('queued', 'running')
                       ORDER BY created_at DESC""",
                    (user_id,),
                ).fetchall()
                results.extend(self._row_to_dict(r) for r in rows)

            if include_finished:
                rows = conn.execute(
                    """SELECT * FROM async_jobs
                       WHERE user_id = ? AND status IN ('completed', 'failed', 'cancelled')
                       ORDER BY finished_at DESC LIMIT ?""",
                    (user_id, finished_limit),
                ).fetchall()
                results.extend(self._row_to_dict(r) for r in rows)

        return results

    def claim_next_queued(self, limit: int) -> list[dict]:
        """
        Reserva atómicamente hasta `limit` jobs en estado 'queued' moviéndolos
        a 'running'. Esto evita race conditions si hubiera múltiples workers.
        """
        claimed = []
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Selección + update en transacción
            rows = conn.execute(
                """SELECT id FROM async_jobs WHERE status = 'queued'
                   ORDER BY created_at ASC LIMIT ?""",
                (limit,),
            ).fetchall()

            for r in rows:
                conn.execute(
                    """UPDATE async_jobs
                       SET status = 'running', started_at = ?, attempts = attempts + 1
                       WHERE id = ? AND status = 'queued'""",
                    (now, r["id"]),
                )

            for r in rows:
                row = conn.execute(
                    "SELECT * FROM async_jobs WHERE id = ?", (r["id"],)
                ).fetchone()
                if row and row["status"] == "running":
                    claimed.append(self._row_to_dict(row))

        return claimed

    def find_orphans(self) -> list[dict]:
        """
        Jobs que quedaron en 'running' tras un reinicio (el proceso anterior
        murió mientras los ejecutaba).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM async_jobs WHERE status = 'running'"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def requeue_orphans(self) -> tuple[list[dict], list[dict]]:
        """
        Robustez 24/7: un job que quedó 'running' tras un reinicio no se pierde.
        Si aún le quedan intentos, vuelve a 'queued' y se ejecutará solo otra vez
        (reinicio limpio — los ejecutores no tienen checkpoints, reanudar exacto
        sería ilusorio; reintentar entero sí es honesto).

        El contador de intentos es la red de seguridad CRÍTICA: un job que crashea
        el proceso entero, sin límite, reiniciaría en bucle y tumbaría ClawLite en
        cada arranque. Pasado max_attempts, el job se da por fallido definitivo.

        Devuelve (reencolados, agotados) para que el runner avise solo de los que
        murieron de verdad — los reencolados son invisibles para el usuario.
        """
        requeued, exhausted = [], []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM async_jobs WHERE status = 'running'"
            ).fetchall()

            for r in rows:
                job = self._row_to_dict(r)
                if job["attempts"] < job["max_attempts"]:
                    conn.execute(
                        """UPDATE async_jobs
                           SET status = 'queued', started_at = NULL, progress = NULL
                           WHERE id = ?""",
                        (job["id"],),
                    )
                    requeued.append(job)
                else:
                    conn.execute(
                        """UPDATE async_jobs
                           SET status = 'failed', finished_at = ?,
                               error = 'Job agotó los reintentos tras sucesivos reinicios.'
                           WHERE id = ?""",
                        (datetime.now().isoformat(), job["id"]),
                    )
                    exhausted.append(job)

        if requeued:
            logger.info(f"♻️  {len(requeued)} jobs huérfanos reencolados (sobreviven al reinicio)")
        if exhausted:
            logger.warning(f"🪦 {len(exhausted)} jobs huérfanos agotaron reintentos")
        return requeued, exhausted

    # ── ACTUALIZACIÓN ────────────────────────────────────────────────────────

    def update_progress(self, job_id: int, progress: str):
        """Actualiza la nota de progreso del job (no cambia status)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE async_jobs SET progress = ? WHERE id = ?",
                (progress[:500], job_id),
            )

    def set_completed(self, job_id: int, result: str):
        """Marca el job como completado y guarda el resultado final."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE async_jobs
                   SET status = 'completed', finished_at = ?, result = ?, error = NULL
                   WHERE id = ?""",
                (datetime.now().isoformat(), result, job_id),
            )
        logger.info(f"✅ Job #{job_id} completado")

    def set_failed(self, job_id: int, error: str) -> bool:
        """
        Resuelve un fallo de ejecución. Si al job le quedan intentos, vuelve a
        'queued' para reintentarse solo (fallo recuperable: API caída, red, etc.).
        Si agotó max_attempts, falla definitivo.

        Devuelve True si el job se REINTENTARÁ (el runner NO debe notificar fallo
        en ese caso — el reintento es invisible), False si murió de verdad.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT attempts, max_attempts FROM async_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return False

            will_retry = row["attempts"] < row["max_attempts"]
            if will_retry:
                conn.execute(
                    """UPDATE async_jobs
                       SET status = 'queued', started_at = NULL, progress = NULL,
                           error = ?
                       WHERE id = ?""",
                    (error[:5000], job_id),
                )
            else:
                conn.execute(
                    """UPDATE async_jobs
                       SET status = 'failed', finished_at = ?, error = ?
                       WHERE id = ?""",
                    (datetime.now().isoformat(), error[:5000], job_id),
                )

        if will_retry:
            logger.warning(f"♻️  Job #{job_id} falló pero se reintentará: {error[:120]}")
        else:
            logger.warning(f"❌ Job #{job_id} falló definitivo: {error[:150]}")
        return will_retry

    def cancel(self, job_id: int, user_id: str) -> bool:
        """
        Cancela un job. Solo funciona si está en 'queued' (no se interrumpe
        un job que ya está corriendo). Devuelve True si lo canceló.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """UPDATE async_jobs
                   SET status = 'cancelled', finished_at = ?
                   WHERE id = ? AND user_id = ? AND status = 'queued'""",
                (datetime.now().isoformat(), job_id, user_id),
            )
            cancelled = cursor.rowcount > 0
        if cancelled:
            logger.info(f"🚫 Job #{job_id} cancelado por {user_id}")
        return cancelled

    # ── LIMPIEZA ─────────────────────────────────────────────────────────────

    def cleanup_old(self, days: int = 30):
        """Borra jobs finalizados más viejos que N días."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """DELETE FROM async_jobs
                   WHERE status IN ('completed', 'failed', 'cancelled')
                     AND finished_at < ?""",
                (cutoff,),
            )
            deleted = cursor.rowcount
        if deleted:
            logger.info(f"🧹 {deleted} jobs viejos limpiados (>{days} días)")

    # ── INTERNO ──────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row) -> dict:
        if row is None:
            return {}
        d = dict(row)
        # Deserializar config_json
        try:
            d["config"] = json.loads(d.get("config_json") or "{}")
        except Exception:
            d["config"] = {}
        d.setdefault("attempts", 0)
        d.setdefault("max_attempts", 3)
        return d
