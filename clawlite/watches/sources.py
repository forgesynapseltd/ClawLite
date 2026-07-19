"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

watches/sources.py — Catálogo plugin-style de FUENTES DE EVENTO para watches

Una fuente de evento sabe responder una sola pregunta:
  "dado este watch, ¿ocurrió algo nuevo que valga la pena notificar?"

Diseño espejo de jobs/executor.py (JobRegistry): añadir una fuente nueva es una
sola llamada a register(); ni el WatchTrigger ni el store necesitan saber qué
fuentes existen. El core se desacopla del catálogo igual que el JobRunner se
desacopla de los tipos de job.

Contrato de una fuente:

    async def check(ctx: EventCheckContext) -> EventCheckResult

  - ctx expone: user_id, params (del watch), state (estado entre ciclos).
  - devuelve EventCheckResult con:
        events    → lista de textos legibles (uno por novedad). Vacía = nada.
        new_state → estado a persistir para el próximo ciclo (ej: ids vistos).
  - NUNCA lanza hacia arriba por fallos esperables (sin auth, API caída):
    devuelve EventCheckResult.empty(state) y deja que el watch siga vivo.

Cada fuente declara también un SCHEMA de params: el conjunto de claves que el
parser en lenguaje natural debe rellenar. Eso permite que el parser del core
sea genérico (no hardcodea campos de Gmail) y que añadir fuentes no toque core.
"""

from dataclasses import dataclass, field
from typing import Awaitable, Callable
from loguru import logger


@dataclass
class EventCheckContext:
    """Lo que recibe una fuente para evaluar un watch."""
    user_id: str
    params: dict
    state: dict


@dataclass
class EventCheckResult:
    """Lo que devuelve una fuente tras evaluar."""
    events: list[str] = field(default_factory=list)
    new_state: dict = field(default_factory=dict)

    @classmethod
    def empty(cls, state: dict) -> "EventCheckResult":
        """Atajo para 'no pasó nada, conserva el estado tal cual'."""
        return cls(events=[], new_state=state)


@dataclass
class EventSource:
    """
    Una fuente registrada. `schema` describe los params que el parser debe
    rellenar (clave → descripción legible para guiar al LLM). `description`
    explica al parser cuándo elegir esta fuente.
    """
    name: str
    description: str
    schema: dict
    check: Callable[[EventCheckContext], Awaitable[EventCheckResult]]


# ── REGISTRO CENTRAL ─────────────────────────────────────────────────────────

class SourceRegistry:
    """
    Registro de fuentes de evento. Espejo de JobRegistry.
    El parser del core lee describe_for_planner() para decidir; el WatchTrigger
    lee get() para ejecutar. Ninguno conoce las fuentes concretas de antemano.
    """

    def __init__(self):
        self._sources: dict[str, EventSource] = {}

    def register(self, source: EventSource):
        if source.name in self._sources:
            logger.warning(f"⚠️ Sobrescribiendo fuente de evento '{source.name}'")
        self._sources[source.name] = source
        logger.info(f"👁️ SourceRegistry: fuente '{source.name}' registrada")

    def get(self, name: str) -> EventSource | None:
        return self._sources.get(name)

    def names(self) -> list[str]:
        return sorted(self._sources.keys())

    def describe_for_planner(self) -> str:
        """
        Texto que el parser LLM usa para elegir fuente y campos. Generado desde
        el catálogo, así que añadir una fuente la expone al parser sin tocar core.
        """
        lines = []
        for name in self.names():
            src = self._sources[name]
            params = ", ".join(f"{k} ({v})" for k, v in src.schema.items()) or "(sin parámetros)"
            lines.append(f'- "{name}": {src.description}\n    params: {params}')
        return "\n".join(lines)


source_registry = SourceRegistry()


# ── FUENTE: gmail_match ───────────────────────────────────────────────────────
# "Avísame cuando llegue un correo de ...". Se apoya en gmail_tool, que ya existe
# y ya gestiona OAuth/refresh. Esta fuente NO toca gmail.py: solo lo consume.

# Cuántos no-leídos inspeccionar por ciclo. Suficiente para no perder correos
# entre ciclos de 15 min sin traer la bandeja entera.
_GMAIL_SCAN_LIMIT = 15
# Cuántos ids recordar como "ya notificados" para no repetir. Acotado para que
# el state no crezca sin límite.
_GMAIL_SEEN_CAP = 200


def _matches(email: dict, params: dict) -> bool:
    """
    ¿Este correo cumple la condición del watch? Todos los filtros presentes deben
    cumplirse (AND). Comparación case-insensitive por substring — robusta frente
    a variaciones de formato del remitente ("Banco ... <notif@...>").
    """
    from_contains = (params.get("from_contains") or "").lower().strip()
    subject_contains = (params.get("subject_contains") or "").lower().strip()

    if not from_contains and not subject_contains:
        # Sin criterios no vigilamos toda la bandeja: el parser debería evitar
        # esto, pero si llega vacío, no disparamos para no spamear.
        return False

    if from_contains and from_contains not in (email.get("from", "") or "").lower():
        return False
    if subject_contains and subject_contains not in (email.get("subject", "") or "").lower():
        return False
    return True


async def _check_gmail_match(ctx: EventCheckContext) -> EventCheckResult:
    from clawlite.agent.tools.gmail import gmail_tool

    seen_ids = list(ctx.state.get("seen_ids", []))

    # Sin autenticación no es un error del watch: simplemente no hay nada que ver.
    if not gmail_tool.is_authenticated():
        return EventCheckResult.empty(ctx.state)

    try:
        emails = gmail_tool.get_unread_emails(
            max_results=_GMAIL_SCAN_LIMIT, scheduled=True, user_id=ctx.user_id
        )
    except Exception as e:
        logger.debug(f"gmail_match: lectura falló, se reintenta el próximo ciclo: {e}")
        return EventCheckResult.empty(ctx.state)

    seen_set = set(seen_ids)
    events = []
    newly_seen = []

    for email in emails:
        eid = email.get("id")
        if not eid or eid in seen_set:
            continue
        if _matches(email, ctx.params):
            sender = email.get("from", "?")
            subject = email.get("subject", "")
            snippet = (email.get("snippet", "") or "").strip()
            line = f"✉️ *{subject}*\n👤 {sender}"
            if snippet:
                line += f"\n_{snippet[:160]}_"
            events.append(line)
        # Marcamos como visto aunque NO haga match: ya lo inspeccionamos y no
        # queremos re-evaluarlo cada ciclo. Si más tarde el usuario lo deja sin
        # leer, no re-dispara — que es justo lo correcto.
        newly_seen.append(eid)

    # Estado nuevo: ids vistos previos + los de este ciclo, recortado al tope.
    merged = seen_ids + [e for e in newly_seen if e not in seen_set]
    if len(merged) > _GMAIL_SEEN_CAP:
        merged = merged[-_GMAIL_SEEN_CAP:]

    return EventCheckResult(events=events, new_state={"seen_ids": merged})


def register_default_sources():
    """
    Registra las fuentes que vienen con ClawLite. Llamar una vez al arrancar.
    Plugins externos pueden registrar más llamando a source_registry.register().
    """
    source_registry.register(EventSource(
        name="gmail_match",
        description=(
            "Vigila la bandeja de Gmail y avisa cuando llega un correo no leído "
            "que coincide con un remitente y/o un asunto. Úsala para "
            '"avísame cuando llegue un correo de ...", "cuando me escriba ...", '
            '"si recibo un email sobre ...".'
        ),
        schema={
            "from_contains": "texto que debe aparecer en el remitente, p. ej. un nombre o dominio",
            "subject_contains": "texto que debe aparecer en el asunto (opcional)",
        },
        check=_check_gmail_match,
    ))
