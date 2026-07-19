"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agent/approval.py — Approval Gate
Antes de ejecutar una acción crítica (enviar correo, ejecutar código, gastar,
borrar), ClawLite pide aprobación humana explícita en Telegram. El usuario ve
EXACTAMENTE qué se va a hacer y aprueba o cancela con un botón.

Esto es seguridad visible: a diferencia de los agentes con acceso total por
defecto, ClawLite nunca ejecuta una acción de alto impacto sin consentimiento.
"""

import asyncio
import uuid
from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


class ApprovalGate:
    """
    Intercepta acciones críticas y pide confirmación humana en Telegram
    antes de ejecutarlas. Punto único de verdad de qué requiere aprobación.

    Uso:
        approved = await approval_gate.request(
            user_id, bot, "enviar este correo a juan@x.com", details="Asunto: ..."
        )
        if approved:
            ... ejecuta ...
    """

    def __init__(self):
        # token -> asyncio.Future que se resuelve con True/False
        self._pending: dict[str, asyncio.Future] = {}

    async def request(
        self,
        user_id: str,
        bot,
        action_summary: str,
        details: str = "",
        timeout: int = 120,
    ) -> bool:
        """
        Pide aprobación al usuario. Devuelve True si aprueba, False si cancela
        o si expira el tiempo. Bloquea hasta recibir respuesta o timeout.
        """
        token = uuid.uuid4().hex[:16]
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[token] = future

        text = f"🔐 *Necesito tu aprobación*\n\n{action_summary}"
        if details:
            text += f"\n\n{details}"
        text += "\n\n_Nada se ejecuta hasta que apruebes._"

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Aprobar", callback_data=f"approve_{token}"),
            InlineKeyboardButton("❌ Cancelar", callback_data=f"reject_{token}"),
        ]])

        try:
            await bot.send_message(
                chat_id=int(user_id),
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"❌ ApprovalGate no pudo pedir aprobación: {e}")
            self._pending.pop(token, None)
            return False

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.info(f"⏲️ Aprobación expiró (token {token})")
            return False
        finally:
            self._pending.pop(token, None)

    def resolve(self, token: str, approved: bool) -> bool:
        """
        Resuelve una aprobación pendiente desde el callback de Telegram.
        Devuelve True si el token existía (era una aprobación real).
        """
        future = self._pending.get(token)
        if future and not future.done():
            future.set_result(approved)
            return True
        return False


approval_gate = ApprovalGate()