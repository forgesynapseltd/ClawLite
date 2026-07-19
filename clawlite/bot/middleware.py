"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

bot/middleware.py — Rate limiting y logging de peticiones
Se ejecuta antes de que el mensaje llegue al agente.
"""

import time
from collections import defaultdict
from loguru import logger
from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes
from clawlite.config import config

# Límite: máximo de mensajes por usuario por ventana de tiempo
RATE_LIMIT_MESSAGES = 10
RATE_LIMIT_WINDOW_SECONDS = 60

# Registro de mensajes por usuario: {user_id: [timestamps]}
_user_timestamps: dict[str, list[float]] = defaultdict(list)


def is_rate_limited(user_id: str) -> bool:
    """
    Devuelve True si el usuario ha superado el límite de mensajes.
    Limpia los timestamps fuera de la ventana antes de comprobar.
    """
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS

    # Limpiar timestamps viejos
    _user_timestamps[user_id] = [
        ts for ts in _user_timestamps[user_id] if ts > window_start
    ]

    if len(_user_timestamps[user_id]) >= RATE_LIMIT_MESSAGES:
        return True

    _user_timestamps[user_id].append(now)
    return False


async def owner_only_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Restringe el bot al único dueño configurado (TELEGRAM_OWNER_ID). Sin
    esto, cualquiera que llegue a hablarle al bot es tratado como un
    usuario legítimo más -- incluida la capacidad de pedir ejecución de
    código. Registrar con: app.add_handler(TypeHandler(Update,
    owner_only_middleware), group=-2) -- ANTES que rate_limit_middleware.

    Fail-closed: un Update sin effective_user identificable se rechaza,
    no se deja pasar (un middleware de autorización no debe continuar
    cuando no puede identificar al actor).
    """
    if update.effective_user is None:
        logger.warning("🔒 Update sin effective_user rechazado (fail-closed)")
        raise ApplicationHandlerStop()

    user_id = str(update.effective_user.id)
    if user_id != config.TELEGRAM_OWNER_ID:
        logger.warning(f"🔒 Acceso rechazado: user {user_id} no es el dueño configurado")
        if update.message:
            await update.message.reply_text(
                "🔒 Este bot es privado. No estás autorizado para usarlo."
            )
        # ApplicationHandlerStop, no Exception simple: es lo único que
        # detiene el procesamiento en TODOS los grupos (confirmado en la
        # documentación de la librería) -- una Exception simple no lo
        # habría hecho, el mismo error que tenía rate_limit_middleware.
        raise ApplicationHandlerStop()


async def rate_limit_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Middleware de rate limiting. Corre DESPUÉS de owner_only_middleware --
    solo el dueño autorizado llega hasta acá.
    Registrar con: app.add_handler(TypeHandler(Update, rate_limit_middleware), group=-1)
    """
    if update.effective_user is None:
        logger.warning("🚦 Update sin effective_user rechazado (fail-closed)")
        raise ApplicationHandlerStop()

    user_id = str(update.effective_user.id)

    if is_rate_limited(user_id):
        logger.warning(f"🚦 Rate limit alcanzado para user {user_id}")
        if update.message:
            await update.message.reply_text(
                "⏳ Vas muy rápido. Espera un momento antes de enviar otro mensaje."
            )
        # Corregido: Exception simple NO detenía el procesamiento en otros
        # grupos (confirmado en la documentación de la librería) -- el
        # rate limit nunca habría bloqueado nada realmente, aunque se
        # hubiera registrado.
        raise ApplicationHandlerStop()

    logger.debug(f"📩 Request de user {user_id}")
