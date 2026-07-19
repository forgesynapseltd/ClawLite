"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

workflows/extractor.py — Promueve skills recurrentes a workflows ejecutables
Pasa los mensajes REALES del usuario al workflow para que el match sea robusto.
"""

import json
import re
from loguru import logger
from clawlite.llm.client import llm
from clawlite.llm.json_parser import extract_json


PROMOTION_THRESHOLD = 3
RECURRENCE_WINDOW_DAYS = 14


EXTRACT_WORKFLOW_PROMPT = """Convert this recurring task pattern into a reusable workflow.

The user has activated this skill {uses} times with messages like:
{examples}

Skill metadata:
- Trigger: {trigger}
- Approach: {approach}
- Outcome: {outcome}
- Task type: {task_type}

Available actions in the registry (you MUST only use these):
{actions}

Return ONLY a JSON workflow:
{{
  "name": "short descriptive name (3-5 words)",
  "trigger": "natural language description of when this applies",
  "steps": [
    {{"action": "action_name", "params": {{}}, "save_as": "variable_name"}}
  ]
}}

Rules:
- Each step must use an action from the registry list
- Use save_as to chain outputs between steps
- Keep workflows simple — 2-5 steps maximum
- If the task is too vague to automate, return {{"skip": true}}
"""


class WorkflowExtractor:
    """
    Promueve skills recurrentes a workflows.
    Pasa los mensajes reales del usuario al workflow para garantizar
    matching robusto en su idioma y vocabulario.
    """

    def __init__(self, registry, store, skill_store):
        self.registry = registry
        self.store = store
        self.skill_store = skill_store

    async def analyze_user_history(self, user_id: str) -> int | None:
        recurring = self.skill_store.get_recurring_skills(
            user_id=user_id,
            min_uses=PROMOTION_THRESHOLD,
            days_window=RECURRENCE_WINDOW_DAYS,
        )

        if not recurring:
            return None

        candidate = recurring[0]
        examples = candidate.get("examples", [])

        if not examples:
            logger.debug("Skill sin examples reales — esperando más datos")
            return None

        # Verificar usando un example real, no el trigger abstracto
        existing = self.store.find_matching_workflow(user_id, examples[0])
        if existing:
            return None

        logger.info(
            f"🎯 Skill recurrente [{candidate['uses']} usos, {len(examples)} examples]: "
            f"'{examples[0][:60]}' — promoviendo a workflow"
        )

        return await self._extract_workflow(user_id, candidate)

    async def _extract_workflow(self, user_id: str, skill: dict) -> int | None:
        try:
            actions_list = ", ".join(self.registry.list_actions())
            examples = skill.get("examples", [])
            examples_text = "\n".join(f'- "{e}"' for e in examples[:5])

            prompt = EXTRACT_WORKFLOW_PROMPT.format(
                uses=skill["uses"],
                examples=examples_text or "(no examples)",
                trigger=skill["trigger"],
                approach=skill["approach"],
                outcome=skill["outcome"],
                task_type=skill["task_type"],
                actions=actions_list,
            )

            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                structured=True,
            )

            data = extract_json(raw, expect="object")
            if not data:
                return None


            if data.get("skip"):
                logger.debug("Workflow extraction skipped — task too vague")
                return None

            name = data.get("name", "")
            trigger = data.get("trigger", "")
            steps = data.get("steps", [])

            if not name or not trigger or not steps:
                return None

            for step in steps:
                action = step.get("action", "")
                if not self.registry.is_valid_action(action):
                    logger.warning(f"Workflow rechazado — action inválida: '{action}'")
                    return None

            # Pasar los examples reales al workflow para que el embedding sea preciso
            workflow_id = self.store.save_workflow(
                user_id=user_id,
                name=name,
                trigger_text=trigger,
                steps=steps,
                examples=examples,
            )

            if workflow_id:
                logger.info(
                    f"🔧 Nuevo workflow creado: '{name}' (workflow #{workflow_id}) "
                    f"con {len(examples)} examples reales"
                )

            return workflow_id

        except Exception as e:
            logger.debug(f"Workflow extraction failed: {e}")
            return None
