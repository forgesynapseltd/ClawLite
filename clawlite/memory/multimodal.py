"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

memory/multimodal.py — Memoria multi-modal
Guarda imágenes, audios, documentos y logs con descripciones searchable.
Todo local, todo privado.
"""

import os
import json
import sqlite3
import shutil
import pickle
from datetime import datetime
from pathlib import Path
from loguru import logger
from clawlite.llm.client import llm


ASSETS_DIR = "./data/memory_assets"


class MultimodalMemory:
    """
    Memoria que recuerda imágenes, audios, documentos y logs.
    Cada asset tiene una descripción generada por LLM para búsqueda semántica.
    """

    def __init__(self, db_path: str, embedding_engine):
        """
        embedding_engine: instancia de EmbeddingEngine (motor único compartido).
        """
        self.db_path = db_path
        self.embeddings = embedding_engine
        self._init_schema()
        os.makedirs(ASSETS_DIR, exist_ok=True)

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS memory_assets (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       TEXT NOT NULL,
                    asset_type    TEXT NOT NULL,
                    file_path     TEXT,
                    description   TEXT NOT NULL,
                    metadata      TEXT,
                    embedding     BLOB,
                    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_accessed DATETIME
                );

                CREATE INDEX IF NOT EXISTS idx_assets_user_type
                    ON memory_assets(user_id, asset_type);
            """)

    async def save_asset(
        self,
        user_id: str,
        asset_type: str,
        source_path: str | None,
        description: str,
        metadata: dict | None = None,
        compute_embedding: bool = True,
    ) -> int | None:
        """
        Guarda un asset en memoria.
        - source_path: ruta original (se copia a memory_assets si existe)
        - description: descripción semántica
        - asset_type: 'image' | 'audio' | 'document' | 'log'
        """
        try:
            stored_path = None

            if source_path and os.path.exists(source_path):
                user_dir = Path(ASSETS_DIR) / user_id / asset_type
                user_dir.mkdir(parents=True, exist_ok=True)

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                ext = Path(source_path).suffix
                stored_path = str(user_dir / f"{timestamp}{ext}")
                shutil.copy2(source_path, stored_path)

            embedding_json = None
            if compute_embedding and description:
                vec = self.embeddings.encode(description)
                embedding_json = json.dumps(vec)

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    """INSERT INTO memory_assets
                       (user_id, asset_type, file_path, description, metadata, embedding)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        asset_type,
                        stored_path,
                        description,
                        json.dumps(metadata) if metadata else None,
                        embedding_json,
                    )
                )
                asset_id = cursor.lastrowid

            logger.info(f"💾 Asset guardado [{asset_type}]: {description[:60]}")
            return asset_id

        except Exception as e:
            logger.error(f"❌ Error guardando asset: {e}")
            return None

    async def describe_and_save_image(
        self, user_id: str, image_path: str, context: str = ""
    ) -> int | None:
        """Genera descripción de imagen con LLM vision y la guarda."""
        try:
            import base64
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()

            description, _ = await llm.complete_vision(
                image_b64=b64,
                question="Describe this image briefly for memory purposes. Be specific about objects, people, text visible, and overall context. Max 2 sentences.",
                max_tokens=150,
            )

            metadata = {"context": context} if context else None
            return await self.save_asset(
                user_id=user_id,
                asset_type="image",
                source_path=image_path,
                description=description,
                metadata=metadata,
            )
        except Exception as e:
            logger.error(f"❌ Error describiendo imagen: {e}")
            return None

    async def save_audio_with_transcript(
        self, user_id: str, audio_path: str, transcript: str
    ) -> int | None:
        return await self.save_asset(
            user_id=user_id,
            asset_type="audio",
            source_path=audio_path,
            description=f"Audio message: {transcript}",
            metadata={"transcript": transcript},
        )

    async def save_document_summary(
        self, user_id: str, doc_path: str, filename: str, summary: str
    ) -> int | None:
        return await self.save_asset(
            user_id=user_id,
            asset_type="document",
            source_path=doc_path,
            description=f"Document '{filename}': {summary[:300]}",
            metadata={"filename": filename, "summary": summary[:1000]},
        )

    async def save_action_log(
        self, user_id: str, action: str, details: dict
    ) -> int | None:
        description = f"Action '{action}': {json.dumps(details)[:200]}"
        return await self.save_asset(
            user_id=user_id,
            asset_type="log",
            source_path=None,
            description=description,
            metadata={"action": action, **details},
        )

    def search_assets(
        self,
        user_id: str,
        query: str,
        asset_types: list[str] | None = None,
        top_k: int = 5,
    ) -> list[dict]:
        """Busca assets por similitud semántica con el query."""
        try:
            import numpy as np
            query_vec = np.array(self.embeddings.encode(query))

            with sqlite3.connect(self.db_path) as conn:
                if asset_types:
                    placeholders = ",".join("?" * len(asset_types))
                    rows = conn.execute(
                        f"""SELECT id, asset_type, file_path, description, metadata, embedding
                           FROM memory_assets
                           WHERE user_id = ? AND asset_type IN ({placeholders})
                             AND embedding IS NOT NULL""",
                        (user_id, *asset_types)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT id, asset_type, file_path, description, metadata, embedding
                           FROM memory_assets
                           WHERE user_id = ? AND embedding IS NOT NULL""",
                        (user_id,)
                    ).fetchall()

            if not rows:
                return []

            scored = []
            for row in rows:
                try:
                    asset_vec = np.array(json.loads(row[5]))
                    similarity = float(
                        np.dot(query_vec, asset_vec) /
                        (np.linalg.norm(query_vec) * np.linalg.norm(asset_vec) + 1e-8)
                    )
                    scored.append({
                        "id": row[0],
                        "asset_type": row[1],
                        "file_path": row[2],
                        "description": row[3],
                        "metadata": json.loads(row[4]) if row[4] else {},
                        "score": similarity,
                    })
                except Exception:
                    continue

            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[:top_k]

        except Exception as e:
            logger.error(f"❌ Error buscando assets: {e}")
            return []

    def format_assets_context(self, assets: list[dict]) -> str:
        if not assets:
            return ""
        lines = ["[Relevant memory assets]"]
        for a in assets:
            icon = {"image": "🖼", "audio": "🎙", "document": "📄", "log": "📝"}.get(
                a["asset_type"], "📌"
            )
            lines.append(f"{icon} {a['description']}")
        return "\n".join(lines)

    def clear_user(self, user_id: str):
        """
        Borra todos los assets multimodales del usuario: las filas de memory_assets y
        los ficheros copiados bajo ASSETS_DIR/<user_id>/. Parte del wipe total de
        /memory clear — la soberanía incluye los binarios en disco, no solo la DB."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM memory_assets WHERE user_id = ?", (user_id,))
        user_dir = Path(ASSETS_DIR) / user_id
        if user_dir.exists():
            shutil.rmtree(user_dir, ignore_errors=True)
        logger.info(f"🗑️  MultimodalMemory borrada para {user_id} (assets DB + ficheros)")
