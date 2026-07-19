"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agent/tools/brand.py — Community Manager y Desarrollo de Marca
Memoria de marca persistente + generación de contenido + estrategia.
El usuario configura su marca una vez y ClawLite siempre la recuerda.
"""

import json
import re
from loguru import logger
from clawlite.memory.profile import DeepMemory
from clawlite.llm.client import llm
from clawlite.llm.json_parser import extract_json
from clawlite.agent.tools.search import search_tool
from clawlite.personality.voice import ClawPersonality


BRAND_EXTRACT_PROMPT = """Extract brand/business information ONLY when the user is DECLARING
facts about their OWN business. Return ONLY a JSON object (empty {} if nothing to save).

CRITICAL — a QUESTION is never brand information:
- "What are the marketing trends for social media?" → {} (a question, tells you nothing about
  the user's business)
- "How do I grow on Instagram?" / "give me post ideas" / "what works on TikTok?" → {} (requests
  for help, not declarations about a business)
- "I run a florist in Madrid called Flor y Vida, casual tone" → extract those facts (a declaration)
- "My target audience is young professionals" → {"target_audience": "young professionals"}

Only extract a field when the user states it as a fact about THEIR business. If the message is a
question, a request for content, or a request for advice, return {} — there is nothing to save.

JSON shape:
{
  "business_name": "...",
  "business_type": "...",
  "target_audience": "...",
  "tone": "formal | casual | playful | inspirational",
  "location": "...",
  "platforms": ["instagram", "tiktok", "linkedin", "x"],
  "products_services": ["..."],
  "brand_values": ["..."]
}

Return {} unless the user is clearly stating facts about their own business.
"""

CONTENT_DETECT_PROMPT = """Is this message a request for social media content, brand strategy, or community management?

Return ONLY JSON: {"is_cm_request": true/false, "intent": "post|calendar|strategy|hashtags|caption|trend|competitor|brand_setup|general_cm"}

CM requests include: asking for posts, captions, hashtags, content ideas, posting schedules,
brand strategy, competitor analysis, trend research for social media, or anything about
managing social media presence.

Return {"is_cm_request": false, "intent": ""} if not a CM request.
"""

CM_SYSTEM_PROMPT = """You are an expert Community Manager and Brand Strategist with 10+ years of experience.

You know what works on each platform:
- Instagram: visual storytelling, emotional captions, 5-10 hashtags, carousel posts perform best
- TikTok: hooks in first 3 seconds, trending sounds, authenticity over polish
- LinkedIn: professional insights, personal stories with business lessons, minimal hashtags
- X (Twitter): concise, opinionated, conversational, trending topics
- Facebook: community building, longer posts acceptable, events and local focus

Your content is:
- Specific to the brand, never generic
- Written in the brand's tone and voice
- Optimized for the platform requested
- Ready to publish — no placeholders like [INSERT EMOJI]
- Culturally relevant to the brand's location and audience

Always include:
- The full post text ready to copy-paste
- Hashtag suggestions (platform-appropriate quantity)
- Best time to post suggestion
- Image/video description for the visual

{brand_context}
"""


class BrandManager:
    """
    Community Manager integrado con memoria de marca persistente.
    Aprende sobre el negocio del usuario y nunca pregunta lo mismo dos veces.
    """

    PATTERN_KEY = "brand_profile"

    def __init__(self, deep_memory: DeepMemory):
        self.memory = deep_memory

    # ── MEMORIA DE MARCA ────────────────────────────────────────────────────

    def get_brand(self, user_id: str) -> dict:
        return self.memory.get_pattern(user_id, self.PATTERN_KEY)

    def save_brand(self, user_id: str, brand_data: dict):
        existing = self.get_brand(user_id)
        # Merge — no sobreescribir campos existentes con vacíos
        merged = {**existing, **{k: v for k, v in brand_data.items() if v}}
        self.memory.set_pattern(user_id, self.PATTERN_KEY, merged)
        logger.info(f"🏷️  Brand profile updated for {user_id}: {list(brand_data.keys())}")

    def build_brand_context(self, user_id: str) -> str:
        brand = self.get_brand(user_id)
        if not brand:
            return ""

        lines = ["[Brand profile — use this to personalize all content]\n"]
        if brand.get("business_name"):
            lines.append(f"Business: {brand['business_name']}")
        if brand.get("business_type"):
            lines.append(f"Type: {brand['business_type']}")
        if brand.get("target_audience"):
            lines.append(f"Target audience: {brand['target_audience']}")
        if brand.get("tone"):
            lines.append(f"Brand tone: {brand['tone']}")
        if brand.get("location"):
            lines.append(f"Location: {brand['location']}")
        if brand.get("platforms"):
            lines.append(f"Active platforms: {', '.join(brand['platforms'])}")
        if brand.get("products_services"):
            lines.append(f"Products/services: {', '.join(brand['products_services'])}")
        if brand.get("brand_values"):
            lines.append(f"Brand values: {', '.join(brand['brand_values'])}")

        return "\n".join(lines)

    def has_brand(self, user_id: str) -> bool:
        brand = self.get_brand(user_id)
        return bool(brand.get("business_name") or brand.get("business_type"))

    # ── DETECCIÓN DE INTENCIÓN ───────────────────────────────────────────────

    async def is_cm_request(self, message: str) -> tuple[bool, str]:
        """Detecta si el mensaje es una petición de CM."""
        try:
            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": message}],
                system=CONTENT_DETECT_PROMPT,
                max_tokens=60,
                structured=True,
            )
            data = extract_json(raw, expect="object")
            if not data:
                return False, ""
            return data.get("is_cm_request", False), data.get("intent", "")
        except Exception:
            return False, ""

    # ── EXTRACCIÓN DE MARCA ──────────────────────────────────────────────────

    async def extract_and_save_brand(self, user_id: str, message: str):
        """Extrae información de marca del mensaje y la guarda silenciosamente."""
        try:
            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": message}],
                system=BRAND_EXTRACT_PROMPT,
                max_tokens=200,
                structured=True,
            )
            data = extract_json(raw, expect="object")
            if not data:
                return
            self.save_brand(user_id, data)
        except Exception as e:
            logger.debug(f"Brand extraction skipped: {e}")

    # ── GENERACIÓN DE CONTENIDO ──────────────────────────────────────────────

    async def generate_content(self, user_id: str, request: str, intent: str) -> str:
        """Genera contenido personalizado para la marca del usuario."""
        brand_context = self.build_brand_context(user_id)
        # Red única de idioma (misma fuente que core.py/engine.py/synthesizer.py):
        # sin esto, el system prompt en inglés arrastraba la respuesta al inglés
        # aunque el planner ya hubiera fijado el idioma del turno (hallazgo 11 jul).
        system = (
            CM_SYSTEM_PROMPT.replace("{brand_context}", brand_context)
            + f"\n\n{ClawPersonality.get_language_rule()}"
        )

        # Para tendencias, buscar primero en tiempo real
        extra_context = ""
        if intent in ["trend", "post", "calendar"]:
            brand = self.get_brand(user_id)
            business_type = brand.get("business_type", "")
            location = brand.get("location", "")
            if business_type:
                trends = await search_tool.search(
                    f"trending {business_type} social media content ideas {location} 2026",
                    max_results=3, user_id=user_id,
                )
                extra_context = f"\n\n[Current trends for reference]\n{trends}\n[End trends]\n"

        prompt = request + extra_context

        # Si no tiene perfil de marca, pedir la información necesaria
        if not self.has_brand(user_id) and intent != "brand_setup":
            prompt = (
                f"{request}\n\n"
                f"Note: I don't have brand information yet. Generate good content but also "
                f"ask at the end what business this is for, so I can personalize future content."
            )

        try:
            response, _ = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                system=system,
                max_tokens=1200,
                enforce_language=True,
            )
            return response
        except Exception as e:
            return f"No pude generar el contenido: {str(e)}"

    def build_calendar_research_query(self, user_id: str, period: str, message: str) -> str | None:
        """Construye la query de investigación de tendencias para el
        calendario de contenido, centrada en la intención REAL del usuario
        (message/ctx.request) — no en una plantilla fija por tipo de negocio.
        El perfil de marca (business_type, platforms) se usa como contexto de
        apoyo, igual que en generate_calendar(). None si ni el mensaje ni el
        perfil aportan información suficiente para una query significativa —
        en ese caso el llamador debe omitir la investigación."""
        brand = self.get_brand(user_id)
        business_type = brand.get("business_type", "")
        platforms = brand.get("platforms", [])

        if not message.strip() and not business_type:
            return None

        parts = [f"current content marketing trends for {period}ly content"]
        if message.strip():
            parts.append(f"related to: {message.strip()}")
        if business_type:
            parts.append(f"for {business_type} businesses")
        if platforms:
            parts.append(f"on {', '.join(platforms)}")
        return " ".join(parts)

    # Presupuesto de caracteres para el grounding de investigación dentro del
    # prompt del calendario: el objetivo es que los hallazgos INFORMEN la
    # sección de tendencias, no que dominen el prompt y desplacen las
    # instrucciones del calendario en sí. No es un límite técnico derivado de
    # un token budget exacto — es una elección deliberada de proporción.
    CALENDAR_RESEARCH_GROUNDING_MAX_CHARS = 1500

    async def generate_calendar(
        self, user_id: str, message: str, period: str = "week",
        research_findings: str | None = None,
    ) -> str:
        """Genera un calendario de contenido para la semana o el mes.

        Fuente de verdad del contexto de negocio, en orden de precedencia:
        1. Lo que el usuario dice en ESTE mensaje — gana si contradice el perfil.
        2. El perfil de marca persistido — rellena lo que este mensaje no menciona.
        3. Si ninguno de los dos aporta información real, el propio modelo pregunta
           al final (mismo principio que generate_content, aplicado aquí por
           primera vez porque esta función nunca recibía el mensaje actual).

        research_findings: texto ya sintetizado de una investigación real
        (opcional) — para que la sección de "tendencias" se base en hallazgos
        verificados en vez de en el conocimiento de entrenamiento del modelo.
        Sin cambio de comportamiento si es None."""
        brand_context = self.build_brand_context(user_id)
        brand = self.get_brand(user_id)
        platforms = brand.get("platforms", ["instagram"])

        # Red única de idioma — ver nota en generate_content (hallazgo 11 jul).
        system = (
            CM_SYSTEM_PROMPT.replace("{brand_context}", brand_context)
            + f"\n\n{ClawPersonality.get_language_rule()}"
        )

        research_block = (
            f"\n\nReal current research findings to ground the trend-based "
            f"content (use these instead of guessing from training "
            f"knowledge):\n{research_findings[:self.CALENDAR_RESEARCH_GROUNDING_MAX_CHARS]}\n"
            if research_findings else ""
        )

        prompt = (
            f"The user's current request: \"{message}\"\n\n"
            f"Create a complete {period}ly content calendar for {', '.join(platforms)}.\n\n"
            f"Business context precedence: anything the user's CURRENT request above states "
            f"about their business ALWAYS wins over the saved brand profile if they conflict. "
            f"Use the saved profile only to fill in details the current request doesn't mention. "
            f"If, after considering both, you still don't have enough to personalize this "
            f"calendar for a specific business, generate solid generic content but end by "
            f"asking what business this is for, so future content can be personalized."
            f"{research_block}\n\n"
            f"For each day include:\n"
            f"- Platform\n"
            f"- Post theme and full caption (ready to publish)\n"
            f"- Hashtags\n"
            f"- Image/video description\n"
            f"- Best posting time\n\n"
            f"Make it varied — mix product posts, educational content, behind the scenes, "
            f"engagement posts, and trend-based content."
        )

        try:
            response, _ = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                system=system,
                max_tokens=2000,
                enforce_language=True,
            )
        except Exception as e:
            return f"No pude generar el calendario: {str(e)}"

        # Observación factual, no "grounding" ni "uso de investigación": el
        # sistema NO puede verificar si un hallazgo se usó bien ni si una
        # cifra copiada está en el contexto correcto -- eso requeriría juicio
        # semántico que este proyecto no confía a un modelo local (ver
        # expediente de trazabilidad de grounding, 2 rechazos previos del
        # auditor por sobreprometer). Lo único que se reporta es un hecho
        # observable y verificable a simple vista: cuántas anclas EXPLÍCITAS
        # (cifras/porcentajes/años, dominios) de la investigación aparecen
        # LITERALMENTE en el texto final.
        if research_findings:
            matched, total = self._count_literal_anchor_matches(response, research_findings)
            if total:
                response += (
                    f"\n\n---\n"
                    f"🔢 *Literal matches with research*\n"
                    f"• Explicit figures/sources from research found literally in this text: {matched}/{total}\n"
                )

        return response

    # Anclas EXPLÍCITAS, cada una 100% mecánica -- ninguna requiere juicio
    # semántico:
    # - Número/porcentaje/año: "340%", "2.3", "2026", "45".
    # - Dominio: "Later.com", "hubspot.com" -- tal como aparece literalmente.
    # Deliberadamente NO incluye nombres propios sin dominio (p. ej.
    # "HubSpot" solo): un heurístico de "palabra capitalizada" reintroduce
    # la ambigüedad ya rechazada (falso positivo con cualquier palabra al
    # inicio de oración). Anclaje incompleto pero honesto.
    _NUMERIC_ANCHOR_RE = re.compile(r"\b\d+(?:[.,]\d+)?%?\b")
    _DOMAIN_ANCHOR_RE = re.compile(r"\b[A-Za-z][\w-]*\.(?:com|org|net|io|co|es)\b", re.IGNORECASE)

    @classmethod
    def _extract_explicit_anchors(cls, text: str) -> set:
        return (
            set(cls._NUMERIC_ANCHOR_RE.findall(text))
            | {m.group(0) for m in cls._DOMAIN_ANCHOR_RE.finditer(text)}
        )

    @classmethod
    def _count_literal_anchor_matches(cls, text: str, research_findings: str) -> tuple[int, int]:
        """Cuenta cuántas anclas EXPLÍCITAS (cifras/porcentajes/años,
        dominios) de la investigación aparecen LITERALMENTE en el texto
        final. No mide uso correcto ni relevancia contextual -- solo
        coincidencia literal, observable y verificable por inspección
        directa. No se llama "grounding" ni "trazabilidad" en ningún texto
        visible: la métrica no puede sostener esas afirmaciones.

        Contrato exacto (precisión pedida por el auditor): esta métrica
        informa ÚNICAMENTE coincidencias literales de anclas explícitas
        presentes tanto en la investigación como en el contenido generado.
        NO evalúa si el contenido utilizó correctamente la investigación
        ni su fidelidad factual."""
        source_anchors = cls._extract_explicit_anchors(research_findings)
        if not source_anchors:
            return 0, 0
        text_anchors = cls._extract_explicit_anchors(text)
        return len(source_anchors & text_anchors), len(source_anchors)
