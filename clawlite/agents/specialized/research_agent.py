"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agents/specialized/research_agent.py — Agente de investigación
"""

from loguru import logger
from clawlite.agent.tools.research.engine import research_engine
from clawlite.agents.skill_store import SkillStore


class ResearchAgent:
    """Investigación profunda: Tavily + scraper + fact-checker."""

    def __init__(self, skill_store: SkillStore):
        self.skill_store = skill_store

    async def run(
        self,
        user_id: str,
        query: str,
        is_news: bool = False,
        term_groups: list[list[str]] | None = None,
        query_type: str | None = None,
    ) -> dict:
        logger.info(f"🔬 ResearchAgent: {query[:60]}")

        skills = self.skill_store.get_relevant_skills(user_id, "research")
        skills_context = self.skill_store.format_skills_context(skills)

        result = await research_engine.research(
            query, is_news=is_news, term_groups=term_groups, query_type=query_type, user_id=user_id
        )

        return {
            "agent": "research",
            "content": result.answer,
            "sources": result.sources,
            "verified_claims": result.verified_claims,
            "total_claims": result.total_claims,
            "sources_checked": result.sources_checked,
            "synthesis_failed": result.synthesis_failed,
            "edge_message": result.edge_message,
            "skills_used": skills_context,
        }
