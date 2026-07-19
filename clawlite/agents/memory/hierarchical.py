"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agents/memory/hierarchical.py — Memoria jerárquica de 3 capas
Capa 1: Hechos (ya existe en profile.py)
Capa 2: Patrones de comportamiento
Capa 3: Grafo de conocimiento
"""

import json
import sqlite3
import re
from datetime import datetime
from loguru import logger
from clawlite.llm.client import llm
from clawlite.llm.json_parser import extract_json


PATTERN_EXTRACT_PROMPT = """Analyze this user interaction and extract behavioral patterns.

Message: {message}
Time: {time}
Day of week: {day}

Return ONLY JSON (empty {{}} if no pattern):
{{
  "pattern": "description of behavioral pattern",
  "context": "when this pattern applies"
}}

Examples of patterns:
- "Asks about AI news on weekday mornings"
- "Prefers bullet points over paragraphs"
- "Focuses on security topics after work hours"
"""

ENTITY_EXTRACT_PROMPT = """Extract entities and relationships from this text.

Text: {text}

Return ONLY JSON:
{{
  "entities": [
    {{"name": "entity name", "type": "person|company|project|concept|place"}}
  ],
  "relations": [
    {{"from": "entity1", "relation": "verb", "to": "entity2"}}
  ]
}}

