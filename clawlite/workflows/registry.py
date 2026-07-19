"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

workflows/registry.py — Catálogo de acciones ejecutables
Las acciones inyectan automáticamente el context relevante para que el LLM
tenga acceso a todos los outputs de pasos anteriores sin hardcodear nombres.
"""

import json
from loguru import logger
from clawlite.agent.tools.search import search_tool
from clawlite.agent.tools.reader import reader
from clawlite.llm.client import llm
from clawlite.sandbox.docker_manager import docker_sandbox


INTERNAL_CONTEXT_KEYS = {"user_id", "user_message"}


class ActionRegistry:
    """
    Catálogo central de acciones que los workflows pueden ejecutar.
    Cualquier paso de un workflow debe ser una acción registrada aquí.
    """

    def __init__(self, brand_manager=None, gmail_tool=None, deep_memory=None):
        self.brand_manager = brand_manager
        self.gmail_tool = gmail_tool
        self.deep_memory = deep_memory

        self._actions = {
            "search_web": self._search_web,
            "search_trends": self._search_trends,
            "extract_brand": self._extract_brand,
            "generate_content": self._generate_content,
            "summarize_text": self._summarize_text,
            "gmail_classify": self._gmail_classify,
            "calendar_today": self._calendar_today,
            "format_output": self._format_output,
            "execute_python": self._execute_python,
        }

    def list_actions(self) -> list[str]:
        return list(self._actions.keys())

    def is_valid_action(self, name: str) -> bool:
        return name in self._actions

    async def execute(self, action: str, params: dict, context: dict) -> dict:
        if not self.is_valid_action(action):
            return {"success": False, "output": None, "error": f"Action '{action}' not in registry"}

        try:
            result = await self._actions[action](params, context)
            return {"success": True, "output": result, "error": None}
        except Exception as e:
            logger.error(f"❌ Action '{action}' failed: {e}")
            return {"success": False, "output": None, "error": str(e)}

    def _build_context_block(self, context: dict, exclude: set = None) -> str:
        """Construye un bloque de contexto con TODOS los outputs de pasos anteriores."""
        exclude = exclude or set()
        excluded_keys = INTERNAL_CONTEXT_KEYS | exclude

        parts = []
        for key, value in context.items():
            if key in excluded_keys:
                continue
            if value is None or value == "":
                continue

            if isinstance(value, dict):
                if not value:
                    continue
                content = json.dumps(value, ensure_ascii=False, indent=2)
            elif isinstance(value, (list, tuple)):
                if not value:
                    continue
                content = "\n".join(str(v) for v in value[:10])
            else:
                content = str(value)

            if len(content) > 1500:
                content = content[:1500] + "..."

            parts.append(f"## {key}\n{content}")

        if not parts:
            return ""

        return "\n\n=== CONTEXT FROM PREVIOUS STEPS ===\n" + "\n\n".join(parts) + "\n=== END CONTEXT ===\n"

    # ── ACCIONES REGISTRADAS ────────────────────────────────────────────────

    async def _search_web(self, params: dict, context: dict) -> str:
        query = params.get("query") or context.get("user_message", "")
        query = query.strip()
        if not query:
            return ""
        max_results = params.get("max_results", 5)
        return await search_tool.search(query, max_results=max_results, user_id=context.get("user_id"))

    async def _search_trends(self, params: dict, context: dict) -> str:
        topic = params.get("topic", "").strip()
        location = params.get("location", "").strip()
        if not topic:
            topic = context.get("user_message", "")[:80]
        query = f"trending {topic} {location} 2026".strip()
        return await search_tool.search(query, max_results=4, user_id=context.get("user_id"))

    async def _extract_brand(self, params: dict, context: dict) -> dict:
        if not self.brand_manager:
            return {}
        user_id = context.get("user_id")
        if not user_id:
            return {}
        return self.brand_manager.get_brand(user_id) or {}

    async def _generate_content(self, params: dict, context: dict) -> str:
        prompt = params.get("prompt", context.get("user_message", ""))
        system = params.get("system", "You are a helpful assistant. Use the provided context to generate a complete, specific response. Respond in the user's language.")
        max_tokens = params.get("max_tokens", 800)

        context_block = self._build_context_block(context)
        full_prompt = f"{prompt}\n{context_block}".strip()

        response, _ = await llm.complete(
            messages=[{"role": "user", "content": full_prompt}],
            system=system,
            max_tokens=max_tokens,
        )
        return response

    async def _summarize_text(self, params: dict, context: dict) -> str:
        text = params.get("text", "").strip()
        if not text:
            for key in reversed(list(context.keys())):
                if key in INTERNAL_CONTEXT_KEYS:
                    continue
                val = context[key]
                if isinstance(val, str) and len(val) > 50:
                    text = val
                    break

        if not text:
            return ""

        user_message = context.get("user_message", "")
        max_tokens = params.get("max_tokens", 250)

        system = (
            "You are a precise summarizer. "
            "Respond in the same language as the user's original message. "
            "Summarize the key points in 2-4 clear sentences. "
            "Be specific and avoid filler or hedging. "
            "If the text is in English but the user asked in another language, still respond in the user's language."
        )

        response, _ = await llm.complete(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"User's original message: {user_message}\n\nText to summarize:\n{text[:3500]}"}
            ],
            max_tokens=max_tokens,
        )
        return response

    async def _gmail_classify(self, params: dict, context: dict) -> str:
        if not self.gmail_tool or not self.gmail_tool.is_authenticated():
            return "Gmail no conectado."

        max_results = params.get("max_results", 10)
        emails = self.gmail_tool.get_unread_emails(
            max_results=max_results, user_id=context.get("user_id")
        )
        if not emails:
            return "Inbox limpio."

        email_text = self.gmail_tool.format_emails_for_summary(emails)
        response, _ = await llm.complete(
            messages=[{"role": "user", "content": email_text}],
            system="Classify each email as URGENT, INFO or SKIP. One line per email. Be ruthless.",
            max_tokens=300,
        )
        return response

    async def _calendar_today(self, params: dict, context: dict) -> str:
        from datetime import datetime
        if not self.gmail_tool or not self.gmail_tool.is_authenticated():
            return "Calendar no conectado."

        events = self.gmail_tool.get_upcoming_events(
            max_results=10, user_id=context.get("user_id")
        )
        today = datetime.now().strftime("%Y-%m-%d")
        today_events = [e for e in events if e["start"].startswith(today)]

        if not today_events:
            return "Sin eventos hoy."

        lines = []
        for e in today_events:
            time = e["start"][11:16] if "T" in e["start"] else ""
            lines.append(f"• {time} — {e['title']}")
        return "\n".join(lines)

    async def _format_output(self, params: dict, context: dict) -> str:
        template = params.get("template", "")
        if not template:
            for key in reversed(list(context.keys())):
                if key in INTERNAL_CONTEXT_KEYS:
                    continue
                val = context[key]
                if val:
                    return str(val)
            return ""
        return template

    async def _execute_python(self, params: dict, context: dict) -> str:
        """
        Ejecuta código Python en un contenedor Docker aislado.
        Soporta tres niveles: 'isolated' (default), 'networked', 'filesystem'.
        Devuelve el stdout del código ejecutado.
        """
        code = params.get("code", "").strip()
        if not code:
            return ""

        level = params.get("level", "isolated")

        result = docker_sandbox.execute_python(code=code, level=level)

        if result.timed_out:
            return f"⏱ Execution timed out after {result.duration:.1f}s"

        if not result.success:
            return f"❌ Execution failed (exit {result.exit_code}):\n{result.stderr}\n{result.error}".strip()

        output = result.stdout.strip()
        if not output:
            return "✅ Executed successfully (no output)"

        return output