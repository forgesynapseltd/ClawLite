"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agents/specialized/brand_agent.py — Agente de marca y contenido
"""

from loguru import logger
from clawlite.agent.tools.brand import BrandManager
from clawlite.agents.skill_store import SkillStore


class BrandAgent:
    """Contexto de marca, tono y contenido personalizado."""

    def __init__(self, brand_manager: BrandManager, skill_store: SkillStore):
        self.brand_manager = brand_manager
        self.skill_store = skill_store

    async def run(self, user_id: str, query: str) -> dict:
        logger.info(f"🎨 BrandAgent: {user_id}")

        # Cada fuente del resultado se blinda por separado: un fallo en una no
        # vacía el resto ni lanza al gather. El agente siempre devuelve su dict.
        try:
            brand = self.brand_manager.get_brand(user_id)
        except Exception as e:
            logger.warning(f"⚠️ BrandAgent get_brand falló: {e}")
            brand = None
        try:
            brand_context = self.brand_manager.build_brand_context(user_id)
        except Exception as e:
            logger.warning(f"⚠️ BrandAgent brand_context falló: {e}")
            brand_context = ""
        try:
            has_brand = self.brand_manager.has_brand(user_id)
        except Exception:
            has_brand = False
        try:
            skills = self.skill_store.get_relevant_skills(user_id, "content")
            content_skills = self.skill_store.format_skills_context(skills)
        except Exception as e:
            logger.warning(f"⚠️ BrandAgent skills falló: {e}")
            content_skills = ""

        return {
            "agent": "brand",
            "brand": brand,
            "brand_context": brand_context,
            "has_brand": has_brand,
            "content_skills": content_skills,
        }
