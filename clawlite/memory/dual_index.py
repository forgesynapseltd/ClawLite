"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

memory/dual_index.py — Indexación dual
Capa 1: Embeddings rápidos (siempre)
Capa 2: Razonamiento LLM profundo (solo si la capa 1 no es suficiente)
"""

from loguru import logger
from clawlite.llm.client import llm
from clawlite.llm.json_parser import extract_json


EMBEDDING_THRESHOLD = 0.55


class DualIndex:
    """
    Búsqueda híbrida en memoria:
    1. Embeddings rápidos para queries factuales
    2. Razonamiento LLM cuando la query es compleja o los embeddings fallan
    """

    def __init__(self, memory_store, multimodal_memory):
        self.memory = memory_store
        self.multimodal = multimodal_memory
        # Acceso al grafo de conocimiento para el boost por entidad en la búsqueda
        # híbrida. Solo necesita el db_path que el memory_store ya tiene — sin
        # nuevas dependencias en main.py.
        try:
            from clawlite.agents.memory.knowledge_graph import KnowledgeGraph
            self.graph = KnowledgeGraph(memory_store.db_path)
        except Exception:
            self.graph = None

    def _entities_in_query(self, user_id: str, query: str) -> list[str]:
        """
        Cruza las entidades conocidas del grafo del usuario contra el texto de la
        query. Gratis (sin LLM): si la query menciona 'Juan' y Juan es una entidad
        del grafo, devuelve ['Juan'] para que recall_hybrid priorice lo ligado a él.
        """
        if not self.graph:
            return []
        try:
            known = self.graph.get_all_entities(user_id)
        except Exception:
            return []
        q_low = query.lower()
        hits = []
        for e in known:
            name = e.get("entity", "")
            if name and len(name) >= 3 and name.lower() in q_low:
                hits.append(name)
        return hits

    async def recall(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
    ) -> dict:
        """
        Búsqueda dual.
        Devuelve: {
          'messages': [...],
          'assets': [...],
          'used_deep_reasoning': bool,
          'reason': str
        }
        """
        # Capa 1 — búsqueda híbrida con boost por entidad.
        # Si la query menciona entidades conocidas del grafo (p.ej. "qué dijimos
        # de Juan"), recall_hybrid sube los mensajes ligados a ellas.
        query_entities = self._entities_in_query(user_id, query)
        hybrid = self.memory.recall_hybrid(
            user_id, query, top_k=top_k, entities=query_entities
        )
        messages_l1 = [r["content"] for r in hybrid]
        assets_l1 = self.multimodal.search_assets(user_id, query, top_k=top_k)

        if query_entities:
            logger.info(f"🕸️ Boost por entidad: {', '.join(query_entities)}")

        # Capa 2 se activa SOLO por señal objetiva, agnóstica de idioma: la capa 1
        # no trajo evidencia fuerte en NINGUNA modalidad. "Fuerte" = al menos un
        # resultado por encima del umbral. El score mide directamente lo que la lista
        # ANALYTICAL_KEYWORDS aproximaba mal (y solo en ES/EN): "¿hay un recuerdo que
        # de verdad encaje con la query?".
        top_message_score = hybrid[0]["score"] if hybrid else 0.0
        top_asset_score = assets_l1[0]["score"] if assets_l1 else 0.0

        weak_messages = len(messages_l1) < 2 or top_message_score < EMBEDDING_THRESHOLD
        weak_assets = (not assets_l1) or top_asset_score < EMBEDDING_THRESHOLD
        weak_results = weak_messages and weak_assets

        # Trazabilidad de la decisión (misma filosofía de transparencia que
        # recall_hybrid): deja ver por qué se activó o no la capa 2 — por score
        # objetivo y agnóstico de idioma, no por listas de palabras.
        logger.debug(
            f"🔬 DualIndex recall: msgs={len(messages_l1)} "
            f"top_msg={top_message_score:.2f} top_asset={top_asset_score:.2f} "
            f"→ {'capa 2' if weak_results else 'vía rápida'}"
        )

        if not weak_results:
            return {
                "messages": messages_l1,
                "assets": assets_l1[:top_k],
                "used_deep_reasoning": False,
                "reason": "embeddings_sufficient",
            }

        logger.info(f"🧠 DualIndex: activando capa 2 para query: {query[:60]}")

        filtered_messages, filtered_assets = await self._deep_reasoning(
            query=query,
            messages=messages_l1,
            assets=assets_l1,
        )

        return {
            "messages": filtered_messages,
            "assets": filtered_assets,
            "used_deep_reasoning": True,
            "reason": "weak_embeddings",
        }

    async def _deep_reasoning(
        self,
        query: str,
        messages: list[str],
        assets: list[dict],
    ) -> tuple[list[str], list[dict]]:
        if not messages and not assets:
            return [], []

        candidates_text = []
        for i, msg in enumerate(messages):
            candidates_text.append(f"[M{i}] Message: {msg[:300]}")
        for i, asset in enumerate(assets):
            candidates_text.append(
                f"[A{i}] {asset['asset_type'].capitalize()}: {asset['description'][:300]}"
            )

        if not candidates_text:
            return [], []

        candidates_str = "\n".join(candidates_text)

        prompt = f"""User query: {query}

Candidate memories:
{candidates_str}

Which candidates are truly relevant to answer this query?
Return ONLY a JSON with indices to keep:
{{"keep_messages": [0, 1], "keep_assets": [0]}}

Only include candidates that DIRECTLY help answer the query. Be strict — irrelevant matches confuse the response."""

        try:
            import json
            import re

            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                structured=True,
            )
            decision = extract_json(raw, expect="object")
            if not decision:
                return messages, assets

            keep_msgs = decision.get("keep_messages", [])
            keep_assets = decision.get("keep_assets", [])

            filtered_messages = [messages[i] for i in keep_msgs if i < len(messages)]
            filtered_assets = [assets[i] for i in keep_assets if i < len(assets)]

            return filtered_messages, filtered_assets

        except Exception as e:
            logger.debug(f"Deep reasoning failed, returning all: {e}")
            return messages, assets

    def format_recall_context(self, recall: dict) -> str:
        parts = []

        if recall["messages"]:
            parts.append("[Relevant past messages]\n" + "\n".join(
                f"• {m}" for m in recall["messages"]
            ))

        if recall["assets"]:
            asset_lines = []
            for a in recall["assets"]:
                icon = {"image": "🖼", "audio": "🎙", "document": "📄", "log": "📝"}.get(
                    a["asset_type"], "📌"
                )
                asset_lines.append(f"{icon} {a['description']}")
            parts.append("[Relevant memory assets]\n" + "\n".join(asset_lines))

        if recall["used_deep_reasoning"]:
            parts.append(f"_[Deep memory search used — {recall['reason']}]_")

        return "\n\n".join(parts)
