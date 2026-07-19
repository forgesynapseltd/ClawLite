"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agents/memory/knowledge_graph.py — Grafo de conocimiento
Entidades y relaciones extraídas de las conversaciones del usuario.
Capa 3 de la memoria jerárquica.
"""

import sqlite3
from loguru import logger


class KnowledgeGraph:
    """
    Acceso directo al grafo de conocimiento.
    La extracción de entidades vive en hierarchical.py.
    Este módulo provee consultas especializadas sobre el grafo.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_related_entities(self, user_id: str, entity: str, limit: int = 10) -> list[dict]:
        """Devuelve entidades relacionadas con una entidad dada."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    """SELECT n2.entity, e.relation, n2.type
                       FROM knowledge_edges e
                       JOIN knowledge_nodes n1 ON e.from_node = n1.id
                       JOIN knowledge_nodes n2 ON e.to_node = n2.id
                       WHERE e.user_id = ? AND n1.entity = ?
                       LIMIT ?""",
                    (user_id, entity, limit)
                ).fetchall()
            return [{"entity": r[0], "relation": r[1], "type": r[2]} for r in rows]
        except Exception as e:
            logger.debug(f"KnowledgeGraph query failed: {e}")
            return []

    def get_all_entities(self, user_id: str) -> list[dict]:
        """Devuelve todas las entidades conocidas del usuario."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    """SELECT entity, type FROM knowledge_nodes
                       WHERE user_id = ? ORDER BY created_at DESC""",
                    (user_id,)
                ).fetchall()
            return [{"entity": r[0], "type": r[1]} for r in rows]
        except Exception as e:
            logger.debug(f"KnowledgeGraph entities query failed: {e}")
            return []

    def get_graph_summary(self, user_id: str) -> str:
        """Resumen del grafo para mostrar al usuario."""
        entities = self.get_all_entities(user_id)
        if not entities:
            return "No hay entidades en el grafo de conocimiento."

        by_type: dict[str, list[str]] = {}
        for e in entities:
            t = e["type"]
            by_type.setdefault(t, []).append(e["entity"])

        lines = ["🕸️ *Grafo de conocimiento:*\n"]
        for entity_type, names in by_type.items():
            lines.append(f"*{entity_type.capitalize()}:* {', '.join(names[:5])}")

        return "\n".join(lines)
