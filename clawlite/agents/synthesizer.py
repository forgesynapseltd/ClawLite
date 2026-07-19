"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agents/synthesizer.py — Sintetizador de resultados multi-agente
"""

from loguru import logger
from clawlite.llm.client import llm
from clawlite.personality.voice import ClawPersonality
from clawlite.agent.tools.research.factchecker import FactCheckResult

SYNTHESIZER_PROMPT = """You are synthesizing research into a clear and useful response.

User query: {query}

Research findings:
{research}

User context:
{context}

Brand context:
{brand}

Instructions:
- {language_rule}
- Lead with the answer to the query when possible. Be direct.
- Do NOT add excessive hedging, disclaimers, or cautious language just because some information comes from fewer sources.
- Only mention uncertainty when it is genuinely important for the user to know.
- Distinguish between well-supported information and single-source information only when relevant.
- Use a natural, confident tone. Be specific and concise.
- Do not mention agents, systems, verification levels, or internal processes.
"""


class Synthesizer:
    """
    Sintetiza los resultados de múltiples agentes en una respuesta coherente.
    Decide qué incluir y qué descartar — no es un agregador.
    """

    async def merge(
        self,
        query: str,
        research_result: dict | None,
        context_result: dict | None,
        brand_result: dict | None,
    ) -> str:
        logger.info("🔀 Synthesizer merging agent results")

        research_result = research_result or {}
        context_result = context_result or {}
        brand_result = brand_result or {}

        research_content = research_result.get("content", "")

        prompt = SYNTHESIZER_PROMPT.format(
            query=query,
            research=(research_content or "No research available.")[:2000],
            context=context_result.get("full_context", "")[:800],
            brand=brand_result.get("brand_context", "Not applicable")[:400],
            language_rule=ClawPersonality.get_language_rule(),
        )

        system = ClawPersonality.get_system_prompt(
            context_result.get("layer1", "")
        )

        try:
            response, _ = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                system=system,
                max_tokens=1500,
                enforce_language=True,
            )
            if response and response.strip():
                return response
            raise ValueError("Synthesizer LLM devolvió respuesta vacía")
        except Exception as e:
            logger.error(f"❌ Synthesizer failed: {e}")
            fallback = research_result.get("content", "")
            if fallback and fallback.strip():
                return fallback
            return "No pude completar la investigación en este momento. Vuelve a intentarlo."