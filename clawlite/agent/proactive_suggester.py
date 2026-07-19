"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agent/proactive_suggester.py — La capa de anticipación ("JARVIS-layer")

Qué hace: cuando hablas normal, detecta oportunidades de automatización que NO
pediste y te las ofrece — sin ejecutarlas. Iniciativa + consentimiento humano.

Diseño (los principios que sostienen cada decisión de este archivo):

  • CONTEXTO, NO FRASE SUELTA. El detector juzga la conversación reciente, no el
    último mensaje aislado. "no responde" suelto es ambiguo; con los turnos
    previos deja de serlo. La evidencia es lo que vuelve fiable un juicio que es,
    por naturaleza, probabilístico. Más contexto → mejor juicio.

  • UN SOLO JUICIO SEMÁNTICO, EN EL LLM. Distinguir "amazon" (remitente concreto)
    de "mi jefe" (referencia a aclarar) NO se puede hacer con reglas de texto sin
    hardcodear listas de roles por idioma. Es un juicio semántico — y va donde
    corresponde: el mismo análisis del LLM decide si el dato es accionable o si
    hay que pedirlo. Cero heurística frágil, cero listas, agnóstico de idioma.

  • SIN HARDCODE. El catálogo de fuentes se lee en vivo (source_registry). Los
    únicos ejemplos del prompt son neutros con "..."; no hay frases por idioma.
    El modelo razona en el idioma del usuario.

  • DETERMINISTA EN EL FLUJO. Corre awaited desde el wrapper handle(), así el
    draft está listo antes de que la capa Telegram lo recoja — la oferta no
    compite con la respuesta (sin condición de carrera).

  • SEGURO POR DEFECTO. Anti-spam (cooldown), anti-repetición, anti-duplicado
    (no ofrece lo que ya vigilas). Ante la duda, calla: el coste de molestar es
    mayor que el de callar una oportunidad válida. Un fallo del suggester NUNCA
    afecta la conversación.

Estados que persiste (vía deep_memory.set_pattern):
  watch_suggestion_draft     → oportunidad lista para que Telegram la ofrezca
  watch_suggestion_active    → oferta mostrada, esperando Sí/No (lo pone Telegram)
  awaiting_suggestion_detail → el usuario dijo Sí pero falta el dato concreto
  proactive_suggestion_last  → anti-spam (firma + timestamp de la última oferta)
