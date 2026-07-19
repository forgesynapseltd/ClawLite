"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

proactivity/triggers.py — Los triggers proactivos incluyendo Reminder
"""

from datetime import datetime, timedelta
from loguru import logger
from clawlite.memory.profile import DeepMemory, UserProfile
from clawlite.agent.tools.search import search_tool
from clawlite.llm.client import llm
from clawlite.personality.voice import ClawPersonality


class TriggerResult:
    def __init__(self, should_fire: bool, message: str = "", trigger_type: str = ""):
        self.should_fire = should_fire
        self.message = message
        self.trigger_type = trigger_type


class ReminderTrigger:
    """
    El más importante — evalúa recordatorios pendientes cuya hora llegó.
    Tiene prioridad sobre todos los demás triggers.
    """

    async def evaluate(self, user_id: str, profile: UserProfile) -> TriggerResult:
        due = profile.memory.get_all_due_reminders()
        user_due = [r for r in due if r["user_id"] == user_id]

        if not user_due:
            return TriggerResult(False)

        reminder = user_due[0]
        profile.memory.resolve_due_reminder(reminder["id"])

        message = f"⏰ *{reminder['message']}*"
        logger.info(f"⏰ ReminderTrigger fired for {user_id}: {reminder['message']}")

        return TriggerResult(
            should_fire=True,
            message=message,
            trigger_type="reminder"
        )


class TemporalTrigger:
    """
    Brief matutino completo.
    Delega toda la lógica a BriefGenerator — un solo pipeline para
    trigger automático y solicitud manual.
    """

    def __init__(self, hour: int = 8):
        self.hour = hour

    async def evaluate(self, user_id: str, profile: UserProfile) -> TriggerResult:
        from clawlite.agent.tools.brief import brief_generator

        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        if now.hour != self.hour:
            return TriggerResult(False)

        last_fired = profile.memory.get_pattern(user_id, "brief_last_fired")
        if last_fired.get("date") == today:
            return TriggerResult(False)

        profile.memory.set_pattern(user_id, "brief_last_fired", {"date": today})
        logger.info(f"📰 TemporalTrigger fired for {user_id}")

        brief = await brief_generator.generate(user_id, profile)

        return TriggerResult(
            should_fire=True,
            message=brief,
            trigger_type="morning_brief"
        )


class ContextualTrigger:
    """Tareas pendientes — máximo una vez cada 3 días por usuario."""

    STALE_DAYS = 3
    SILENCE_DAYS = 3

    async def evaluate(self, user_id: str, profile: UserProfile) -> TriggerResult:
        # Rate limiting — no repetir antes de SILENCE_DAYS
        pattern = profile.memory.get_pattern(user_id, "contextual_last_fired")
        if pattern:
            last = datetime.fromisoformat(pattern.get("last_fired", "2000-01-01"))
            if (datetime.now() - last).days < self.SILENCE_DAYS:
                return TriggerResult(False)

        stale = profile.memory.get_stale_tasks(user_id, days=self.STALE_DAYS)
        if not stale:
            return TriggerResult(False)

        # Guardar fecha antes de disparar
        profile.memory.set_pattern(user_id, "contextual_last_fired", {
            "last_fired": datetime.now().isoformat()
        })

        task_list = "\n".join(f"- {t['task']}" for t in stale[:2])
        user_context = profile.build_context(user_id)

        prompt = f"""{ClawPersonality.get_system_prompt(user_context)}

The user has tasks pending for more than {self.STALE_DAYS} days. Write a brief, direct check-in.
Mention 1-2 specific tasks. Ask one clear question. Don't be preachy.

Pending tasks:\n{task_list}"""

        response, source = await llm.complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
        )

        logger.info(f"📋 ContextualTrigger fired for {user_id}")
        return TriggerResult(
            should_fire=True,
            message=ClawPersonality.format_proactive_message(response, source),
            trigger_type="stale_tasks"
        )


class PatternTrigger:
    """Objetivos que el usuario mencionó pero no ha retomado."""

    GOAL_SILENCE_DAYS = 5

    async def evaluate(self, user_id: str, profile: UserProfile) -> TriggerResult:
        goals = profile.memory.get_active_goals(user_id)
        if not goals:
            return TriggerResult(False)

        pattern = profile.memory.get_pattern(user_id, "goal_reminder")
        if pattern:
            last = datetime.fromisoformat(pattern.get("last_reminded", "2000-01-01"))
            if (datetime.now() - last).days < self.GOAL_SILENCE_DAYS:
                return TriggerResult(False)

        user_context = profile.build_context(user_id)
        goal_list = "\n".join(f"- {g}" for g in goals[:2])

        prompt = f"""{ClawPersonality.get_system_prompt(user_context)}

The user mentioned these goals but hasn't talked about them in a while.
Write one brief, direct message. Not motivational-poster style. Just a real question.

Goals:\n{goal_list}"""

        response, source = await llm.complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
        )

        profile.memory.set_pattern(user_id, "goal_reminder", {
            "last_reminded": datetime.now().isoformat()
        })

        logger.info(f"🎯 PatternTrigger fired for {user_id}")
        return TriggerResult(
            should_fire=True,
            message=ClawPersonality.format_proactive_message(response, source),
            trigger_type="goal_check"
        )


class MemoryConnectionTrigger:
    """
    Recuperación contextual proactiva. Conecta puntos de la memoria: toma una
    entidad reciente del grafo de conocimiento (persona, proyecto, tema que el
    usuario mencionó) y, si tiene relaciones, escribe un check-in natural sobre
    ella antes de que el usuario pregunte. Es lo que diferencia a ClawLite: no
    solo recuerda cuando le preguntas, sino que retoma hilos por iniciativa.

    Va al final de la cadena de triggers (menor prioridad que recordatorios,
    brief, tareas y objetivos) y tiene rate-limiting propio para no ser intrusivo.
    """

    SILENCE_DAYS = 4

    async def evaluate(self, user_id: str, profile: UserProfile) -> TriggerResult:
        # Rate limiting temporal — no más de un check-in cada SILENCE_DAYS
        pattern = profile.memory.get_pattern(user_id, "memory_connection_last_fired")
        last_entity = ""
        if pattern:
            last = datetime.fromisoformat(pattern.get("last_fired", "2000-01-01"))
            if (datetime.now() - last).days < self.SILENCE_DAYS:
                return TriggerResult(False)
            last_entity = pattern.get("entity", "")

        # Acceso al grafo con el db_path que el profile ya tiene — sin inyecciones nuevas
        try:
            from clawlite.agents.memory.knowledge_graph import KnowledgeGraph
            graph = KnowledgeGraph(profile.memory.db_path)
        except Exception:
            return TriggerResult(False)

        entities = graph.get_all_entities(user_id)  # ya vienen ordenadas por recencia
        if not entities:
            return TriggerResult(False)

        # Busca una entidad reciente CON relaciones que NO sea la última sobre la
        # que ya escribimos — así nunca repite el mismo hilo dos veces seguidas.
        # Umbral de sustancia: solo retomar un hilo si la entidad tiene SUFICIENTES
        # relaciones reales en el grafo. Con una sola relación no hay nada genuino
        # que decir y el modelo termina inventando para rellenar. Exigir un mínimo
        # hace que el trigger hable solo cuando de verdad hay un hilo — esa es la
        # cura de raíz de la alucinación, no la instrucción del prompt.
        MIN_RELATIONS = 3
        chosen = None
        related = []
        for ent in entities[:8]:
            if ent["entity"] == last_entity:
                continue
            rels = graph.get_related_entities(user_id, ent["entity"], limit=5)
            if len(rels) >= MIN_RELATIONS:
                chosen = ent
                related = rels
                break

        if not chosen:
            return TriggerResult(False)

        # Construye el contexto de relaciones para el LLM
        rel_lines = "\n".join(
            f"- {chosen['entity']} {r['relation']} {r['entity']}" for r in related[:4]
        )
        user_context = profile.build_context(user_id)

        prompt = f"""{ClawPersonality.get_system_prompt(user_context)}

