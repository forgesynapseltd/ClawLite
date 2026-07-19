"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

proactivity/engine.py — Motor de proactividad
"""

import sqlite3
from loguru import logger
from telegram import Bot
from clawlite.memory.profile import DeepMemory, UserProfile
from clawlite.proactivity.triggers import (
    ReminderTrigger,
    TemporalTrigger,
    ContextualTrigger,
    PatternTrigger,
    MemoryConnectionTrigger,
    WatchTrigger,
)
from clawlite.config import config
from clawlite.governance import action_guard, Mandate, MandateOrigin


class ProactivityEngine:

    def __init__(self, bot: Bot, deep_memory: DeepMemory):
        self.bot = bot
        self.deep_memory = deep_memory
        self.profile = UserProfile(deep_memory)

        # ReminderTrigger siempre primero — es el más crítico.
        # WatchTrigger antes de la conexión de memoria: un evento real del mundo
        # (llegó el correo vigilado) es más accionable que un check-in de memoria.
        # MemoryConnectionTrigger al final — menor prioridad, recuperación proactiva.
        self.triggers = [
            ReminderTrigger(),
            TemporalTrigger(hour=config.BRIEF_HOUR),
            ContextualTrigger(),
            PatternTrigger(),
            WatchTrigger(),
            MemoryConnectionTrigger(),
        ]

        logger.info("🚀 ProactivityEngine iniciado")

    async def run_cycle(self):
        users = self._get_active_users()
        if not users:
            return

        logger.debug(f"🔄 Proactivity cycle — {len(users)} users")

        for user_id in users:
            self._consolidate_memory(user_id)
            await self._update_history_summary(user_id)
            await self._evaluate_for_user(user_id)

    def _consolidate_memory(self, user_id: str):
        """Auto-organización de memoria (memU, incremento mínimo): dispara en cada ciclo
        la consolidación de hechos —función ya existente pero hasta ahora SIN caller— para
        que el backlog de duplicados acumulados se limpie solo. Determinista: consolidate_facts
        solo deduplica por forma normalizada, nunca fusiona hechos distintos, y ya loguea lo
        que elimina. Aislado: un fallo aquí nunca tumba el ciclo de proactividad."""
        try:
            self.deep_memory.consolidate_facts(user_id)
        except Exception as e:
            logger.error(f"❌ Consolidación de memoria falló para {user_id}: {e}")

    async def _update_history_summary(self, user_id: str):
        """memU-02: dispara en cada ciclo la actualización del resumen rodante
        de historial —función ya existente (DeepMemory.update_history_summary)
        evalúa internamente su propio umbral de mensajes nuevos, nunca el
        tiempo. Aislado: un fallo aquí nunca tumba el ciclo de proactividad."""
        try:
            await self.deep_memory.update_history_summary(user_id)
        except Exception as e:
            logger.error(f"❌ Actualización de resumen de historial falló para {user_id}: {e}")

    async def _evaluate_for_user(self, user_id: str):
        for trigger in self.triggers:
            try:
                result = await trigger.evaluate(user_id, self.profile)
                if result.should_fire and result.message:
                    await self._send_proactive(user_id, result.message)
                    logger.info(f"📤 [{result.trigger_type}] → {user_id}")
                    # Reminders: pueden ser múltiples, no hacemos break
                    if result.trigger_type != "reminder":
                        break
            except Exception as e:
                logger.error(f"❌ Trigger error for {user_id}: {e}")

    async def _send_proactive(self, user_id: str, message: str):
        # ── Compuerta de gobernanza (ActionGuard) ──────────────────────────────
        # El agente inicia el mensaje: nace de un trigger que el usuario autorizó
        # antes (mandato SYSTEM_SCHEDULED). Mediado y auditado; el contenido externo
        # nunca podría originar este envío (default-deny por origen).
        decision = action_guard.authorize(
            "send_proactive_message",
            Mandate(
                origin=MandateOrigin.SYSTEM_SCHEDULED,
                user_id=str(user_id),
                summary=(message or "")[:200],
            ),
        )
        if not decision.allowed:
            logger.warning(f"🛡️ Mensaje proactivo DENEGADO por el kernel: {decision.reason}")
            return
        # Mismo fix que telegram_notifier (jobs/executor): mensajes
        # proactivos también pueden exceder el límite de Telegram.
        from clawlite.bot.handlers import split_telegram_message
        try:
            for chunk in split_telegram_message(message):
                await self.bot.send_message(
                    chat_id=int(user_id),
                    text=chunk,
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"❌ Failed to send to {user_id}: {e}")

    def _get_active_users(self) -> list[str]:
        try:
            with sqlite3.connect(self.deep_memory.db_path) as conn:
                rows = conn.execute(
                    "SELECT DISTINCT user_id FROM messages"
                ).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []
