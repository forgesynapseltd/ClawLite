"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

memory/profile.py — UserProfile, DeepMemory y Reminders
"""

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger
from clawlite.llm.client import llm


class DeepMemory:

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS user_facts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT NOT NULL,
                    fact        TEXT NOT NULL,
                    confidence  REAL DEFAULT 1.0,
                    superseded  INTEGER DEFAULT 0,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS goals (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT NOT NULL,
                    goal        TEXT NOT NULL,
                    status      TEXT DEFAULT 'active',
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT NOT NULL,
                    task        TEXT NOT NULL,
                    status      TEXT DEFAULT 'pending',
                    due_date    DATETIME,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS interests (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT NOT NULL,
                    topic       TEXT NOT NULL,
                    weight      REAL DEFAULT 1.0,
                    last_seen   DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS patterns (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT NOT NULL,
                    pattern     TEXT NOT NULL,
                    data        TEXT,
                    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT NOT NULL,
                    message     TEXT NOT NULL,
                    remind_at   DATETIME NOT NULL,
                    status      TEXT DEFAULT 'pending',
                    recurrence  TEXT DEFAULT '',
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_facts_user ON user_facts(user_id);
                CREATE INDEX IF NOT EXISTS idx_goals_user ON goals(user_id);
                CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id, status);
                CREATE INDEX IF NOT EXISTS idx_interests_user ON interests(user_id);
                CREATE INDEX IF NOT EXISTS idx_patterns_user ON patterns(user_id);
                CREATE INDEX IF NOT EXISTS idx_reminders ON reminders(user_id, status, remind_at);
            """)

            # Migración idempotente: añade recurrence a DBs creadas antes de esta
            # columna. SQLite no soporta "ADD COLUMN IF NOT EXISTS", así que se
            # comprueba en el catálogo de columnas antes de alterar.
            cols = [r[1] for r in conn.execute("PRAGMA table_info(reminders)").fetchall()]
            if "recurrence" not in cols:
                conn.execute("ALTER TABLE reminders ADD COLUMN recurrence TEXT DEFAULT ''")

            # Migración: columna 'superseded' para la memoria auto-organizada (supersede
            # sin borrar: el hecho viejo se marca, no se elimina).
            fcols = [r[1] for r in conn.execute("PRAGMA table_info(user_facts)").fetchall()]
            if "superseded" not in fcols:
                conn.execute("ALTER TABLE user_facts ADD COLUMN superseded INTEGER DEFAULT 0")

            # Migración: columna 'category' para la auto-organización por world-model.
            # El reemplazo (supersede) es un match determinista de categoría de valor
            # único; 'other' = catch-all multivaluado que nunca se supersede. Las DBs
            # previas quedan con 'other' (seguro: nunca reemplazan hasta reclasificarse).
            if "category" not in fcols:
                conn.execute("ALTER TABLE user_facts ADD COLUMN category TEXT DEFAULT 'other'")

    # ── FACTS ──────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Forma canónica de un texto para detectar duplicados de forma
        DETERMINISTA: minúsculas, espacios colapsados y puntuación de borde
        fuera. No usa el modelo ni embeddings — nunca puede fusionar dos
        textos distintos. Genérico: usado por facts (vía _normalize_fact,
        alias de compatibilidad), goals y tasks."""
        t = (text or "").strip().lower()
        t = re.sub(r"\s+", " ", t)
        return t.strip(" .,;:!¡¿?\"'")

    @staticmethod
    def _normalize_fact(text: str) -> str:
        """Alias de compatibilidad — ver _normalize_text() (misma lógica,
        ahora genérica). Se mantiene este nombre para no tocar los call
        sites existentes de facts."""
        return DeepMemory._normalize_text(text)

    def add_fact(self, user_id: str, fact: str, confidence: float = 1.0,
                 category: str = "other"):
        # Auto-organización (dedup en escritura): si ya existe un hecho equivalente
        # (misma forma normalizada), NO se duplica — se refresca y se conserva la
        # redacción más informativa (la más larga). Así el perfil no se infla con
        # "me llamo Fernando" repetido en cada sesión. Determinista y seguro.
        # `category` viene del world-model (clasificación cerrada); se persiste para que
        # el supersede por categoría sea determinista. 'other' = catch-all seguro.
        norm = self._normalize_fact(fact)
        if not norm:
            return
        with sqlite3.connect(self.db_path) as conn:
            # Solo entre los ACTIVOS: si el hecho fue superseded y el usuario lo
            # reafirma, se vuelve a añadir como vigente (auto-corrección de la memoria).
            existing = conn.execute(
                "SELECT id, fact FROM user_facts WHERE user_id = ? AND superseded = 0", (user_id,)
            ).fetchall()
            for row_id, existing_fact in existing:
                if self._normalize_fact(existing_fact) == norm:
                    better = fact if len(fact) > len(existing_fact) else existing_fact
                    conn.execute(
                        "UPDATE user_facts SET fact = ?, confidence = MAX(confidence, ?), "
                        "category = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (better, confidence, category, row_id),
                    )
                    logger.debug(f"🧠 Fact dedup (ya existía equivalente): {fact[:60]}")
                    return
            conn.execute(
                "INSERT INTO user_facts (user_id, fact, confidence, category) VALUES (?, ?, ?, ?)",
                (user_id, fact, confidence, category)
            )
        logger.debug(f"🧠 Fact added for {user_id}: {fact[:60]} [{category}]")

    def consolidate_facts(self, user_id: str) -> int:
        """Pase de auto-organización sobre los hechos YA acumulados: agrupa por forma
        normalizada y deja por grupo el más informativo, borrando el resto. Determinista
        (no fusiona hechos distintos). Devuelve cuántos duplicados eliminó. Limpia el
        backlog anterior al dedup-en-escritura."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, fact FROM user_facts WHERE user_id = ? AND superseded = 0 ORDER BY id",
                (user_id,)
            ).fetchall()
            seen: dict[str, tuple[int, str]] = {}  # norm -> (id, fact) que se conserva
            to_delete: list[int] = []
            for row_id, fact in rows:
                norm = self._normalize_fact(fact)
                if not norm:
                    continue
                if norm in seen:
                    keep_id, keep_fact = seen[norm]
                    if len(fact) > len(keep_fact):       # el nuevo es más informativo
                        to_delete.append(keep_id)
                        seen[norm] = (row_id, fact)
                    else:
                        to_delete.append(row_id)
                else:
                    seen[norm] = (row_id, fact)
            for del_id in to_delete:
                conn.execute(
                    "DELETE FROM user_facts WHERE id = ? AND user_id = ?", (del_id, user_id)
                )
        if to_delete:
            logger.info(f"🧹 Consolidación de memoria: {len(to_delete)} hechos duplicados eliminados para {user_id}")
        return len(to_delete)

    # Umbral de mensajes nuevos (desde el último corte) para disparar una
    # actualización de resumen — NO es un intervalo de tiempo. El ciclo de
    # 15 min de ProactivityEngine es solo DÓNDE se revisa la condición,
    # nunca la razón de disparo. El doble de get_recent() default (10),
    # para no resumir por ráfagas cortas ni dejar pasar demasiado sin resumir.
    HISTORY_SUMMARY_MIN_NEW_MESSAGES = 20

    # Tope de mensajes por actualización — evita que un usuario con cientos
    # de mensajes acumulados entre ciclos mande todo de una sola vez al LLM
    # (coste y límite de contexto). Si quedan mensajes pendientes tras el
    # tope, summarized_up_to_message_id avanza solo hasta el último
    # REALMENTE resumido — el siguiente ciclo continúa desde ahí.
    MAX_MESSAGES_PER_SUMMARY_UPDATE = 100

    async def update_history_summary(self, user_id: str) -> bool:
        """Resumen RODANTE de historial: si hay >= HISTORY_SUMMARY_MIN_NEW_MESSAGES
        mensajes nuevos desde el último corte, genera un resumen actualizado
        combinando el resumen anterior + hasta MAX_MESSAGES_PER_SUMMARY_UPDATE
        mensajes nuevos (nunca re-resume todo el historial, nunca manda un
        lote sin tope). Se guarda en patterns (clave 'conversation_summary'),
        mismo mecanismo que user_level/onboarding_profile. Fail-safe: si el
        LLM falla o devuelve vacío, se mantiene el resumen anterior intacto —
        nunca un resumen corrupto ni un error visible al usuario. Devuelve
        True si actualizó, False si no había suficientes mensajes nuevos o falló."""
        pattern = self.get_pattern(user_id, "conversation_summary")
        last_id = pattern.get("summarized_up_to_message_id", 0)

        with sqlite3.connect(self.db_path) as conn:
            pending_count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id = ? AND id > ?",
                (user_id, last_id)
            ).fetchone()[0]

            if pending_count < self.HISTORY_SUMMARY_MIN_NEW_MESSAGES:
                return False

            new_messages = conn.execute(
                "SELECT id, role, content FROM messages WHERE user_id = ? AND id > ? "
                "ORDER BY id LIMIT ?",
                (user_id, last_id, self.MAX_MESSAGES_PER_SUMMARY_UPDATE)
            ).fetchall()

        if not new_messages:
            return False

        previous_summary = pattern.get("summary", "")
        transcript = "\n".join(f"{role}: {content}" for _, role, content in new_messages)
        prompt = (
            "You maintain a running summary of an ongoing conversation between "
            "a user and their AI assistant. This summary accumulates across many "
            "updates over time, so follow these rules strictly to avoid drift:\n"
            "- Preserve facts, decisions and context from the previous summary "
            "that are still relevant.\n"
            "- Drop information that the new messages make clearly obsolete or "
            "superseded.\n"
            "- NEVER invent or infer information not present in the previous "
            "summary or the new messages.\n"
            "- NEVER reinterpret or change the meaning of past decisions — only "
            "add, update, or remove based on what actually happened.\n"
            "- Keep proper names (people, places, projects) exactly as written.\n"
            "- Keep it concise (a few sentences) while maintaining continuity "
            "with the previous summary's tone and content.\n\n"
            + (f'Previous summary: "{previous_summary}"\n\n' if previous_summary
               else "There is no previous summary yet.\n\n")
            + f"New messages since the last summary:\n{transcript}\n\n"
            "Write the updated summary. Output ONLY the summary text, no preamble."
        )
        try:
            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300, task_type="memory",
            )
            new_summary = raw.strip()
            if not new_summary:
                raise ValueError("resumen vacío")
        except Exception as e:
            logger.error(f"Actualización de resumen de historial falló para {user_id}: {e}")
            return False

        self.set_pattern(user_id, "conversation_summary", {
            "summary": new_summary,
            "summarized_up_to_message_id": new_messages[-1][0],
        })
        return True

    def get_facts(self, user_id: str) -> list[str]:
        # Solo hechos VIGENTES (no superseded) → el recall/contexto nunca se contradice.
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT fact FROM user_facts WHERE user_id = ? AND superseded = 0 "
                "ORDER BY confidence DESC, updated_at DESC",
                (user_id,)
            ).fetchall()
        return [r[0] for r in rows]

    def get_facts_with_ids(self, user_id: str) -> list[dict]:
        """Como get_facts pero con id y categoría (solo vigentes), para borrar/superseder
        individualmente y para la reconciliación por categoría (world-model)."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, fact, category FROM user_facts WHERE user_id = ? AND superseded = 0 "
                "ORDER BY confidence DESC, updated_at DESC",
                (user_id,)
            ).fetchall()
        return [{"id": r[0], "fact": r[1], "category": r[2] or "other"} for r in rows]

    def mark_superseded(self, user_id: str, fact_id: int) -> bool:
        """Marca un hecho como superseded (reemplazado por uno más nuevo). NO lo borra:
        queda como historial recuperable y deja de aparecer en el contexto activo."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE user_facts SET superseded = 1 WHERE id = ? AND user_id = ?",
                (fact_id, user_id)
            )
        ok = cur.rowcount > 0
        if ok:
            logger.info(f"🗂️ Fact #{fact_id} marcado superseded (historial) para {user_id}")
        return ok

    def get_superseded_facts(self, user_id: str) -> list[str]:
        """Historial de hechos reemplazados (para explicar/auditar la auto-organización)."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT fact FROM user_facts WHERE user_id = ? AND superseded = 1 "
                "ORDER BY updated_at DESC",
                (user_id,)
            ).fetchall()
        return [r[0] for r in rows]

    def delete_fact(self, user_id: str, fact_id: int) -> bool:
        """Borra un fact concreto. Soberanía de datos: el usuario es dueño de su memoria."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM user_facts WHERE id = ? AND user_id = ?",
                (fact_id, user_id)
            )
        deleted = cur.rowcount > 0
        if deleted:
            logger.info(f"🗑️ Fact #{fact_id} borrado para {user_id}")
        return deleted

    def delete_all_facts(self, user_id: str) -> int:
        """Borra TODO lo que ClawLite sabe (facts) del usuario. Devuelve cuántos borró."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM user_facts WHERE user_id = ?", (user_id,))
        n = cur.rowcount
        logger.info(f"🗑️ {n} facts borrados para {user_id} (wipe total)")
        return n

    # ── GOALS ──────────────────────────────────────────────────────────────

    def add_goal(self, user_id: str, goal: str):
        """Auto-organización (dedup en escritura, memU-03): si ya existe un
        goal ACTIVO equivalente (misma forma normalizada), no se inserta una
        fila duplicada. Contrato explícito: solo participan filas con
        status EXACTAMENTE 'active' — NULL o cualquier otro valor queda
        fuera de la comparación (NULL = 'active' nunca es verdadero en
        SQL), nunca se asume su significado. Determinista, nunca fusiona
        dos goals distintos."""
        norm = self._normalize_text(goal)
        with sqlite3.connect(self.db_path) as conn:
            if norm:
                existing = conn.execute(
                    "SELECT goal FROM goals WHERE user_id = ? AND status = 'active'",
                    (user_id,)
                ).fetchall()
                if any(self._normalize_text(g) == norm for (g,) in existing):
                    logger.debug(f"🎯 Goal duplicado ignorado para {user_id}: {goal[:60]}")
                    return
            conn.execute(
                "INSERT INTO goals (user_id, goal) VALUES (?, ?)",
                (user_id, goal)
            )
        logger.debug(f"🎯 Goal added for {user_id}: {goal[:60]}")

    def get_active_goals(self, user_id: str) -> list[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT goal FROM goals WHERE user_id = ? AND status = 'active'",
                (user_id,)
            ).fetchall()
        return [r[0] for r in rows]

    # ── TASKS ──────────────────────────────────────────────────────────────

    def add_task(self, user_id: str, task: str, due_date: datetime = None):
        """Auto-organización (dedup en escritura, memU-03): si ya existe un
        task PENDIENTE equivalente (misma forma normalizada), no se inserta
        una fila duplicada — y NO se toca el due_date de la existente.
        Contrato explícito: solo participan filas con status EXACTAMENTE
        'pending' — NULL o cualquier otro valor queda fuera de la
        comparación, nunca se asume su significado. Determinista, nunca
        fusiona dos tasks distintas. Una tarea ya completada (status='done')
        no bloquea una nueva instancia del mismo texto."""
        norm = self._normalize_text(task)
        with sqlite3.connect(self.db_path) as conn:
            if norm:
                existing = conn.execute(
                    "SELECT task FROM tasks WHERE user_id = ? AND status = 'pending'",
                    (user_id,)
                ).fetchall()
                if any(self._normalize_text(t) == norm for (t,) in existing):
                    logger.debug(f"✅ Task duplicada ignorada para {user_id}: {task[:60]}")
                    return
            conn.execute(
                "INSERT INTO tasks (user_id, task, due_date) VALUES (?, ?, ?)",
                (user_id, task, due_date)
            )
        logger.debug(f"✅ Task added for {user_id}: {task[:60]}")

    def get_pending_tasks(self, user_id: str) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT id, task, due_date, created_at FROM tasks
                   WHERE user_id = ? AND status = 'pending'
                   ORDER BY due_date ASC NULLS LAST, created_at ASC""",
                (user_id,)
            ).fetchall()
        return [{"id": r[0], "task": r[1], "due_date": r[2], "created_at": r[3]} for r in rows]

    def get_stale_tasks(self, user_id: str, days: int = 3) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT id, task, created_at FROM tasks
                   WHERE user_id = ? AND status = 'pending' AND created_at < ?""",
                (user_id, cutoff)
            ).fetchall()
        return [{"id": r[0], "task": r[1], "created_at": r[2]} for r in rows]

    def complete_task(self, user_id: str, task_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ? AND user_id = ?",
                (datetime.now().isoformat(), task_id, user_id)
            )

    # ── INTERESTS ──────────────────────────────────────────────────────────

    def add_or_boost_interest(self, user_id: str, topic: str, boost: float = 0.5):
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id, weight FROM interests WHERE user_id = ? AND topic = ?",
                (user_id, topic)
            ).fetchone()
            if existing:
                new_weight = min(existing[1] + boost, 10.0)
                conn.execute(
                    "UPDATE interests SET weight = ?, last_seen = ? WHERE id = ?",
                    (new_weight, datetime.now().isoformat(), existing[0])
                )
            else:
                conn.execute(
                    "INSERT INTO interests (user_id, topic, weight) VALUES (?, ?, ?)",
                    (user_id, topic, 1.0)
                )

    # Vida media del decay de intereses, en días: cada INTEREST_DECAY_HALF_LIFE_DAYS
    # sin refuerzo (sin que add_or_boost_interest se llame de nuevo sobre ese
    # topic), el peso EFECTIVO de ordenamiento se reduce a la mitad. El
    # "weight" almacenado en la BD NUNCA se modifica por esto — es puramente
    # un cálculo de LECTURA para decidir el orden en get_top_interests().
    INTEREST_DECAY_HALF_LIFE_DAYS = 30.0

    @staticmethod
    def _effective_interest_weight(stored_weight: float, last_seen: str | None, now: datetime) -> float:
        """Peso EFECTIVO (solo para ordenar, nunca se guarda) — decay
        exponencial por vida media sobre stored_weight según los días
        transcurridos entre last_seen y `now` (pasado explícito, NUNCA
        datetime.now() interno — mismo instante para todo el ranking,
        testeable sin mockear el reloj). Contrato ante last_seen ausente o
        no parseable: decay máximo (0.0) — un dato cuya antigüedad no
        podemos verificar no debe competir en pie de igualdad con uno de
        antigüedad confirmada (el esquema real tiene DEFAULT
        CURRENT_TIMESTAMP, verificado — este caso es defensivo, no
        esperado en operación normal). Reversible: en cuanto se refuerce
        de nuevo, last_seen se actualiza y vuelve a rankear fresco."""
        if not last_seen:
            return 0.0
        try:
            seen_at = datetime.fromisoformat(last_seen)
        except ValueError:
            return 0.0
        days_elapsed = max((now - seen_at).total_seconds() / 86400, 0.0)
        return stored_weight * (0.5 ** (days_elapsed / DeepMemory.INTEREST_DECAY_HALF_LIFE_DAYS))

    def get_top_interests(self, user_id: str, limit: int = 5) -> list[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT topic, weight, last_seen FROM interests WHERE user_id = ?",
                (user_id,)
            ).fetchall()
        now = datetime.now()

        # Desempate explícito de 4 niveles, aprovechando que sort() es
        # ESTABLE: se ordena del criterio MENOS significativo al MÁS
        # significativo — cada pasada preserva el orden relativo de la
        # anterior en los empates, así que el último criterio aplicado
        # (effective_weight) termina dominando, con cada empate cayendo
        # correctamente al siguiente nivel. effective_weight se calcula UNA
        # sola vez por fila y se reutiliza en la última pasada.
        scored = [
            (topic, weight, last_seen, DeepMemory._effective_interest_weight(weight, last_seen, now))
            for topic, weight, last_seen in rows
        ]
        ranked = sorted(scored, key=lambda r: r[0])              # 4. topic ASC
        ranked.sort(key=lambda r: r[2] or "", reverse=True)      # 3. last_seen DESC
        ranked.sort(key=lambda r: r[1], reverse=True)            # 2. stored_weight DESC
        ranked.sort(key=lambda r: r[3], reverse=True)            # 1. effective_weight DESC

        return [r[0] for r in ranked[:limit]]

    # ── PATTERNS ───────────────────────────────────────────────────────────

    def set_pattern(self, user_id: str, pattern: str, data: dict):
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id FROM patterns WHERE user_id = ? AND pattern = ?",
                (user_id, pattern)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE patterns SET data = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(data), datetime.now().isoformat(), existing[0])
                )
            else:
                conn.execute(
                    "INSERT INTO patterns (user_id, pattern, data) VALUES (?, ?, ?)",
                    (user_id, pattern, json.dumps(data))
                )

    def get_pattern(self, user_id: str, pattern: str) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT data FROM patterns WHERE user_id = ? AND pattern = ?",
                (user_id, pattern)
            ).fetchone()
        return json.loads(row[0]) if row else {}

    def clear_pattern(self, user_id: str, pattern: str):
        """Elimina un patrón específico del usuario."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM patterns WHERE user_id = ? AND pattern = ?",
                (user_id, pattern)
            )

    # Patterns que NO son memoria sino CONFIGURACIÓN/IDENTIDAD del usuario: deben
    # sobrevivir a /memory clear parcial (borrarlos relanzaría el onboarding o
    # reiniciaría preferencias). Allowlist estructural de claves de settings — no es
    # una heurística por idioma; es la separación memoria-vs-config. Añadir un setting
    # persistente nuevo en la tabla patterns implica añadir su clave aquí.
    #   • onboarding_complete — flag de "ya onboardeado" (evita relanzar el wizard)
    #   • voice_mode          — preferencia de voz on/off
    #   • onboarding_profile  — snapshot del seed de identidad (nombre/uso/intereses)
    #                           para re-plantarlo tras un clear parcial (reseed)
    _CONFIG_PATTERNS = ("onboarding_complete", "voice_mode", "onboarding_profile")

    def clear_user(self, user_id: str, full: bool = False):
        """
        Borra la memoria del usuario. Dos alcances:
          • full=False (/memory clear): borra lo APRENDIDO — facts, goals, tasks,
            interests y los patterns de memoria/estado transitorio — pero PRESERVA los
            patterns de config/identidad (_CONFIG_PATTERNS). El seed de identidad se
            re-planta aparte (reseed_from_onboarding) para que la IA siga conociendo lo
            básico del usuario sin relanzar el wizard.
          • full=True (reset total / botón 'borrar todo'): borra TODO, incluidos los
            patterns de config/identidad → la próxima vez el wizard re-onboarda.
        NO toca reminders en ningún modo (alarmas programadas que el usuario espera
        conservar y que el diálogo de borrado no menciona)."""
        with sqlite3.connect(self.db_path) as conn:
            for table in ("user_facts", "goals", "tasks", "interests"):
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            if full:
                conn.execute("DELETE FROM patterns WHERE user_id = ?", (user_id,))
            else:
                placeholders = ",".join("?" * len(self._CONFIG_PATTERNS))
                conn.execute(
                    f"DELETE FROM patterns WHERE user_id = ? AND pattern NOT IN ({placeholders})",
                    (user_id, *self._CONFIG_PATTERNS),
                )
        scope = "TODO incl. config" if full else "memoria; config preservada"
        logger.info(f"🗑️  DeepMemory borrada para {user_id} ({scope})")

    def reseed_from_onboarding(self, user_id: str):
        """
        Re-planta el seed de identidad (nombre, intereses) tras un /memory clear
        parcial, desde el snapshot 'onboarding_profile' guardado al completar el wizard.
        Así la IA sigue conociendo lo básico y reconstruye la memoria aprendida encima,
        sin re-onboardear. No-op si no hay snapshot (usuario onboardeado antes de que el
        snapshot existiera: re-onboarda una vez con 'borrar todo' para generarlo).

        El caso de uso del asistente NO se replanta como hecho: es config (vive en el
        campo 'use_case' del snapshot, no en 'facts')."""
        seed = self.get_pattern(user_id, "onboarding_profile")
        if not seed:
            return
        # Migración de formato legado (una sola vez): snapshots viejos guardaban el caso
        # de uso ("Uso principal: …") dentro de 'facts', como si fuera un hecho de vida.
        # Eso contaminaba build_context e inducía confabulación (responder p.ej.
        # "desarrollo de software" a "¿mi color favorito?"). Se depura del snapshot y se
        # reescribe limpio, así no vuelve a replantarse en futuros clears.
        facts = seed.get("facts", [])
        clean_facts = [f for f in facts if not f.startswith("Uso principal:")]
        if clean_facts != facts:
            seed = {**seed, "facts": clean_facts}
            self.set_pattern(user_id, "onboarding_profile", seed)
            logger.info(f"🧹 Snapshot onboarding migrado para {user_id}: 'Uso principal' deja de ser hecho de perfil")
        for fact in clean_facts:
            self.add_fact(user_id, fact)
        for interest in seed.get("interests", []):
            self.add_or_boost_interest(user_id, interest, boost=2.0)
        logger.info(f"🌱 Seed de identidad re-plantado para {user_id} tras clear parcial")

    # ── REMINDERS ──────────────────────────────────────────────────────────

    # Reglas de recurrencia soportadas. Vacío = recordatorio único.
    VALID_RECURRENCE = {"", "daily", "weekly", "monthly"}

    def add_reminder(
        self,
        user_id: str,
        message: str,
        remind_at: datetime,
        recurrence: str = "",
    ) -> int:
        """
        Crea un recordatorio. `recurrence` vacío = único; daily/weekly/monthly =
        recurrente (se reprograma solo al dispararse). No se valida aquí que la
        fecha sea futura: de eso se encarga quien extrae (ReminderTool), que tiene
        el contexto para decidir; el store solo persiste.
        """
        if recurrence not in self.VALID_RECURRENCE:
            recurrence = ""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO reminders (user_id, message, remind_at, recurrence) VALUES (?, ?, ?, ?)",
                (user_id, message, remind_at.isoformat(), recurrence)
            )
            reminder_id = cursor.lastrowid
        tag = f" (recurrente: {recurrence})" if recurrence else ""
        logger.info(f"⏰ Reminder #{reminder_id} set for {user_id} at {remind_at}{tag}")
        return reminder_id

    def get_all_due_reminders(self) -> list[dict]:
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT id, user_id, message, remind_at, recurrence FROM reminders
                   WHERE status = 'pending' AND remind_at <= ?
                   ORDER BY remind_at ASC""",
                (now,)
            ).fetchall()
        return [
            {"id": r[0], "user_id": r[1], "message": r[2], "remind_at": r[3], "recurrence": r[4] or ""}
            for r in rows
        ]

    def resolve_due_reminder(self, reminder_id: int):
        """
        Resuelve un recordatorio que acaba de dispararse. Si es único, lo marca
        'sent'. Si es recurrente, calcula la próxima ocurrencia y reprograma el
        MISMO registro a esa fecha (sigue 'pending'), de modo que vuelva a sonar.
        Punto único de verdad de "qué pasa tras disparar" — el trigger no decide.
        """
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT remind_at, recurrence FROM reminders WHERE id = ?",
                (reminder_id,)
            ).fetchone()
            if not row:
                return
            remind_at_str, recurrence = row[0], (row[1] or "")

            if not recurrence:
                conn.execute(
                    "UPDATE reminders SET status = 'sent' WHERE id = ?", (reminder_id,)
                )
                return

            # Recurrente: avanzar la fecha hasta la próxima ocurrencia futura.
            try:
                base = datetime.fromisoformat(remind_at_str)
            except Exception:
                conn.execute(
                    "UPDATE reminders SET status = 'sent' WHERE id = ?", (reminder_id,)
                )
                return

            nxt = self._advance(base, recurrence)
            # Si el sistema estuvo apagado y se saltaron varias ocurrencias,
            # avanzar hasta superar 'ahora' para no disparar en bucle.
            now = datetime.now()
            while nxt <= now:
                nxt = self._advance(nxt, recurrence)

            conn.execute(
                "UPDATE reminders SET remind_at = ? WHERE id = ?",
                (nxt.isoformat(), reminder_id)
            )

    @staticmethod
    def _advance(dt: datetime, recurrence: str) -> datetime:
        """Avanza una fecha según la regla. Mensual = +1 mes preservando el día
        en lo posible (cae al último día válido si el mes es más corto)."""
        from datetime import timedelta
        if recurrence == "daily":
            return dt + timedelta(days=1)
        if recurrence == "weekly":
            return dt + timedelta(weeks=1)
        if recurrence == "monthly":
            month = dt.month + 1
            year = dt.year + (1 if month > 12 else 0)
            month = 1 if month > 12 else month
            # Ajuste de día para meses cortos (ej. 31 ene → 28/29 feb).
            day = dt.day
            while day > 0:
                try:
                    return dt.replace(year=year, month=month, day=day)
                except ValueError:
                    day -= 1
        return dt

    def mark_reminder_sent(self, reminder_id: int):
        """Compat: marca 'sent' sin lógica de recurrencia. Conservado para no
        romper llamadores antiguos; el flujo nuevo usa resolve_due_reminder."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE reminders SET status = 'sent' WHERE id = ?",
                (reminder_id,)
            )

    def get_pending_reminders(self, user_id: str) -> list[dict]:
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT id, message, remind_at, recurrence FROM reminders
                   WHERE user_id = ? AND status = 'pending' AND remind_at > ?
                   ORDER BY remind_at ASC""",
                (user_id, now)
            ).fetchall()
        return [
            {"id": r[0], "message": r[1], "remind_at": r[2], "recurrence": r[3] or ""}
            for r in rows
        ]

    def cancel_reminder(self, reminder_id: int, user_id: str) -> bool:
        """Cancela un recordatorio del usuario (soberanía). Funciona en pending."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE reminders SET status = 'cancelled' WHERE id = ? AND user_id = ? AND status = 'pending'",
                (reminder_id, user_id)
            )
        ok = cur.rowcount > 0
        if ok:
            logger.info(f"🚫 Reminder #{reminder_id} cancelado por {user_id}")
        return ok


