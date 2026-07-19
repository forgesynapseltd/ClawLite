"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agents/specialized/context_agent.py — Agente de contexto personal
"""

from loguru import logger
from clawlite.memory.profile import UserProfile
from clawlite.agents.memory.hierarchical import HierarchicalMemory


class ContextAgent:
    """Perfil del usuario + memoria jerárquica de 3 capas."""

    def __init__(self, profile: UserProfile, hierarchical: HierarchicalMemory):
        self.profile = profile
        self.hierarchical = hierarchical
        self._pending_learning: "asyncio.Task | None" = None

    async def run(self, user_id: str, query: str) -> dict:
        logger.info(f"🧠 ContextAgent: building context for {user_id}")

        # El resultado real del agente es el CONTEXTO. Se construye primero y se
        # blinda: si una capa falla, se entrega lo que se pudo, nunca se lanza.
        try:
            layer1 = self.profile.build_context(user_id)
        except Exception as e:
            logger.warning(f"⚠️ ContextAgent layer1 falló: {e}")
            layer1 = ""
        try:
            layer2_3 = self.hierarchical.build_full_context(user_id)
        except Exception as e:
            logger.warning(f"⚠️ ContextAgent layer2_3 falló: {e}")
            layer2_3 = ""

        # El aprendizaje (guardar patrones/entidades) es un EFECTO SECUNDARIO: no
        # forma parte del contexto que devolvemos. Su fallo jamás debe arrastrar
        # el resultado del agente ni, vía gather, a los demás agentes. Se dispara
        # en background, desacoplado del retorno. Se guarda la referencia (no solo
        # se dispara) para que quien lo necesite -- p.ej. un worker aislado que va
        # a cerrar su event loop -- pueda esperarla explícitamente sin tener que
        # adivinar ni esperar tareas ajenas del loop.
        import asyncio
        self._pending_learning = asyncio.create_task(self._learn_safely(user_id, query))

        return {
            "agent": "context",
            "layer1": layer1,
            "layer2_3": layer2_3,
            "full_context": f"{layer1}\n\n{layer2_3}".strip(),
        }

    async def _learn_safely(self, user_id: str, query: str):
        """Efecto secundario aislado: si falla, se loguea y muere ahí. Nunca sube."""
        try:
            await self.hierarchical.detect_and_save_pattern(user_id, query)
        except Exception as e:
            logger.debug(f"ContextAgent detect_pattern omitido: {e}")
        try:
            await self.hierarchical.extract_and_save_entities(user_id, query)
        except Exception as e:
            logger.debug(f"ContextAgent extract_entities omitido: {e}")

    async def await_pending_learning(self):
        """Espera la tarea de aprendizaje en background disparada por el último
        run(), si sigue pendiente. Contrato explícito para callers que necesiten
        garantizar que el aprendizaje terminó -- p.ej. antes de que un worker
        aislado cierre su event loop con asyncio.run()."""
        import asyncio
        if self._pending_learning is not None:
            await asyncio.gather(self._pending_learning, return_exceptions=True)
