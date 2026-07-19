"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

memory/store.py — Persistencia local con SQLite + RAG ligero
Sin vector DB externo. Corre 100% local.
"""

import json
import sqlite3
import numpy as np
from datetime import datetime
from pathlib import Path
from loguru import logger
from clawlite.memory.embeddings import EmbeddingEngine
from clawlite.sandbox.guard import redact_secrets


class MemoryStore:
    def __init__(self, db_path: str, embedding_engine: EmbeddingEngine):
        self.db_path = db_path
        self.embeddings = embedding_engine
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info(f"💾 MemoryStore iniciado en: {db_path}")

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    embedding   TEXT,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS facts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT NOT NULL,
                    fact        TEXT NOT NULL,
                    embedding   TEXT,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);
                CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id);
            """)

            # Migración idempotente (patrón PRAGMA de profile.py): columna 'kind'
            # — procedencia del mensaje ('chat' | 'research'). Habilita el saneado
            # determinista del historial conversacional (bug de fabricación de
            # insignias, 6 jul): el sistema pasa a SABER qué respuestas provienen
            # de investigación verificada en vez de inferirlo del estilo del texto.
            # Filas históricas quedan 'chat' — seguro: en el peor caso un research
            # viejo se trata como charla (comportamiento actual). Nunca al revés.
            mcols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
            if "kind" not in mcols:
                conn.execute("ALTER TABLE messages ADD COLUMN kind TEXT DEFAULT 'chat'")

    def save_message(self, user_id: str, role: str, content: str, kind: str = "chat"):
        # Credential filtering por defecto: se redactan secretos (API keys, tokens,
        # claves privadas) ANTES de persistir, para que NUNCA queden en claro en la DB
        # ni reaparezcan en el contexto recuperado. El turno en vivo conserva el texto
        # original (el modelo puede actuar sobre él en el momento); solo el almacenado
        # se redacta. Determinista, por formato, agnóstico de idioma.
        content = redact_secrets(content)

        # Idempotencia ante duplicado inmediato: si el último mensaje guardado para
        # este usuario es idéntico (mismo rol y contenido), no se reinserta. Permite
        # guardar el turno del usuario una vez al inicio del flujo sin que los
        # handlers que aún lo guarden creen duplicados. Sin tocar cada handler.
        with sqlite3.connect(self.db_path) as conn:
            last = conn.execute(
                "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (str(user_id),)
            ).fetchone()
            if last and last[0] == role and last[1] == content:
                return  # duplicado inmediato — ya está guardado
            embedding = self.embeddings.encode(content)
            conn.execute(
                "INSERT INTO messages (user_id, role, content, embedding, kind) VALUES (?, ?, ?, ?, ?)",
                (str(user_id), role, content, json.dumps(embedding), kind)
            )

    def get_recent(self, user_id: str, limit: int = 10) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT role, content, kind FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (str(user_id), limit)
            ).fetchall()
        return [{"role": r[0], "content": r[1], "kind": r[2]} for r in reversed(rows)]

    def get_recent_excluding_current(
        self, user_id: str, current_message: str, limit: int = 10
    ) -> list[dict]:
        """
        Historial reciente EXCLUYENDO el turno actual del usuario, de forma robusta.

        Agent.handle() guarda el mensaje del usuario ANTES de leer el historial (fix
        del "vacío en saludos"), así que en el flujo normal el último elemento de
        get_recent() ES el turno actual. Pero eso es una convención del PUNTO DE USO
        (el orden de llamadas en core.py), no una garantía del método — si algún
        caller futuro llama en otro orden, o corre en paralelo, un `recent[:-1]` a
        ciegas podría descartar el mensaje equivocado y corromper el contexto en
        silencio.

        Este método VERIFICA antes de excluir: solo quita el último elemento si de
        verdad coincide (mismo rol y contenido, tal como quedó persistido — pasado
        por redact_secrets igual que save_message) con el turno actual. Si no
        coincide, no descarta nada — degrada seguro (se conserva el historial
        completo) en vez de tirar un mensaje real por una suposición de posición.
        """
        recent = self.get_recent(user_id, limit=limit + 1)
        if not recent:
            return []
        expected = redact_secrets(current_message)
        last = recent[-1]
        if last.get("role") == "user" and last.get("content") == expected:
            return recent[:-1][-limit:]
        return recent[-limit:]

    def recall_similar(self, user_id: str, query: str, top_k: int = 5) -> list[str]:
        """
        Búsqueda semántica pura (compatibilidad). Internamente usa recall_hybrid
        sin señales extra, para no romper a los llamadores existentes.
        """
        results = self.recall_hybrid(user_id, query, top_k=top_k)
        return [r["content"] for r in results]

    def recall_hybrid(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
        entities: list[str] | None = None,
        recency_weight: float = 0.15,
        entity_weight: float = 0.20,
        candidate_limit: int = 500,
    ) -> list[dict]:
        """
        Búsqueda híbrida robusta. Combina tres señales:
          • Semántica  — similitud coseno del embedding (señal principal).
          • Recencia   — un mensaje reciente pesa más (decaimiento suave).
          • Entidad    — si el mensaje menciona una entidad relevante del grafo, sube.

        Escala mejor que cargar todo: pre-filtra a los `candidate_limit` mensajes
        más recientes en SQL (con índice) antes de calcular similitud en Python.
        Para historiales enormes esto evita traer decenas de miles de filas.

        Devuelve list[dict] con content, score y desglose, por si el llamador
        quiere inspeccionar por qué algo fue recuperado (transparencia).
        """
        query_vec = np.array(self.embeddings.encode(query))
        q_norm = np.linalg.norm(query_vec) + 1e-8
        entities = [e.lower() for e in (entities or [])]

        with sqlite3.connect(self.db_path) as conn:
            # Pre-filtrado por recencia en SQL: solo los N más recientes.
            # created_at + id como desempate. El índice idx_messages_user ayuda.
            rows = conn.execute(
                """SELECT content, embedding, created_at
                   FROM messages
                   WHERE user_id = ? AND embedding IS NOT NULL
                   ORDER BY id DESC
                   LIMIT ?""",
                (str(user_id), candidate_limit)
            ).fetchall()

        if not rows:
            return []

        # Para recencia: el más nuevo (índice 0) = peso 1.0, decae hacia el más viejo.
        n = len(rows)
        scored = []
        for idx, (content, emb_json, created_at) in enumerate(rows):
            try:
                emb = np.array(json.loads(emb_json))
            except Exception:
                continue
            semantic = float(np.dot(query_vec, emb) / (q_norm * (np.linalg.norm(emb) + 1e-8)))

            # Recencia: decaimiento lineal por posición (0 = más reciente)
            recency = 1.0 - (idx / n) if n > 1 else 1.0

            # Entidad: boost si el contenido menciona alguna entidad relevante
            entity_hit = 0.0
            if entities:
                c_low = content.lower()
                if any(e in c_low for e in entities):
                    entity_hit = 1.0

            final = (
                semantic
                + recency_weight * recency
                + entity_weight * entity_hit
            )
            scored.append({
                "content": content,
                "score": final,
                "semantic": semantic,
                "recency": recency,
                "entity_hit": entity_hit,
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def clear_user(self, user_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE user_id = ?", (str(user_id),))
            conn.execute("DELETE FROM facts WHERE user_id = ?", (str(user_id),))
        logger.info(f"🗑️  Memoria borrada para user {user_id}")