Only extract clear, explicit relationships. Return empty arrays if nothing clear.
"""


class HierarchicalMemory:
    """
    Memoria de 3 capas que complementa la DeepMemory existente.
    Capa 2: Patrones de comportamiento detectados automáticamente.
    Capa 3: Grafo de conocimiento de entidades y relaciones.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS behavior_patterns (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT NOT NULL,
                    pattern     TEXT NOT NULL,
                    context     TEXT,
                    frequency   INTEGER DEFAULT 1,
                    last_seen   DATETIME DEFAULT CURRENT_TIMESTAMP,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS knowledge_nodes (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    entity  TEXT NOT NULL,
                    type    TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, entity)
                );

                CREATE TABLE IF NOT EXISTS knowledge_edges (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id   TEXT NOT NULL,
                    from_node INTEGER NOT NULL,
                    to_node   INTEGER NOT NULL,
                    relation  TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (from_node) REFERENCES knowledge_nodes(id),
                    FOREIGN KEY (to_node) REFERENCES knowledge_nodes(id)
                );

                CREATE INDEX IF NOT EXISTS idx_patterns_user
                    ON behavior_patterns(user_id);
                CREATE INDEX IF NOT EXISTS idx_nodes_user
                    ON knowledge_nodes(user_id);
            """)

    # ── CAPA 2: PATRONES ────────────────────────────────────────────────────

    async def detect_and_save_pattern(self, user_id: str, message: str):
        """Detecta patrones de comportamiento silenciosamente."""
        try:
            now = datetime.now()
            prompt = PATTERN_EXTRACT_PROMPT.format(
                message=message[:200],
                time=now.strftime("%H:%M"),
                day=now.strftime("%A"),
            )

            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=80,
                structured=True,
            )

            data = extract_json(raw, expect="object")
            if not data:
                return

            if not data.get("pattern"):
                return

            self._save_or_update_pattern(user_id, data["pattern"], data.get("context", ""))

        except Exception as e:
            logger.debug(f"Pattern detection skipped: {e}")

    def _save_or_update_pattern(self, user_id: str, pattern: str, context: str):
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id, frequency FROM behavior_patterns WHERE user_id = ? AND pattern = ?",
                (user_id, pattern)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE behavior_patterns SET frequency = ?, last_seen = ? WHERE id = ?",
                    (existing[1] + 1, datetime.now().isoformat(), existing[0])
                )
            else:
                conn.execute(
                    "INSERT INTO behavior_patterns (user_id, pattern, context) VALUES (?, ?, ?)",
                    (user_id, pattern, context)
                )

    def get_top_patterns(self, user_id: str, limit: int = 5) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT pattern, context, frequency FROM behavior_patterns
                   WHERE user_id = ? ORDER BY frequency DESC LIMIT ?""",
                (user_id, limit)
            ).fetchall()
        return [{"pattern": r[0], "context": r[1], "frequency": r[2]} for r in rows]

    # ── CAPA 3: GRAFO DE CONOCIMIENTO ────────────────────────────────────────

    async def extract_and_save_entities(self, user_id: str, text: str):
        """Extrae entidades y relaciones del texto silenciosamente."""
        try:
            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": ENTITY_EXTRACT_PROMPT.format(text=text[:400])}],
                max_tokens=200,
                structured=True,
            )

            data = extract_json(raw, expect="object")
            if not data:
                return

            entities = data.get("entities", [])
            relations = data.get("relations", [])

            if not entities:
                return

            # Guardar nodos
            node_ids = {}
            for entity in entities:
                node_id = self._save_node(user_id, entity["name"], entity.get("type", "concept"))
                if node_id:
                    node_ids[entity["name"]] = node_id

            # Guardar relaciones
            for rel in relations:
                from_id = node_ids.get(rel.get("from"))
                to_id = node_ids.get(rel.get("to"))
                if from_id and to_id:
                    self._save_edge(user_id, from_id, to_id, rel.get("relation", "relates_to"))

        except Exception as e:
            logger.debug(f"Entity extraction skipped: {e}")

    def _save_node(self, user_id: str, entity: str, entity_type: str) -> int | None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                existing = conn.execute(
                    "SELECT id FROM knowledge_nodes WHERE user_id = ? AND entity = ?",
                    (user_id, entity)
                ).fetchone()

                if existing:
                    return existing[0]

                cursor = conn.execute(
                    "INSERT INTO knowledge_nodes (user_id, entity, type) VALUES (?, ?, ?)",
                    (user_id, entity, entity_type)
                )
                return cursor.lastrowid
        except Exception:
            return None

    def _save_edge(self, user_id: str, from_id: int, to_id: int, relation: str):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO knowledge_edges
                       (user_id, from_node, to_node, relation) VALUES (?, ?, ?, ?)""",
                    (user_id, from_id, to_id, relation)
                )
        except Exception:
            pass

    def get_knowledge_context(self, user_id: str) -> str:
        """Devuelve el grafo de conocimiento como texto para el LLM."""
        with sqlite3.connect(self.db_path) as conn:
            edges = conn.execute(
                """SELECT n1.entity, e.relation, n2.entity
                   FROM knowledge_edges e
                   JOIN knowledge_nodes n1 ON e.from_node = n1.id
                   JOIN knowledge_nodes n2 ON e.to_node = n2.id
                   WHERE e.user_id = ?
                   LIMIT 20""",
                (user_id,)
            ).fetchall()

        if not edges:
            return ""

        lines = ["[Knowledge graph]\n"]
        for from_e, rel, to_e in edges:
            lines.append(f"• {from_e} → {rel} → {to_e}")

        return "\n".join(lines)

    def build_full_context(self, user_id: str) -> str:
        """Contexto completo de capas 2 y 3 para inyectar en el LLM."""
        parts = []

        patterns = self.get_top_patterns(user_id, limit=3)
        if patterns:
            pattern_lines = "\n".join(f"• {p['pattern']}" for p in patterns)
            parts.append(f"[Behavioral patterns]\n{pattern_lines}")

        graph = self.get_knowledge_context(user_id)
        if graph:
            parts.append(graph)

        return "\n\n".join(parts)

    def clear_user(self, user_id: str):
        """
        Borra las capas 2 y 3 del usuario: patrones de comportamiento y el grafo de
        conocimiento (nodos + aristas). Parte del wipe total de /memory clear; sin
        esto, entidades y patrones viejos seguirían alimentando el recall y el boost
        por entidad tras el borrado. Las aristas se borran antes que los nodos (FK)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM knowledge_edges WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM knowledge_nodes WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM behavior_patterns WHERE user_id = ?", (user_id,))
        logger.info(f"🗑️  HierarchicalMemory borrada para {user_id} (patrones + grafo)")