class UserProfile:

    def __init__(self, deep_memory: DeepMemory):
        self.memory = deep_memory

    def set_language_sample(self, user_id: str, message: str):
        """
        Guarda una muestra del último mensaje real del usuario como EVIDENCIA de
        su idioma. Los mensajes proactivos (triggers) corren sin un mensaje del
        usuario presente, así que el modelo no tiene en qué basarse para el idioma
        y por defecto responde en inglés. Darle una muestra real de cómo escribe
        el usuario es más robusto que cualquier detección heurística por idioma:
        el modelo iguala el idioma del ejemplo, que es algo que hace bien.
        Solo guarda mensajes con sustancia (evita ruido de una palabra).
        """
        clean = (message or "").strip()
        if len(clean) >= 8:
            self.memory.set_pattern(user_id, "language_sample", {"text": clean[:200]})

    def get_language_sample(self, user_id: str) -> str:
        sample = self.memory.get_pattern(user_id, "language_sample") or {}
        return sample.get("text", "")

    # Presupuesto de caracteres para el resumen de historial dentro del
    # contexto — evita que build_context() crezca sin límite si el resumen
    # se expande lentamente a lo largo de meses. Mismo espíritu que los
    # truncados ya existentes (facts[:5], goals[:3]) en esta función.
    HISTORY_SUMMARY_MAX_CHARS_IN_CONTEXT = 500

    def build_context(self, user_id: str) -> str:
        facts = self.memory.get_facts(user_id)
        goals = self.memory.get_active_goals(user_id)
        tasks = self.memory.get_pending_tasks(user_id)
        interests = self.memory.get_top_interests(user_id)
        lang_sample = self.get_language_sample(user_id)
        history_summary = self.memory.get_pattern(user_id, "conversation_summary").get("summary", "")

        if not any([facts, goals, tasks, interests, lang_sample, history_summary]):
            return ""

        lines = ["[User profile — use this context naturally, never mention you have it]\n"]
        if facts:
            lines.append("Known facts: " + " | ".join(facts[:5]))
        if goals:
            lines.append("Active goals: " + " | ".join(goals[:3]))
        if tasks:
            lines.append("Pending tasks: " + " | ".join(t["task"] for t in tasks[:5]))
        if interests:
            lines.append(
                "Topics the USER personally enjoys discussing (NOT a description "
                "of yourself — never use these to describe your own nature, "
                "architecture or identity): " + ", ".join(interests)
            )
        if history_summary:
            truncated = history_summary[:self.HISTORY_SUMMARY_MAX_CHARS_IN_CONTEXT]
            if len(history_summary) > self.HISTORY_SUMMARY_MAX_CHARS_IN_CONTEXT:
                last_space = truncated.rfind(" ")
                if last_space > 0:
                    truncated = truncated[:last_space]
                truncated += "…"
            lines.append(
                "Summary of earlier conversation (before the recent messages "
                "shown separately): " + truncated
            )
        # NOTA: deliberadamente NO se incluye el language_sample como instrucción de
        # idioma. El idioma de la respuesta lo decide SOLO el mensaje actual del
        # usuario (vía LANGUAGE_RULE), no una muestra histórica del perfil. Antes,
        # este bloque ordenaba "responde SIEMPRE en el idioma del sample", lo que
        # contradecía a LANGUAGE_RULE: con perfil en español y mensaje en alemán, el
        # modelo recibía dos mandatos opuestos y respondía en el idioma equivocado.
        # El sample sigue capturándose para otros usos, pero no impone idioma aquí.

        return "\n".join(lines)

    def get_summary(self, user_id: str) -> str:
        facts = self.memory.get_facts(user_id)
        goals = self.memory.get_active_goals(user_id)
        tasks = self.memory.get_pending_tasks(user_id)
        interests = self.memory.get_top_interests(user_id)
        reminders = self.memory.get_pending_reminders(user_id)

        parts = []
        if facts:
            parts.append("📋 *Lo que sé de ti:*\n" + "\n".join(f"• {f}" for f in facts[:5]))
        if goals:
            parts.append("🎯 *Tus objetivos:*\n" + "\n".join(f"• {g}" for g in goals))
        if tasks:
            parts.append("✅ *Tareas pendientes:*\n" + "\n".join(f"• {t['task']}" for t in tasks[:5]))
        if interests:
            parts.append(f"💡 *Tus intereses:* {', '.join(interests)}")
        if reminders:
            parts.append("⏰ *Recordatorios:*\n" + "\n".join(
                f"• {r['message']} — {r['remind_at'][:16].replace('T', ' ')}"
                for r in reminders[:5]
            ))

        return "\n\n".join(parts) if parts else "Aún no tengo suficiente información sobre ti."
