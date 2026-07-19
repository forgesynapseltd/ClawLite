"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agents/orchestrator.py — Orquestador del sistema multi-agente
"""

import asyncio
from loguru import logger
from clawlite.agents.specialized.coding_agent import CodingAgent
from clawlite.agents.synthesizer import Synthesizer
from clawlite.agents.skill_store import SkillStore
from clawlite.agents.memory.hierarchical import HierarchicalMemory
from clawlite.agents.worker_pool import AgentWorkerPool
from clawlite.memory.profile import UserProfile
from clawlite.agent.tools.brand import BrandManager
from clawlite.governance import action_guard, Mandate, MandateOrigin, GovernanceDenied


class Orchestrator:
    """
    Decide qué agentes activar y los lanza en paralelo.
    Para tareas simples usa el flujo existente.
    Para tareas complejas activa el sistema multi-agente.
    """

    def __init__(
        self,
        profile: UserProfile,
        brand_manager: BrandManager,
        db_path: str,
        memory_store=None,
    ):
        # SkillStore necesita el motor de embeddings para agrupación semántica.
        # Lo tomamos del memory_store (que ya tiene el EmbeddingEngine inyectado),
        # manteniendo una sola instancia del motor en todo ClawLite.
        self.skill_store = SkillStore(
            db_path,
            embedding_engine=(memory_store.embeddings if memory_store else None),
        )
        self.hierarchical = HierarchicalMemory(db_path)

        # ResearchAgent/ContextAgent/BrandAgent ya no viven en este proceso: corren
        # aislados vía AgentWorkerPool, un proceso OS dedicado por agente (ver
        # contrato de construcción en agents/worker_pool.py).
        self.worker_pool = AgentWorkerPool(db_path)
        self.synthesizer = Synthesizer()

        # Metadatos de verificación de la última corrida de research. El camino
        # síncrono (deep_research) los lee para construir su footer de confianza;
        # el camino async los ignora. No se tocan los valores de retorno de run(),
        # así que ningún llamador existente se rompe.
        self.last_research_meta: dict | None = None

    async def run(
        self,
        user_id: str,
        query: str,
        is_news: bool = False,
        term_groups: list[list[str]] | None = None,
        query_type: str | None = None,
        include_personal_context: bool = True,
    ) -> tuple[str, bool]:
        """Lanza los agentes en paralelo y sintetiza.

        include_personal_context: controla DOS comportamientos, no uno --
        si False:
          1. No se incorpora contexto personal del usuario a la síntesis
             (context_result=None hacia Synthesizer.merge()).
          2. ContextAgent NI SIQUIERA SE EJECUTA, así que tampoco ocurre el
             aprendizaje en background (_learn_safely: detección de
             patrones/entidades) derivado de ESTA consulta.
        Para llamadores con una query SINTÉTICA (no algo que el usuario
        realmente dijo -- ej. "current content marketing trends...", usada
        para investigación de tendencias que alimenta un calendario de
        marca) ambos efectos son el comportamiento correcto: ni se quiere
        contaminar la síntesis con hechos personales ajenos al pedido, ni
        se quiere que esa query sintética modifique la memoria del usuario
        real. Default True preserva el comportamiento de siempre
        (Research+Context+Brand+Síntesis) para todo llamador existente --
        esto no salta el orquestador, solo excluye una fuente de su
        entrada cuando el llamador no la necesita ni la quiere."""
        logger.info(f"🚀 Orchestrator running multi-agent for {user_id}")

        # Los agentes ya están blindados internamente: cada uno se recupera y
        # devuelve su resultado. return_exceptions=True es la red de ÚLTIMO recurso
        # por si algo imprevisto sube igual — un agente caído nunca debe tumbar a
        # los que sí entregaron. El research, que es el núcleo de la respuesta,
        # siempre llega.
        if include_personal_context:
            results = await asyncio.gather(
                self.worker_pool.run_research(
                    user_id, query, is_news=is_news, term_groups=term_groups, query_type=query_type
                ),
                self.worker_pool.run_context(user_id, query),
                self.worker_pool.run_brand(user_id, query),
                return_exceptions=True,
            )
            research_result, context_result, brand_result = results
        else:
            # ContextAgent ni se lanza -- no solo se descarta su resultado.
            # Ver docstring: para una query sintética, ejecutarlo sería
            # trabajo desperdiciado y aprendizaje espurio sobre el grafo de
            # conocimiento del usuario real.
            results = await asyncio.gather(
                self.worker_pool.run_research(
                    user_id, query, is_news=is_news, term_groups=term_groups, query_type=query_type
                ),
                self.worker_pool.run_brand(user_id, query),
                return_exceptions=True,
            )
            research_result, brand_result = results
            context_result = None

        # Si un agente devolvió excepción (caso extremo), se neutraliza a None para
        # que el synthesizer trabaje con lo que sí llegó, sin romperse.
        if isinstance(research_result, Exception):
            logger.error(f"❌ ResearchAgent excepción no recuperada: {research_result}")
            research_result = None
        if isinstance(context_result, Exception):
            logger.warning(f"⚠️ ContextAgent excepción no recuperada: {context_result}")
            context_result = None
        if isinstance(brand_result, Exception):
            logger.warning(f"⚠️ BrandAgent excepción no recuperada: {brand_result}")
            brand_result = None

        # Capturar metadatos de verificación del research para quien quiera el
        # footer de confianza (deep_research síncrono). Si el research cayó, no hay
        # metadatos: se deja en None y el footer simplemente no se añade.
        if research_result:
            self.last_research_meta = {
                "sources_checked": research_result.get("sources_checked", 0),
                "verified_claims": research_result.get("verified_claims", 0),
                "total_claims": research_result.get("total_claims", 0),
                "sources": research_result.get("sources", []),
                "synthesis_failed": research_result.get("synthesis_failed", False),
                "edge_message": research_result.get("edge_message", False),
            }
        else:
            self.last_research_meta = None

        # El engine de research YA produce una respuesta final completa que respeta
        # el idioma de la consulta y trae la calibración de confianza. La segunda
        # síntesis (synthesizer.merge) solo aporta cuando hay marca real que
        # combinar; en búsqueda/noticias pura reescribe esa respuesta inyectando el
        # contexto del usuario (que puede estar en otro idioma) y termina rompiendo
        # el idioma y duplicando el coste LLM. Por eso: si hay aporte de marca, se
        # sintetiza; si no, se devuelve el research del engine tal cual.
        #
        # Si la síntesis del engine falló O la respuesta es un mensaje de borde
        # (banderas estructurales, no texto), no hay contenido de investigación
        # real que combinar con nada: se devuelve el mensaje tal cual y se salta
        # el merge, para que el synthesizer nunca reciba un error ni un "no
        # encontré fuentes" como si fueran "research findings" a redactar.
        if research_result and (
            research_result.get("synthesis_failed") or research_result.get("edge_message")
        ):
            response = research_result["content"]
        else:
            brand_text = (brand_result or {}).get("brand_context", "") if brand_result else ""
            brand_adds_value = bool(brand_text) and brand_text.strip().lower() not in ("", "not applicable")

            if brand_adds_value:
                response = await self.synthesizer.merge(
                    query=query,
                    research_result=research_result,
                    context_result=context_result,
                    brand_result=brand_result,
                )
            elif research_result and research_result.get("content", "").strip():
                # Camino normal de búsqueda/noticias: el research del engine es la
                # respuesta. Idioma correcto (lo fijó el engine), confianza ya calibrada.
                response = research_result["content"]
            else:
                # Sin research utilizable ni marca: último recurso vía synthesizer, que
                # tiene su propio fallback honesto.
                response = await self.synthesizer.merge(
                    query=query,
                    research_result=research_result,
                    context_result=context_result,
                    brand_result=brand_result,
                )

        # Self-improving loop — silencioso
        asyncio.create_task(
            self.skill_store.learn(user_id, "research", query, response)
        )

        return response, False

    async def run_coding(self, user_id: str, request: str, progress_callback=None,
                         scheduled: bool = False) -> dict:
        """
        Ejecuta el CodingAgent en sandbox Docker aislado.
        El callback de progreso permite notificar al usuario cada paso en tiempo real.
        Retorna un dict estructurado con summary, files, execution_log para que
        el canal (Telegram, etc.) presente los resultados como mejor convenga.

        scheduled=False → petición directa del usuario (USER_DIRECT);
        scheduled=True  → job creado antes por el usuario (SYSTEM_SCHEDULED).
        """
        # ── Compuerta de gobernanza (ActionGuard) ──────────────────────────────
        # Ejecutar código es ALTO impacto (aislado en Docker, pero arbitrario + red).
        # Toda ejecución pasa por aquí (chokepoint único, sync y async) y se audita.
        origin = MandateOrigin.SYSTEM_SCHEDULED if scheduled else MandateOrigin.USER_DIRECT
        try:
            action_guard.enforce(
                "execute_code",
                Mandate(origin=origin, user_id=str(user_id), summary=(request or "")[:200]),
            )
        except GovernanceDenied as denied:
            logger.warning(f"🛡️ Ejecución de código DENEGADA por el kernel: {denied.decision.reason}")
            return {
                "success": False,
                "summary": f"🚫 No ejecuté el código: la política de seguridad lo bloqueó ({denied.decision.reason}).",
                "files": [],
                "execution_log": "",
            }

        logger.info(f"💻 Orchestrator routing to CodingAgent for {user_id}")
        agent = CodingAgent(progress_callback=progress_callback)
        result = await agent.run(user_id, request)

        # Self-improving — registrar el patrón si fue exitoso
        if result.get("success"):
            asyncio.create_task(
                self.skill_store.learn(
                    user_id, "coding", request,
                    result.get("summary", "")
                )
            )
        return result