The user mentioned "{chosen['entity']}" recently. Below is what the knowledge graph ACTUALLY
records about it. Write ONE brief, natural check-in that picks up this thread.

STRICT RULES:
- Use ONLY the recorded facts below. Do NOT invent past conversations, do NOT say you "talked
  last week" or reference events not in the data. You have NO memory of specific past chats
  beyond what is recorded here.
- One genuine question or observation about the topic — not a summary, not a list.
- Follow the language rule in the user context above: reply in the user's language.

What the knowledge graph records:
{rel_lines}"""

        response, source = await llm.complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
        )

        profile.memory.set_pattern(user_id, "memory_connection_last_fired", {
            "last_fired": datetime.now().isoformat(),
            "entity": chosen["entity"],
        })

        logger.info(f"🕸️ MemoryConnectionTrigger fired for {user_id} (entity: {chosen['entity']})")
        return TriggerResult(
            should_fire=True,
            message=ClawPersonality.format_proactive_message(response, source),
            trigger_type="memory_connection"
        )


class WatchTrigger:
    """
    Cron en lenguaje natural por evento. Evalúa los watches activos del usuario
    contra sus fuentes de evento (watches/sources.py) y, si alguna reporta
    novedades, las notifica.

    Es plugin-style respecto a los otros triggers: clase nueva, no toca las cinco
    existentes. Y es plugin-style respecto a las fuentes: no conoce Gmail ni
    ninguna fuente concreta — itera el catálogo registrado. Añadir una fuente
    nueva (calendario, archivo, webhook) no toca este trigger.

    Por qué vive en el ProactivityEngine y no en el JobRunner: vigilar
    condiciones es polling ligero periódico, exactamente el latido que el engine
    ya ejecuta cada ciclo recorriendo usuarios. Los jobs son one-shot (queued→
    done); forzar un job recurrente exigiría re-encolarlo tras cada corrida, que
    sería un parche. El engine es el hogar natural de la vigilancia.

    El WatchStore se instancia con el db_path que el profile ya tiene —mismo
    patrón que MemoryConnectionTrigger con el KnowledgeGraph— para no requerir
    inyección nueva desde main.py.

    Devuelve un único TriggerResult agregando los disparos de todos los watches
    en un mensaje (el engine espera un resultado por trigger). Cada watch
    persiste su propio estado de evaluación, así que el agregado no pierde
    granularidad.
    """

    async def evaluate(self, user_id: str, profile: UserProfile) -> TriggerResult:
        try:
            from clawlite.watches.store import WatchStore
            from clawlite.watches.sources import (
                source_registry,
                EventCheckContext,
            )
            store = WatchStore(profile.memory.db_path)
        except Exception:
            return TriggerResult(False)

        watches = store.get_active_for_user(user_id)
        if not watches:
            return TriggerResult(False)

        blocks = []
        for w in watches:
            source = source_registry.get(w["source"])
            if source is None:
                # Fuente desconocida (catálogo cambió): no rompemos el ciclo.
                logger.warning(f"👁️ Watch #{w['id']} usa fuente desconocida '{w['source']}'")
                continue

            ctx = EventCheckContext(
                user_id=user_id,
                params=w["params"],
                state=w["state"],
            )

            try:
                result = await source.check(ctx)
            except Exception as e:
                logger.error(f"❌ Watch #{w['id']} [{w['source']}] falló: {e}")
                continue

            # Persistir estado y sello de chequeo siempre — disparó o no.
            store.update_state(w["id"], result.new_state)
            store.mark_checked(w["id"])

            if result.events:
                store.mark_fired(w["id"])
                header = f"👁️ *{w['description']}*"
                body = "\n\n".join(result.events)
                blocks.append(f"{header}\n\n{body}")
                logger.info(f"👁️ WatchTrigger fired #{w['id']} for {user_id} "
                            f"({len(result.events)} evento/s)")

        if not blocks:
            return TriggerResult(False)

        return TriggerResult(
            should_fire=True,
            message="\n\n———\n\n".join(blocks),
            trigger_type="event_watch",
        )
