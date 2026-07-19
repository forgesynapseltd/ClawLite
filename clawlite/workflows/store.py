"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

workflows/store.py — Persistencia y matching de workflows
El embedding del workflow se calcula a partir de los mensajes REALES del usuario,
no de una traducción abstracta del LLM. Esto garantiza match en su idioma.
"""

import json
import sqlite3
from datetime import datetime
from loguru import logger


# Threshold de similitud para considerar match
MATCH_THRESHOLD = 0.70


class WorkflowStore:
    """
    Almacena workflows ejecutables.
    Cada workflow guarda el embedding promediado de TODOS los ejemplos reales
    del usuario que llevaron a su creación — no del trigger abstracto.
    """

    def __init__(self, db_path: str, embedding_engine):
        self.db_path = db_path
        self.embeddings = embedding_engine
        self._init_schema()

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS workflows (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       TEXT,
                    name          TEXT NOT NULL,
                    trigger_text  TEXT NOT NULL,
                    trigger_embedding BLOB,
                    examples      TEXT,
                    steps         TEXT NOT NULL,
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    avg_duration  REAL DEFAULT 0,
                    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_used     DATETIME
                );

                CREATE INDEX IF NOT EXISTS idx_workflows_user
                    ON workflows(user_id);
            """)
            # Migración
            cols = [r[1] for r in conn.execute("PRAGMA table_info(workflows)").fetchall()]
            if "examples" not in cols:
                conn.execute("ALTER TABLE workflows ADD COLUMN examples TEXT")

    def save_workflow(
        self,
        user_id: str,
        name: str,
        trigger_text: str,
        steps: list[dict],
        examples: list[str] | None = None,
    ) -> int | None:
        """
        Guarda un workflow.
        El embedding se calcula como el promedio de los embeddings de cada example
        del usuario — esto garantiza match en su idioma y vocabulario real.
        """
        try:
            import numpy as np

            # Construir embedding desde los examples reales del usuario
            if examples and len(examples) > 0:
                vecs = [np.array(self.embeddings.encode(ex)) for ex in examples]
                avg_vec = np.mean(vecs, axis=0)
                embedding_blob = json.dumps(avg_vec.tolist())
                logger.debug(f"Workflow embedding from {len(examples)} real examples")
            else:
                # Fallback: usar el trigger_text si no hay examples
                vec = self.embeddings.encode(trigger_text)
                embedding_blob = json.dumps(vec)
                logger.debug("Workflow embedding from trigger_text (no examples)")

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    """INSERT INTO workflows
                       (user_id, name, trigger_text, trigger_embedding, examples, steps)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        name,
                        trigger_text,
                        embedding_blob,
                        json.dumps(examples or []),
                        json.dumps(steps),
                    )
                )
                workflow_id = cursor.lastrowid

            logger.info(f"💾 Workflow guardado [{name}]: {trigger_text[:60]}")
            return workflow_id

        except Exception as e:
            logger.error(f"❌ Error guardando workflow: {e}")
            return None

    def find_matching_workflow(self, user_id: str, message: str) -> dict | None:
        """
        Busca un workflow que matche el mensaje.
        El embedding del workflow ya viene del idioma del usuario,
        así que el match es directo.
        """
        try:
            import numpy as np
            query_vec = np.array(self.embeddings.encode(message))

            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    """SELECT id, name, trigger_text, trigger_embedding, steps,
                              success_count, failure_count
                       FROM workflows
                       WHERE (user_id = ? OR user_id IS NULL)
                         AND trigger_embedding IS NOT NULL""",
                    (user_id,)
                ).fetchall()

            if not rows:
                return None

            best = None
            best_score = 0.0

            for row in rows:
                try:
                    raw = row[3]
                    # JSON (str o bytes utf-8). Sin fallback a pickle: Expediente 6
                    # del barrido de seguridad confirmó 0 registros legacy en la base
                    # real y lo removió por ser superficie de ataque innecesaria
                    # (pickle.loads sobre datos no verificados). Si una migración
                    # futura necesitara tolerar pickle de nuevo, NO reintroducir el
                    # fallback silencioso -- hacer una migración explícita y única.
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    wf_vec = np.array(json.loads(raw))
                    similarity = float(
                        np.dot(query_vec, wf_vec) /
                        (np.linalg.norm(query_vec) * np.linalg.norm(wf_vec) + 1e-8)
                    )

                    success = row[5]
                    failure = row[6]
                    total = success + failure
                    success_rate = success / total if total > 0 else 1.0

                    adjusted_score = similarity * success_rate

                    if adjusted_score > best_score and similarity >= MATCH_THRESHOLD:
                        best_score = adjusted_score
                        best = {
                            "id": row[0],
                            "name": row[1],
                            "trigger_text": row[2],
                            "steps": json.loads(row[4]),
                            "similarity": similarity,
                            "success_rate": success_rate,
                        }
                except Exception:
                    continue

            return best

        except Exception as e:
            logger.error(f"❌ Error buscando workflow: {e}")
            return None

    def record_execution(self, workflow_id: int, success: bool, duration: float):
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT success_count, failure_count, avg_duration FROM workflows WHERE id = ?",
                    (workflow_id,)
                ).fetchone()
                if not row:
                    return

                success_count, failure_count, avg_duration = row
                total = success_count + failure_count
                new_avg = ((avg_duration * total) + duration) / (total + 1) if total > 0 else duration

                if success:
                    conn.execute(
                        """UPDATE workflows SET success_count = success_count + 1,
                           avg_duration = ?, last_used = ? WHERE id = ?""",
                        (new_avg, datetime.now().isoformat(), workflow_id)
                    )
                else:
                    conn.execute(
                        """UPDATE workflows SET failure_count = failure_count + 1,
                           avg_duration = ?, last_used = ? WHERE id = ?""",
                        (new_avg, datetime.now().isoformat(), workflow_id)
                    )
        except Exception as e:
            logger.debug(f"Error recording execution: {e}")

    def get_all_workflows(self, user_id: str) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT id, name, trigger_text, success_count, failure_count, avg_duration
                   FROM workflows
                   WHERE user_id = ? OR user_id IS NULL
                   ORDER BY success_count DESC""",
                (user_id,)
            ).fetchall()

        return [
            {
                "id": r[0], "name": r[1], "trigger": r[2],
                "success_count": r[3], "failure_count": r[4], "avg_duration": r[5],
            }
            for r in rows
        ]

    def delete_workflow(self, workflow_id: int) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
            return True
        except Exception:
            return False
