"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

workflows/executor.py — Ejecuta workflows paso a paso
Resuelve placeholders {variable} en params usando el context compartido,
de forma que cada paso pueda referenciar outputs de pasos anteriores.
"""

import time
import re
from loguru import logger


class WorkflowExecutor:
    """
    Ejecuta los pasos de un workflow en orden.
    Resuelve placeholders en params usando el context antes de cada acción.
    El context acumula los outputs de cada paso para que los siguientes los usen.
    """

    def __init__(self, registry, store):
        self.registry = registry
        self.store = store

    async def execute(
        self,
        workflow: dict,
        user_id: str,
        user_message: str,
    ) -> tuple[str, bool]:
        workflow_id = workflow["id"]
        steps = workflow["steps"]
        name = workflow["name"]

        logger.info(f"⚡ Ejecutando workflow [{name}] ({len(steps)} pasos)")

        context = {
            "user_id": user_id,
            "user_message": user_message,
        }

        start_time = time.time()
        success = True
        final_output = ""

        for i, step in enumerate(steps, 1):
            action = step.get("action")
            raw_params = step.get("params", {})
            save_as = step.get("save_as")

            if not action:
                logger.warning(f"Step {i} sin action, saltando")
                continue

            if not self.registry.is_valid_action(action):
                logger.error(f"Action '{action}' no existe en registry")
                success = False
                break

            # Resolver placeholders {var} en los params usando el context actual
            resolved_params = self._resolve_params(raw_params, context)

            logger.debug(f"  Paso {i}/{len(steps)}: {action} params={list(resolved_params.keys())}")
            result = await self.registry.execute(action, resolved_params, context)

            if not result["success"]:
                logger.error(f"Step {i} falló: {result['error']}")
                success = False
                break

            if save_as:
                context[save_as] = result["output"]

            final_output = str(result["output"]) if result["output"] is not None else final_output

        duration = time.time() - start_time
        self.store.record_execution(workflow_id, success, duration)

        if success:
            logger.info(f"✅ Workflow [{name}] completado en {duration:.2f}s")
        else:
            logger.warning(f"❌ Workflow [{name}] falló después de {duration:.2f}s")

        return final_output, success

    def _resolve_params(self, params: dict, context: dict) -> dict:
        """
        Sustituye placeholders {var} en strings de params por valores del context.
        Si la variable no existe en context, deja el placeholder sin cambios
        para que la acción pueda usar su default.
        """
        if not isinstance(params, dict):
            return params

        resolved = {}
        for key, value in params.items():
            resolved[key] = self._resolve_value(value, context)
        return resolved

    def _resolve_value(self, value, context: dict):
        """Resuelve recursivamente un valor (string, dict, list)."""
        if isinstance(value, str):
            return self._substitute_placeholders(value, context)
        elif isinstance(value, dict):
            return {k: self._resolve_value(v, context) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._resolve_value(v, context) for v in value]
        return value

    def _substitute_placeholders(self, text: str, context: dict) -> str:
        """
        Sustituye {var_name} en el texto por context["var_name"].
        Si la variable no existe o es vacía, elimina el placeholder
        para que no quede texto literal sin sentido.
        """
        def replace(match):
            var_name = match.group(1).strip()
            if var_name in context:
                value = context[var_name]
                if value is None or value == "":
                    return ""
                # Convertir dicts a string legible
                if isinstance(value, dict):
                    import json
                    return json.dumps(value, ensure_ascii=False)
                return str(value)
            # Variable no encontrada — devolver vacío en lugar del placeholder literal
            return ""

        return re.sub(r'\{([^{}]+)\}', replace, text)