"""

import json
from datetime import datetime
from loguru import logger
from clawlite.llm.client import llm


# Horas mínimas entre dos sugerencias a un mismo usuario. Invisible hasta que
# aporta: un asistente que ofrece cada pocos minutos es ruido.
_SUGGESTION_COOLDOWN_HOURS = 6

# Confianza mínima (0..1) para ofrecer. Por debajo, silencio.
_MIN_CONFIDENCE = 0.7

# Cuántos turnos de contexto mirar. Suficiente para dar evidencia sin diluir.
_CONTEXT_TURNS = 6

_DRAFT_PATTERN = "watch_suggestion_draft"
_LAST_PATTERN = "proactive_suggestion_last"


class ProactiveSuggester:
    """
    Detecta oportunidades en el turno del usuario leído EN CONTEXTO y, si hay una
    clara, deja un draft para que la capa Telegram la ofrezca con un botón.

    No inyecta dependencias nuevas: recibe deep_memory (patrones/cooldown) y
    watch_store (anti-duplicado y, tras el Sí, creación). El contexto reciente se
    le pasa como argumento desde quien ya lo tiene (el wrapper handle), para no
    acoplar el suggester al MemoryStore.
    """

    def __init__(self, deep_memory, watch_store):
        self.deep_memory = deep_memory
        self.watch_store = watch_store

    async def detect(self, user_id: str, message: str, recent: list[dict] | None = None):
        """
        Analiza el mensaje en su contexto. Si la conversación revela una
        oportunidad clara y mapeable a una fuente real, persiste un draft.
        Fire-and-forget desde fuera: no devuelve nada, no lanza hacia arriba.
        """
        try:
            if self.watch_store is None:
                return
            if not self._cooldown_ok(user_id):
                return

            suggestion = await self._analyze(message, recent or [])
            if suggestion is None:
                return

            # No ofrecer algo que el usuario YA vigila (misma fuente + params).
            # Desacopla del planner: no necesita saber qué pasó en este turno,
            # solo mira el estado real de watches.
            if self._already_watched(user_id, suggestion):
                return

            # No reofrecer lo mismo que ya ofrecimos la última vez.
            last = self.deep_memory.get_pattern(user_id, _LAST_PATTERN) or {}
            if last.get("signature") == suggestion["signature"]:
                return

            self.deep_memory.set_pattern(user_id, _DRAFT_PATTERN, suggestion)
            self.deep_memory.set_pattern(user_id, _LAST_PATTERN, {
                "signature": suggestion["signature"],
                "at": datetime.now().isoformat(),
            })
            logger.info(
                f"💡 Sugerencia proactiva preparada para {user_id}: "
                f"{suggestion['signature']} (needs_detail={suggestion['needs_detail']})"
            )

        except Exception as e:
            logger.debug(f"ProactiveSuggester.detect falló silenciosamente: {e}")

    # ── INTERNO ──────────────────────────────────────────────────────────────

    def _cooldown_ok(self, user_id: str) -> bool:
        last = self.deep_memory.get_pattern(user_id, _LAST_PATTERN) or {}
        ts = last.get("at")
        if not ts:
            return True
        try:
            elapsed_h = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 3600
        except Exception:
            return True
        return elapsed_h >= _SUGGESTION_COOLDOWN_HOURS

    def _already_watched(self, user_id: str, suggestion: dict) -> bool:
        try:
            existing = self.watch_store.get_active_for_user(user_id)
        except Exception:
            return False
        for w in existing:
            if w.get("source") == suggestion["source"] and w.get("params") == suggestion["params"]:
                return True
        return False

    async def _analyze(self, message: str, recent: list[dict]):
        """
        Un solo juicio del LLM resuelve TODO: ¿hay oportunidad?, ¿con qué fuente y
        params?, y crucialmente ¿el dato es un remitente CONCRETO o una referencia
        VAGA que hay que aclarar? Ese último punto (needs_detail) lo decide el
        modelo, no una regla de texto — porque "amazon" vs "mi jefe" es semántica,
        no sintaxis. Devuelve el draft estructurado o None.
        """
        from clawlite.watches.sources import source_registry

        catalog = source_registry.describe_for_planner()
        if not catalog:
            return None

        # Contexto conversacional: los turnos previos son la evidencia. Se incluye
        # el turno actual al final para que el modelo vea la situación completa.
        history = ""
        for turn in recent[-_CONTEXT_TURNS:]:
            who = "User" if turn.get("role") == "user" else "Assistant"
            history += f"{who}: {turn.get('content', '')}\n"

        prompt = (
            "You read a user's CONVERSATION and judge whether the latest message reveals a chance "
            "to offer USEFUL automation they did NOT explicitly ask for. Judge the situation in "
            "context, not the last line alone — the prior turns are the evidence. Reason in the "
            "user's own language.\n\n"
            "An opportunity exists ONLY when the user expresses they are WAITING FOR or EXPECTING a "
            "specific FUTURE EXTERNAL EVENT that one of the watch sources below can detect for them "
            "(for example: an email they are expecting from someone). The signal is anticipation of "
            "an external event — NOT the topic of the message.\n"
            "There is NO opportunity (return opportunity=false) when the latest message is: a request "
            "for YOU to do something now (search, investigate, write, explain, tell a joke), a "
            "question, small talk, or a message that merely mentions a topic. Asking you to look "
            "something up is you doing work now — it is NOT a reason to watch their email. Most "
            "conversations have NO opportunity; when unsure, return false.\n\n"
            "Available watch sources:\n"
            f"{catalog}\n\n"
            "Recent conversation:\n"
            f"{history}\n"
            "Latest message:\n"
            f"{message}\n\n"
            "Decide three things:\n"
            "1) Is there a clear automation opportunity? (opportunity true/false)\n"
            "2) Which source and what params does the message provide?\n"
            "3) Is the target CONCRETE (an actual email address or domain we can match on) or only "
            "a VAGUE reference (a role or relationship like a boss, a provider, an institution) with "
            "no concrete address yet? If vague, set needs_detail=true — the app will ask the user "
            "for the exact address before creating anything. Still fill params with the words used.\n\n"
            'Respond ONLY with JSON:\n'
            '{"opportunity": true|false, "confidence": 0.0-1.0, "source": "<source_name>", '
            '"params": {...}, "needs_detail": true|false, '
            '"label": "<very short noun phrase naming WHAT is watched, in the user\'s language, '
            'e.g. the sender or topic — NOT a question>", '
            '"offer": "<one short friendly question offering it, in the user\'s language>"}\n\n'
            "Examples (neutral, illustrative only):\n"
            '- waiting on a concrete sender -> {"opportunity": true, "confidence": 0.85, '
            '"source": "gmail_match", "params": {"from_contains": "..."}, "needs_detail": false, '
            '"offer": "..."}\n'
            '- waiting on a vague role/relationship -> {"opportunity": true, "confidence": 0.8, '
            '"source": "gmail_match", "params": {"from_contains": "..."}, "needs_detail": true, '
            '"offer": "..."}\n'
            '- no automation angle -> {"opportunity": false, "confidence": 0.0, "source": "", '
            '"params": {}, "needs_detail": false, "offer": ""}'
        )

        try:
            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=240,
                structured=True,
            )
        except Exception:
            return None

        data = _safe_json(raw)
        if not data or not data.get("opportunity"):
            return None
        if float(data.get("confidence", 0)) < _MIN_CONFIDENCE:
            return None

        source_name = data.get("source", "")
        source = source_registry.get(source_name)
        if source is None:
            return None

        # Quedarnos solo con params que la fuente declara, no vacíos y GROUNDED:
        # el prompt ya manda "fill params with the words used" — aquí se hace
        # cumplir en código. Un valor que no aparece en la conversación que el
        # modelo leyó es inventado (p.ej. el "correo de ninguno") y se descarta.
        # Fail-closed: sin params reales, no hay sugerencia (filosofía de §35).
        evidence = _fold(f"{history}\n{message}")
        params = {
            k: v for k, v in (data.get("params") or {}).items()
            if k in source.schema and v and _fold(str(v)) in evidence
        }
        if not params:
            return None

        offer = (data.get("offer") or "").strip()
        if not offer:
            return None

        needs_detail = bool(data.get("needs_detail", False))

        # La descripción nombra QUÉ se vigila (para /watches); el offer es la
        # pregunta. Son cosas distintas: mezclarlas ensucia la lista de watches.
        label = (data.get("label") or "").strip()
        description = label if label else offer

        # Firma estable para anti-repetición (fuente + params).
        signature = source_name + "|" + json.dumps(params, sort_keys=True, ensure_ascii=False)

        return {
            "source": source_name,
            "params": params,
            "offer": offer,
            "description": description,
            "signature": signature,
            "needs_detail": needs_detail,
        }


def _fold(text: str) -> str:
    """Normaliza para comparar: minúsculas y sin acentos (NFKD). Agnóstico de idioma."""
    import unicodedata
    t = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in t if not unicodedata.combining(c))


def _safe_json(raw):
    """Parsea JSON tolerando que el LLM lo envuelva en texto o fences."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(s[start:end + 1])
    except Exception:
        return None
