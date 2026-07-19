"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agents/skill_store.py — Self-Improving Loop
Cada skill guarda los mensajes reales del usuario que lo activaron,
no solo la abstracción del LLM. Esto preserva el idioma y vocabulario real.
"""

import json
import sqlite3
import re
from datetime import datetime
from loguru import logger
from clawlite.llm.client import llm
from clawlite.llm.json_parser import extract_json


SKILL_EXTRACT_PROMPT = """Analyze this completed task and extract a reusable skill pattern.

Task type: {task_type}
User message: {user_message}
Agent response: {response}

Return ONLY a JSON object:
{{
  "trigger": "pattern that would activate this skill (2-5 words)",
  "approach": "how this was solved successfully (1-2 sentences)",
  "outcome": "what made this response successful (1 sentence)",
  "task_type": "{task_type}"
}}

If this task is too specific to generalize, return: {{"skip": true}}
"""


class SkillStore:
    """
    Almacena skills aprendidos. Cada skill guarda:
    - El patrón abstracto (trigger, approach, outcome)
    - Los mensajes ORIGINALES del usuario que lo activaron (examples)
    - El embedding del trigger para agrupación semántica

    Los examples son la clave: cuando un skill se promueve a workflow,
    el workflow se entrena con los mensajes reales del usuario, no
    con la traducción del LLM.
    """

    def __init__(self, db_path: str, embedding_engine=None):
        self.db_path = db_path
        self.embeddings = embedding_engine
        self._init_schema()
        self._migrate_schema()

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS skills (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id           TEXT,
                    task_type         TEXT NOT NULL,
                    trigger           TEXT NOT NULL,
                    trigger_embedding BLOB,
                    approach          TEXT NOT NULL,
                    outcome           TEXT NOT NULL,
                    examples          TEXT,
                    uses              INTEGER DEFAULT 0,
                    success_rate      REAL DEFAULT 1.0,
                    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_used         DATETIME
                );

                CREATE INDEX IF NOT EXISTS idx_skills_type
                    ON skills(task_type, user_id);
            """)

    def _migrate_schema(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(skills)").fetchall()]
                if "trigger_embedding" not in cols:
                    conn.execute("ALTER TABLE skills ADD COLUMN trigger_embedding BLOB")
                    logger.info("🔧 Migrated skills table: added trigger_embedding")
                if "examples" not in cols:
                    conn.execute("ALTER TABLE skills ADD COLUMN examples TEXT")
                    logger.info("🔧 Migrated skills table: added examples")
        except Exception as e:
            logger.debug(f"Schema migration skipped: {e}")

    def set_embedding_engine(self, embedding_engine):
        self.embeddings = embedding_engine

    async def learn(
        self,
        user_id: str,
        task_type: str,
        user_message: str,
        response: str,
    ):
        """Extrae y guarda un skill de una tarea exitosa."""
        try:
            prompt = SKILL_EXTRACT_PROMPT.format(
                task_type=task_type,
                user_message=user_message[:300],
                response=response[:500],
            )

            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                structured=True,
            )

            data = extract_json(raw, expect="object")
            if not data:
                return


            if data.get("skip"):
                return

            self._save_skill(user_id, data, user_message)
            logger.debug(f"💡 Skill learned [{task_type}]: {data.get('trigger', '')}")

        except Exception as e:
            logger.debug(f"Skill extraction skipped: {e}")

    def _compute_embedding(self, text: str) -> bytes | None:
        if not self.embeddings:
            return None
        try:
            vec = self.embeddings.encode(text)
            return json.dumps(vec).encode("utf-8")
        except Exception as e:
            logger.debug(f"Embedding computation failed: {e}")
            return None

    def _deserialize_embedding(self, blob: bytes):
        """
        Lee un embedding guardado (JSON, bytes utf-8). Sin fallback a pickle:
        Expediente 6 del barrido de seguridad confirmó 0 registros legacy en
        la base real (125/125 en JSON) y lo removió por ser superficie de
        ataque innecesaria (pickle.loads sobre datos no verificados). Si una
        migración futura desde una versión muy vieja necesitara tolerar
        pickle de nuevo, NO reintroducir el fallback silencioso -- hacer una
        migración explícita, única, que reescriba esos registros a JSON.
        """
        return json.loads(blob.decode("utf-8"))

    def _save_skill(self, user_id: str, data: dict, user_message: str):
        """
        Guarda o actualiza skill agrupando por similitud semántica del mensaje original.
        Cuando se agrupa, añade el mensaje a examples para entrenar mejor el workflow después.
        """
        trigger = data.get("trigger", "")
        if not trigger:
            return

        # Embedding basado en el mensaje ORIGINAL del usuario, no en el trigger abstracto
        # Esto garantiza que la agrupación sea semánticamente coherente con el idioma del usuario
        embedding_blob = self._compute_embedding(user_message)

        similar = self._find_semantically_similar(user_id, embedding_blob, threshold=0.75)

        with sqlite3.connect(self.db_path) as conn:
            if similar:
                # Actualizar: incrementar uses y añadir el mensaje a examples
                existing_examples = self._get_examples(similar["id"])
                if user_message not in existing_examples:
                    existing_examples.append(user_message)
                    # Mantener máximo 10 ejemplos
                    existing_examples = existing_examples[-10:]

                conn.execute(
                    "UPDATE skills SET uses = uses + 1, last_used = ?, examples = ? WHERE id = ?",
                    (datetime.now().isoformat(), json.dumps(existing_examples), similar["id"])
                )
                logger.debug(f"   ↳ Grouped with skill #{similar['id']} (sim: {similar['similarity']:.2f}, examples: {len(existing_examples)})")
            else:
                conn.execute(
                    """INSERT INTO skills
                       (user_id, task_type, trigger, trigger_embedding, approach, outcome,
                        examples, uses, last_used)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (
                        user_id,
                        data.get("task_type", "general"),
                        trigger,
                        embedding_blob,
                        data.get("approach", ""),
                        data.get("outcome", ""),
                        json.dumps([user_message]),
                        datetime.now().isoformat(),
                    )
                )

    def _get_examples(self, skill_id: int) -> list[str]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT examples FROM skills WHERE id = ?",
                    (skill_id,)
                ).fetchone()
            if row and row[0]:
                return json.loads(row[0])
        except Exception:
            pass
        return []

    def _find_semantically_similar(
        self,
        user_id: str,
        query_embedding: bytes | None,
        threshold: float = 0.75,
    ) -> dict | None:
        if not query_embedding:
            return None

        try:
            import numpy as np
            query_vec = np.array(self._deserialize_embedding(query_embedding))

            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    """SELECT id, trigger, trigger_embedding
                       FROM skills
                       WHERE user_id = ? AND trigger_embedding IS NOT NULL""",
                    (user_id,)
                ).fetchall()

            best = None
            best_score = 0.0

            for row in rows:
                try:
                    skill_vec = np.array(self._deserialize_embedding(row[2]))
                    similarity = float(
                        np.dot(query_vec, skill_vec) /
                        (np.linalg.norm(query_vec) * np.linalg.norm(skill_vec) + 1e-8)
                    )
                    if similarity >= threshold and similarity > best_score:
                        best_score = similarity
                        best = {"id": row[0], "trigger": row[1], "similarity": similarity}
                except Exception:
                    continue

            return best

        except Exception as e:
            logger.debug(f"Semantic similarity search failed: {e}")
            return None

    def get_relevant_skills(
        self,
        user_id: str,
        task_type: str,
        limit: int = 3,
    ) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT id, trigger, approach, outcome, uses, success_rate
                   FROM skills
                   WHERE (user_id = ? OR user_id IS NULL)
                     AND task_type = ?
                   ORDER BY success_rate DESC, uses DESC
                   LIMIT ?""",
                (user_id, task_type, limit)
            ).fetchall()

        return [
            {
                "id": r[0],
                "trigger": r[1],
                "approach": r[2],
                "outcome": r[3],
                "uses": r[4],
                "success_rate": r[5],
            }
            for r in rows
        ]

    def get_recurring_skills(
        self,
        user_id: str,
        min_uses: int = 3,
        days_window: int = 14,
    ) -> list[dict]:
        """Devuelve skills recurrentes con sus examples reales del usuario."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days_window)).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT id, trigger, approach, outcome, uses, task_type, examples
                   FROM skills
                   WHERE (user_id = ? OR user_id IS NULL)
                     AND uses >= ?
                     AND last_used >= ?
                   ORDER BY uses DESC""",
                (user_id, min_uses, cutoff)
            ).fetchall()

        return [
            {
                "id": r[0],
                "trigger": r[1],
                "approach": r[2],
                "outcome": r[3],
                "uses": r[4],
                "task_type": r[5],
                "examples": json.loads(r[6]) if r[6] else [],
            }
            for r in rows
        ]

    def format_skills_context(self, skills: list[dict]) -> str:
        if not skills:
            return ""
        lines = ["[Learned approaches for similar tasks]\n"]
        for s in skills:
            lines.append(
                f"• Trigger: {s['trigger']}\n"
                f"  Approach: {s['approach']}\n"
                f"  What worked: {s['outcome']}"
            )
        return "\n".join(lines)

    def mark_skill_used(self, skill_id: int, success: bool = True):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE skills
                   SET uses = uses + 1,
                       last_used = ?,
                       success_rate = (success_rate * uses + ?) / (uses + 1)
                   WHERE id = ?""",
                (datetime.now().isoformat(), 1.0 if success else 0.0, skill_id)
            )
