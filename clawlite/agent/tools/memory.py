"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agent/tools/memory.py — Tool de memoria para el agente
Interfaz entre el agente y el MemoryStore.
"""

from loguru import logger
from clawlite.memory.store import MemoryStore
from clawlite.sandbox.guard import SandboxGuard
from clawlite.config import config

guard = SandboxGuard(mode=config.SANDBOX_MODE)


class MemoryTool:
    def __init__(self, store: MemoryStore):
        self.store = store

    async def remember(self, user_id: str, content: str) -> bool:
        """Guarda un hecho explícito en memoria."""
        guard.validate_tool_call("remember")
        try:
            self.store.save_message(user_id, "fact", content)
            logger.info(f"💾 Hecho guardado para {user_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Error guardando memoria: {e}")
            return False

    async def recall(self, user_id: str, query: str) -> list[str]:
        """Recupera los recuerdos más relevantes para el query."""
        guard.validate_tool_call("recall")
        results = self.store.recall_similar(user_id, query, top_k=5)
        logger.info(f"🧠 Recall: {len(results)} resultados para '{query[:40]}'")
        return results

    async def get_recent(self, user_id: str, limit: int = 8) -> list[dict]:
        """Devuelve los últimos N mensajes del usuario."""
        return self.store.get_recent(user_id, limit=limit)
