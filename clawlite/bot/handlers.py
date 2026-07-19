"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

bot/handlers.py — Comandos y mensajes de Telegram
Maneja texto, documentos, imágenes y URLs.
"""

import os
import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from loguru import logger
from clawlite.config import config
from clawlite.bot.wizard import OnboardingWizard, WizardStep
from clawlite.agent.tools.gmail import gmail_tool
from clawlite.agent.tools.voice import voice_tool
from clawlite.llm.client import llm
from clawlite.llm.json_parser import extract_json
from clawlite.personality.catalog import msg as catalog_msg

_agent = None
_wizard: OnboardingWizard = None
_job_store = None
_watch_store = None

DOWNLOADS_DIR = "./data/downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)


def set_agent(agent):
    global _agent
    _agent = agent


def set_wizard(wizard: OnboardingWizard):
    global _wizard
    _wizard = wizard


def _session_lang(update: Update) -> str | None:
    """Último idioma de turno conocido: los slash-commands nunca pasan por
    el planner, así que delegan en Agent._display_lang() — misma cadena de
    fallback (caché de sesión → user_language persistido → detect_language
    de último recurso) que usa el flujo de email, para no mantener dos
    copias de la misma lógica."""
    if _agent is None or update.effective_user is None:
        return None
    raw = update.effective_message.text if update.effective_message else None
    return _agent._display_lang(str(update.effective_user.id), raw)


def set_job_store(job_store):
    """Inyecta el JobStore para que los comandos /job, /jobs, etc. lo usen."""
    global _job_store
    _job_store = job_store


def set_watch_store(watch_store):
    global _watch_store
    _watch_store = watch_store


TELEGRAM_MAX = 4096

def split_telegram_message(text: str) -> list[str]:
    """Divide un texto en fragmentos que respetan el límite real de
    Telegram (4096 chars). Un solo fragmento si ya entra entero. Reusado
    por send_response (respuestas de turno) y telegram_notifier
    (notificaciones de job) -- ambos canales de salida comparten el
    mismo límite, antes solo uno de los dos lo respetaba."""
    if len(text) <= TELEGRAM_MAX:
        return [text]
    return [text[i:i+TELEGRAM_MAX] for i in range(0, len(text), TELEGRAM_MAX)]

async def send_response(update: Update, text: str):
    """Envía respuestas largas dividiéndolas en mensajes consecutivos."""
    # Guard: nunca intentar enviar vacío. Telegram rechaza mensajes vacíos con
    # BadRequest. Si una ruta (workflow, agente, etc.) devuelve vacío, avisamos
    # al usuario en vez de crashear — el usuario nunca debe quedar sin respuesta.
    if not text or not text.strip():
        logger.warning("send_response recibió texto vacío — enviando aviso al usuario")
        await update.message.reply_text(
            "⚠️ Procesé tu solicitud pero no generé una respuesta de texto. "
            "Intenta reformular, o usa /status para ver el estado del sistema."
        )
        return

    for chunk in split_telegram_message(text):
        try:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(chunk)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if _wizard and _wizard.is_new_user(user_id):
        await _wizard.start(update, context)
    else:
        await update.message.reply_text(
            "👋 Ya estás configurado. ¿En qué te ayudo?\n\n"
            "Puedes escribirme, mandarme un PDF, una imagen o un enlace."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *Comandos disponibles:*\n\n"
        "/start — Bienvenida / reconfigurar\n"
        "/help — Esta lista\n"
        "/status — Estado del sistema\n"
        "/profile — Lo que sé sobre ti\n"
        "/memory clear — Borrar tu historial\n"
        "/brief on | off — Resumen matutino\n\n"
        "*También puedes:*\n"
        "📄 Mandarme un PDF → lo leo y respondo preguntas\n"
        "🌐 Mandarme un enlace → lo visito y resumo\n"
        "🖼 Mandarme una imagen → la describo\n"
        "💬 Escribirme cualquier cosa\n"
        "📧 /gmail — Conectar y gestionar tu correo",
        parse_mode="Markdown"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    provider = config.LLM_PROVIDER.upper()
    await update.message.reply_text(
        f"⚙️ *Estado de ClawLite:*\n\n"
        f"• LLM local: Ollama ({config.OLLAMA_MODEL})\n"
        f"• Proveedor cloud activo: {provider}\n"
        f"• Groq: {'✅' if config.GROQ_API_KEY else '❌'}\n"
        f"• OpenAI: {'✅' if config.OPENAI_API_KEY else '❌'}\n"
        f"• Anthropic: {'✅' if config.ANTHROPIC_API_KEY else '❌'}\n"
        f"• Gmail: {'✅ Conectado' if gmail_tool.is_authenticated() else '❌ No conectado'}\n"
        f"• Búsqueda web: {'✅ Tavily' if config.TAVILY_API_KEY else '❌ Sin clave'}\n"
        f"• Sandbox: {config.SANDBOX_MODE}\n"
        f"• Proactividad: ✅ Activa\n"
        f"• Brief matutino: {'✅ Activo' if config.BRIEF_ENABLED else '❌ Inactivo'}",
        parse_mode="Markdown"
    )


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    summary = _agent.profile.get_summary(user_id)
    await update.message.reply_text(summary, parse_mode="Markdown")


async def olvida_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Soberanía de datos: lista lo que ClawLite sabe del usuario con un botón 🗑️
    para borrar CADA dato individualmente, más un botón para borrar todo.
    El usuario es dueño de su memoria — puede quitar lo que quiera, cuando quiera.
    """
    user_id = str(update.effective_user.id)
    facts = _agent.deep_memory.get_facts_with_ids(user_id)

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    for f in facts[:20]:  # límite sano de botones
        label = f["fact"][:40] + ("…" if len(f["fact"]) > 40 else "")
        rows.append([InlineKeyboardButton(
            f"🗑️ {label}", callback_data=f"forget_{f['id']}"
        )])
    # El reset total se ofrece SIEMPRE: borra memoria + config/identidad y re-onboarda,
    # así que tiene sentido aunque no haya hechos sueltos que listar. Antes, sin hechos
    # se devolvía un mensaje sin botón y el usuario quedaba atrapado (no re-onboardaba).
    rows.append([InlineKeyboardButton(
        "🧹 Borrar TODO lo que sabes de mí (reset)", callback_data="forget_all"
    )])

    if facts:
        text = "🧠 *Esto es lo que sé de ti.* Toca 🗑️ para borrar cualquier dato:"
    else:
        text = (
            "No tengo hechos sueltos guardados sobre ti todavía.\n\n"
            "Si quieres un *reset total* (borra memoria, intereses y configuración, "
            "y vuelvo a configurarte de cero), usa el botón:"
        )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def watches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    if _watch_store is None:
        await update.message.reply_text(
            "El sistema de vigilancias por evento no está disponible ahora mismo."
        )
        return

    watches = _watch_store.list_by_user(user_id)
    if not watches:
        await update.message.reply_text(
            "👁️ No tienes vigilancias activas.\n\n"
            "Puedes crear una de forma natural, por ejemplo:\n"
            "_avísame cuando me llegue un correo de ..._",
            parse_mode="Markdown",
        )
        return

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    for w in watches[:20]:
        label = w["description"][:38] + ("…" if len(w["description"]) > 38 else "")
        status_icon = "🟢" if w["status"] == "active" else "⏸️"
        toggle = "⏸️ Pausar" if w["status"] == "active" else "▶️ Reanudar"
        toggle_action = "pause" if w["status"] == "active" else "resume"
        rows.append([
            InlineKeyboardButton(f"{status_icon} {label}", callback_data=f"watch_noop_{w['id']}"),
        ])
        rows.append([
            InlineKeyboardButton(toggle, callback_data=f"watch_{toggle_action}_{w['id']}"),
            InlineKeyboardButton("🗑️ Eliminar", callback_data=f"watch_cancel_{w['id']}"),
        ])

    await update.message.reply_text(
        "👁️ *Tus vigilancias por evento.* Pausa, reanuda o elimina cualquiera:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def recordatorios_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Soberanía sobre los recordatorios. Lista los pendientes del usuario con un
    botón para cancelar cada uno. Mismo principio que /watches y /olvida: el
    usuario es dueño de lo que ClawLite recuerda por él.
    """
    user_id = str(update.effective_user.id)
    reminders = _agent.deep_memory.get_pending_reminders(user_id)

    if not reminders:
        await update.message.reply_text(
            "⏰ No tienes recordatorios pendientes.\n\n"
            "Puedes crear uno de forma natural, por ejemplo:\n"
            "_recuérdame pagar la renta cada mes_",
            parse_mode="Markdown",
        )
        return

    from datetime import datetime
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    for r in reminders[:20]:
        try:
            when = datetime.fromisoformat(r["remind_at"]).strftime("%d/%m %H:%M")
        except Exception:
            when = r["remind_at"]
        repeat = {"daily": " 🔁 diario", "weekly": " 🔁 semanal", "monthly": " 🔁 mensual"}.get(
            r.get("recurrence", ""), ""
        )
        label = (r["message"][:32] + ("…" if len(r["message"]) > 32 else ""))
        rows.append([
            InlineKeyboardButton(f"⏰ {when} · {label}{repeat}", callback_data=f"rem_noop_{r['id']}"),
        ])
        rows.append([
            InlineKeyboardButton("🗑️ Cancelar", callback_data=f"rem_cancel_{r['id']}"),
        ])

    await update.message.reply_text(
        "⏰ *Tus recordatorios pendientes.* Cancela el que ya no necesites:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args and args[0] == "clear":
        user_id = str(update.effective_user.id)
        # Marcar borrado como pendiente — requiere confirmación
        _agent.deep_memory.set_pattern(user_id, "memory_clear_pending", {"requested": True})
        await update.message.reply_text(
            "⚠️ *¿Seguro que quieres borrar toda tu memoria?*\n\n"
            "Esto eliminará:\n"
            "• Todos los hechos que sé sobre ti\n"
            "• Tus tareas, objetivos e intereses\n"
            "• Tu historial de conversación\n\n"
            "Esta acción no se puede deshacer.\n\n"
            "Responde *sí* para confirmar o *no* para cancelar.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("Uso: /memory clear")


async def brief_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Uso: /brief on | off")
        return
    if args[0] == "on":
        await update.message.reply_text(
            f"✅ Brief matutino activado a las {config.BRIEF_HOUR}:00 {config.BRIEF_TIMEZONE}."
        )
    elif args[0] == "off":
        await update.message.reply_text("❌ Brief matutino desactivado.")
    else:
        await update.message.reply_text("Uso: /brief on | off")


async def voice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    user_id = str(update.effective_user.id)

    if not args:
        current = _agent.deep_memory.get_pattern(user_id, "voice_mode")
        is_on = current.get("enabled", False)
        await update.message.reply_text(
            f"🎙 *Modo voz:* {'✅ Activado' if is_on else '❌ Desactivado'}\n\n"
            "Uso:\n"
            "/voz on — Recibir respuestas en audio + texto\n"
            "/voz off — Solo texto\n\n"
            "_Puedes mandarme audios cuando quieras, los transcribo automáticamente._",
            parse_mode="Markdown"
        )
        return

    if args[0] == "on":
        _agent.deep_memory.set_pattern(user_id, "voice_mode", {"enabled": True})
        await update.message.reply_text("✅ Modo voz activado. Recibirás texto + audio.")
    elif args[0] == "off":
        _agent.deep_memory.set_pattern(user_id, "voice_mode", {"enabled": False})
        await update.message.reply_text("❌ Modo voz desactivado. Solo texto.")
    else:
        await update.message.reply_text("Uso: /voz on | off")


async def modelo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deja al usuario elegir qué modelo de IA usa ClawLite, en lenguaje humano."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    options = [("🏠 Local y privado", "ollama")]
    if config.GROQ_API_KEY:
        options.append(("⚡ Rápido y gratis", "groq"))
    if config.ANTHROPIC_API_KEY:
        options.append(("🎯 Máxima calidad", "anthropic"))
    if config.OPENAI_API_KEY:
        options.append(("🧠 Avanzado (OpenAI)", "openai"))

    pref = _agent.deep_memory.get_pattern("owner", "llm_preference")
    current = pref.get("provider", "") or "ollama"
    human = {"ollama": "🏠 Local y privado", "groq": "⚡ Rápido y gratis",
             "anthropic": "🎯 Máxima calidad", "openai": "🧠 Avanzado (OpenAI)"}

    rows = []
    for label, key in options:
        check = " ✅" if key == current else ""
        rows.append([InlineKeyboardButton(f"{label}{check}", callback_data=f"modelo_set_{key}")])

    await update.message.reply_text(
        f"🤖 *¿Qué modelo quieres que use?*\n\n"
        f"Ahora mismo uso: *{human.get(current, current)}*\n\n"
        f"🏠 Local y privado — tus datos nunca salen de tu equipo\n"
        f"⚡ Rápido y gratis — en la nube, sin coste\n"
        f"🎯 Máxima calidad — en la nube, la mejor respuesta\n\n"
        f"_Toca una opción para cambiar al instante._",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def gmail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args

    if not args:
        await update.message.reply_text(
            "📧 *Gmail y Calendar*\n\n"
            "/gmail inbox — Ver correos no leídos\n"
            "/gmail summary — Resumen inteligente del inbox\n"
            "/gmail calendar — Ver próximos eventos\n\n"
            "_Para conectar tu cuenta: `python -m clawlite.auth`_",
            parse_mode="Markdown"
        )
        return

    if args[0] == "auth":
        await update.message.reply_text(
            "ℹ️ La autenticación de Google se hace desde la terminal:\n\n"
            "`python -m clawlite.auth`\n\n"
            "Ejecuta ese comando, autoriza en el navegador, y vuelve aquí.",
            parse_mode="Markdown"
        )

    elif args[0] == "inbox":
        if not gmail_tool.is_authenticated():
            await update.message.reply_text("Primero conecta tu cuenta con /gmail auth")
            return
        await update.message.reply_text("📧 Obteniendo correos...")
        user_id = str(update.effective_user.id)
        emails = gmail_tool.get_unread_emails(max_results=5, user_id=user_id)
        if not emails:
            await update.message.reply_text("✅ No tienes correos no leídos.")
            return
        _agent.deep_memory.set_pattern(user_id, "email_inbox", {"emails": emails})
        for i, email in enumerate(emails, 1):
            text = (
                f"📧 *[{i}] {email['subject']}*\n"
                f"De: {email['from']}\n"
                f"Fecha: {email['date']}\n\n"
                f"{email['snippet']}"
            )
            try:
                await update.message.reply_text(text, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(text)
        await update.message.reply_text(
            "Para responder escribe: `responde al correo 1`",
            parse_mode="Markdown"
        )

    elif args[0] == "calendar":
        if not gmail_tool.is_authenticated():
            await update.message.reply_text("Ejecuta `python -m clawlite.auth` primero.", parse_mode="Markdown")
            return
        await update.message.reply_text("📅 Obteniendo tu agenda...")
        events = gmail_tool.get_upcoming_events(max_results=10, user_id=str(update.effective_user.id))
        formatted = gmail_tool.format_events(events)
        try:
            await update.message.reply_text(formatted, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(formatted)
        if events:
            await update.message.reply_text(
                "Para crear un evento escribe:\n`crea un evento: reunión mañana a las 3pm`",
                parse_mode="Markdown"
            )

    elif args[0] == "summary":
        if not gmail_tool.is_authenticated():
            await update.message.reply_text("Primero conecta tu cuenta con /gmail auth")
            return
        await update.message.reply_text("📧 Analizando tu inbox...")
        emails = gmail_tool.get_unread_emails(max_results=10, user_id=str(update.effective_user.id))
        formatted = gmail_tool.format_emails_for_summary(emails)
        response, _ = await _agent.handle(
            str(update.effective_user.id),
            f"Resume estos correos de forma concisa, agrupa por urgencia y destaca los más importantes:\n\n{formatted}"
        )
        await send_response(update, response)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = str(update.effective_user.id)

    if data.startswith("wizard_level_"):
        level = data.replace("wizard_level_", "")
        await _wizard.handle_user_level_choice(update, context, level)
    elif data.startswith("wizard_llm_"):
        llm = data.replace("wizard_llm_", "")
        await _wizard.handle_llm_choice(update, context, llm)
    elif data.startswith("wizard_use_"):
        use_case = data.replace("wizard_use_", "")
        await _wizard.handle_use_case(update, context, use_case)
    elif data.startswith("modelo_set_"):
        provider = data.replace("modelo_set_", "")
        _agent.deep_memory.set_pattern("owner", "llm_preference",
                                       {"provider": provider, "fallback": False})
        human = {"ollama": "🏠 Local y privado", "groq": "⚡ Rápido y gratis",
                 "anthropic": "🎯 Máxima calidad", "openai": "🧠 Avanzado (OpenAI)"}
        await query.answer()
        await query.message.reply_text(
            f"✅ Listo. Ahora uso *{human.get(provider, provider)}*.\n\n"
            f"_Si ese modelo no está disponible en algún momento, te aviso para que "
            f"decidas si esperar o cambiar con /modelo._",
            parse_mode="Markdown",
        )
    elif data.startswith("approve_") or data.startswith("reject_"):
        from clawlite.agent.approval import approval_gate
        approved = data.startswith("approve_")
        token = data.split("_", 1)[1]
        resolved = approval_gate.resolve(token, approved)
        await query.answer("Aprobado ✅" if approved else "Cancelado ❌")
        if resolved:
            await query.edit_message_text(
                ("✅ Aprobado — ejecutando." if approved else "❌ Cancelado — no se ejecutó nada."),
            )
        else:
            await query.edit_message_text("Esta solicitud ya expiró o fue respondida.")

    elif data.startswith("draft_yes_") or data.startswith("draft_no_"):
        # Botones de confirmación de drafts persistentes (evento, email, etc.).
        # Reutilizan la MISMA lógica de _handle_approvals: inyectamos un "sí"/"no"
        # como si el usuario lo hubiera escrito. El draft sigue en memoria.
        approved = data.startswith("draft_yes_")
        kind = data.split("_", 2)[2] if data.count("_") >= 2 else ""
        _agent.deep_memory.clear_pattern(user_id, "awaiting_approval")
        # Idioma por ESTADO (catálogo #6): el borrador de evento persiste el idioma
        # del turno que lo creó; kinds que no lo persisten (email, legado) caen al
        # último idioma de turno conocido de la sesión (una sola fuente de verdad,
        # cierra el límite "toast de email en reserva" con el mismo mecanismo).
        _draft_lang = (
            (_agent.deep_memory.get_pattern(user_id, "event_draft") or {}).get("lang")
            or (_agent.deep_memory.get_pattern(user_id, "job_draft") or {}).get("lang")
            or _agent.get_session(user_id, "last_turn_language")
        )
        await query.answer(
            catalog_msg("toast_confirmed", lang=_draft_lang) if approved
            else catalog_msg("toast_cancelled", lang=_draft_lang)
        )
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        if kind == "event":
            # Botones-only (auditoría 1.3): ejecuta directo, SIN inyectar texto ni pasar por
            # listas de palabras. Email/otros siguen el camino legacy hasta su propio Paso.
            result = await _agent.resolve_event_draft(user_id, approved)
        elif kind == "job":
            # Mismo patrón botones-only para el gate de longitud mínima de /job.
            result = await _agent.resolve_job_draft(user_id, approved)
        else:
            injected = "sí" if approved else "no"
            result = await _agent.handle(user_id, injected)
        response = (result[0] if isinstance(result, tuple) else str(result)) if result else ""
        # En un callback de botón update.message es None — el mensaje vive en
        # query.message. Enviamos por ahí, no por send_response (que asume update.message).
        if response:
            try:
                await query.message.reply_text(response, parse_mode="Markdown")
            except Exception:
                await query.message.reply_text(response)

    elif data.startswith("cmp_cloud_"):
        # Botones del disclaimer de soberanía (Agent._needs_sovereignty_disclaimer).
        # Botones-only: ejecuta directo, sin inyectar texto como si el usuario
        # lo hubiera escrito.
        use_cloud = data == "cmp_cloud_yes"
        await query.answer("Usando la nube para esta sesión..." if use_cloud else "Sigo en local...")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        result = await _agent.resolve_comparison_disclaimer(user_id, use_cloud)
        if result:
            response = result[0] if isinstance(result, tuple) else str(result)
            try:
                await query.message.reply_text(response, parse_mode="Markdown")
            except Exception:
                await query.message.reply_text(response)

    elif data.startswith("forget_"):
        # Soberanía de datos: borrado granular de lo que ClawLite sabe del usuario.
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        target = data.replace("forget_", "")
        if target == "all":
            # Reset TOTAL: borra memoria + config/identidad (full=True). A diferencia
            # de /memory clear (que conserva el seed de identidad), este es el botón
            # nuclear: la próxima vez que el usuario escriba, el wizard re-onboarda.
            wiped = _agent._wipe_all_memory(user_id, full=True)
            if wiped:
                await query.answer("Reset total")
                await query.edit_message_text(
                    "🧹 Borré TODO: hechos, memoria, intereses y configuración. "
                    "Empezamos de cero — la próxima vez que me escribas te configuro de nuevo."
                )
            else:
                await query.answer("Bloqueado")
                await query.edit_message_text(
                    "🚫 No pude borrar la memoria: la política de seguridad lo bloqueó."
                )
        else:
            try:
                fact_id = int(target)
                deleted = _agent.deep_memory.delete_fact(user_id, fact_id)
            except ValueError:
                deleted = False
            await query.answer("Borrado ✅" if deleted else "Ya no estaba")
            # Re-renderiza la lista actualizada para seguir borrando
            facts = _agent.deep_memory.get_facts_with_ids(user_id)
            if not facts:
                await query.edit_message_text("🧠 Listo. Ya no guardo ningún hecho sobre ti.")
            else:
                rows = []
                for f in facts[:20]:
                    label = f["fact"][:40] + ("…" if len(f["fact"]) > 40 else "")
                    rows.append([InlineKeyboardButton(
                        f"🗑️ {label}", callback_data=f"forget_{f['id']}"
                    )])
                rows.append([InlineKeyboardButton(
                    "🧹 Borrar TODO lo que sabes de mí", callback_data="forget_all"
                )])
                await query.edit_message_reply_markup(
                    reply_markup=InlineKeyboardMarkup(rows)
                )

    elif data.startswith("watch_"):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        if data.startswith("watch_noop_"):
            await query.answer()
            return

        rest = data[len("watch_"):]
        action, _, raw_id = rest.partition("_")
        try:
            watch_id = int(raw_id)
        except ValueError:
            await query.answer("Acción inválida")
            return

        if _watch_store is None:
            await query.answer("No disponible")
            return

        if action == "pause":
            ok = _watch_store.pause(watch_id, user_id)
            await query.answer("Pausada ⏸️" if ok else "No se pudo")
        elif action == "resume":
            ok = _watch_store.resume(watch_id, user_id)
            await query.answer("Reanudada ▶️" if ok else "No se pudo")
        elif action == "cancel":
            ok = _watch_store.cancel(watch_id, user_id)
            await query.answer("Eliminada 🗑️" if ok else "No se pudo")
        else:
            await query.answer("Acción desconocida")
            return

        watches = _watch_store.list_by_user(user_id)
        if not watches:
            await query.edit_message_text("👁️ Listo. Ya no tienes vigilancias activas.")
            return

        rows = []
        for w in watches[:20]:
            label = w["description"][:38] + ("…" if len(w["description"]) > 38 else "")
            status_icon = "🟢" if w["status"] == "active" else "⏸️"
            toggle = "⏸️ Pausar" if w["status"] == "active" else "▶️ Reanudar"
            toggle_action = "pause" if w["status"] == "active" else "resume"
            rows.append([
                InlineKeyboardButton(f"{status_icon} {label}", callback_data=f"watch_noop_{w['id']}"),
            ])
            rows.append([
                InlineKeyboardButton(toggle, callback_data=f"watch_{toggle_action}_{w['id']}"),
                InlineKeyboardButton("🗑️ Eliminar", callback_data=f"watch_cancel_{w['id']}"),
            ])
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(rows)
        )

    elif data.startswith("rem_"):
        # Cancelar recordatorios desde /recordatorios.
        from datetime import datetime
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        if data.startswith("rem_noop_"):
            await query.answer()
            return

        raw_id = data[len("rem_cancel_"):] if data.startswith("rem_cancel_") else ""
        try:
            rem_id = int(raw_id)
        except ValueError:
            await query.answer("Acción inválida")
            return

        ok = _agent.deep_memory.cancel_reminder(rem_id, user_id)
        await query.answer("Cancelado 🗑️" if ok else "No se pudo")

        reminders = _agent.deep_memory.get_pending_reminders(user_id)
        if not reminders:
            await query.edit_message_text("⏰ Listo. Ya no tienes recordatorios pendientes.")
            return

        rows = []
        for r in reminders[:20]:
            try:
                when = datetime.fromisoformat(r["remind_at"]).strftime("%d/%m %H:%M")
            except Exception:
                when = r["remind_at"]
            repeat = {"daily": " 🔁 diario", "weekly": " 🔁 semanal", "monthly": " 🔁 mensual"}.get(
                r.get("recurrence", ""), ""
            )
            label = (r["message"][:32] + ("…" if len(r["message"]) > 32 else ""))
            rows.append([
                InlineKeyboardButton(f"⏰ {when} · {label}{repeat}", callback_data=f"rem_noop_{r['id']}"),
            ])
            rows.append([
                InlineKeyboardButton("🗑️ Cancelar", callback_data=f"rem_cancel_{r['id']}"),
            ])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))

    elif data == "sugg_yes" or data == "sugg_no":
        # Respuesta a una sugerencia proactiva de la capa de anticipación.
        draft = _agent.deep_memory.get_pattern(user_id, "watch_suggestion_active")
        _agent.deep_memory.clear_pattern(user_id, "watch_suggestion_active")

        if data == "sugg_no" or not draft:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer("Ok, no lo vigilo")
            return

        if _agent.watch_store is None:
            await query.answer("No disponible")
            return

        # Si faltan datos accionables, en vez de crear una vigilancia muerta,
        # pedimos el dato real. Un interceptor capturará la respuesta y creará
        # el watch con ella.
        if draft.get("needs_detail"):
            _agent.deep_memory.set_pattern(user_id, "awaiting_suggestion_detail", draft)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer()
            await query.message.reply_text(
                "Para vigilarlo necesito el dato exacto: ¿cuál es el correo o dominio? "
                "_(por ejemplo: nombre@empresa.com o empresa.com)_",
                parse_mode="Markdown",
            )
            return

        _agent.watch_store.create(
            user_id=user_id,
            description=draft.get("description", "vigilancia sugerida"),
            source=draft["source"],
            params=draft["params"],
        )
        await query.edit_message_reply_markup(reply_markup=None)
        await query.answer("Listo, lo vigilo 👁️")
        await query.message.reply_text(
            "👁️ Hecho. Te aviso en cuanto ocurra. Gestiónalo con `/watches`.",
            parse_mode="Markdown",
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja PDFs y archivos de texto enviados al bot."""
    user_id = str(update.effective_user.id)
    doc = update.message.document
    caption = update.message.caption or ""

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Descargar el archivo
    file = await context.bot.get_file(doc.file_id)
    file_path = f"{DOWNLOADS_DIR}/{doc.file_name}"
    await file.download_to_drive(file_path)

    logger.info(f"📥 Archivo recibido de {user_id}: {doc.file_name}")
    await update.message.reply_text("📄 Leyendo el documento...")

    # Procesar según tipo
    question = caption if caption else "Resume este documento."
    response, used_cloud = await _agent.handle_document(user_id, file_path, doc.file_name, question)

    await send_response(update, response)

    # Guardar documento en memoria multimodal
    if _agent.multimodal:
        await _agent.multimodal.save_document_summary(user_id, file_path, doc.file_name, response)

    # Limpiar archivo temporal
    try:
        os.remove(file_path)
    except Exception:
        pass


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja imágenes enviadas al bot."""
    user_id = str(update.effective_user.id)
    caption = update.message.caption or "¿Qué hay en esta imagen?"

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Descargar la imagen en mejor resolución
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    file_path = f"{DOWNLOADS_DIR}/photo_{user_id}.jpg"
    await file.download_to_drive(file_path)

    logger.info(f"🖼 Imagen recibida de {user_id}")

    response, used_cloud = await _agent.handle_image(user_id, file_path, caption)
    await send_response(update, response)

    # Guardar imagen en memoria multimodal
    if _agent.multimodal:
        await _agent.multimodal.describe_and_save_image(user_id, file_path, context=caption)

    try:
        os.remove(file_path)
    except Exception:
        pass


async def _keep_typing_alive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mantiene vivo el indicador nativo "escribiendo…" de Telegram durante esperas
    largas (research/coding/voz pueden tardar 20-60s). La acción dura ~5s, se
    reenvía cada 4s. Telegram lo localiza solo en el idioma del propio usuario —
    cero texto hardcodeado. Best-effort: fallos de red se ignoran. El caller
    SIEMPRE debe cancelar la tarea (try/finally) al terminar. Compartida entre
    handle_message y handle_voice — una sola implementación, no copias.
    """
    try:
        while True:
            try:
                await context.bot.send_chat_action(
                    chat_id=update.effective_chat.id, action="typing"
                )
            except Exception:
                pass
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja mensajes de voz: los transcribe y procesa como texto normal."""
    user_id = str(update.effective_user.id)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("🎙 Transcribiendo audio...")

    # Descargar el audio
    voice = update.message.voice or update.message.audio
    file = await context.bot.get_file(voice.file_id)
    file_path = f"{DOWNLOADS_DIR}/voice_{user_id}.ogg"
    await file.download_to_drive(file_path)

    logger.info(f"🎙 Audio recibido de {user_id}")

    # Transcribir
    try:
        text, source = await voice_tool.transcribe(file_path)
        if not text:
            await update.message.reply_text("No pude entender el audio. Intenta de nuevo.")
            return

        # Mostrar la transcripción para transparencia
        await update.message.reply_text(f"📝 _Transcripción:_ {text}", parse_mode="Markdown")

        # Procesar como mensaje normal. El procesamiento real (research/calendario/
        # coding) puede tardar bastante — se mantiene vivo el "escribiendo…" nativo
        # de Telegram durante toda la espera (antes solo se mandaba una vez, al
        # principio, y desaparecía a los ~5s dejando al usuario sin señal de que
        # el bot seguía trabajando).
        typing_task = asyncio.create_task(_keep_typing_alive(update, context))
        try:
            response, _ = await _agent.handle(user_id, text)
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
        await send_response(update, response)

        # Guardar audio + transcripción en memoria multimodal
        if _agent.multimodal:
            await _agent.multimodal.save_audio_with_transcript(user_id, file_path, text)

        # Si el modo voz está activo, mandar también audio
        await _maybe_send_voice_response(update, user_id, response)

    except Exception as e:
        logger.error(f"❌ Error procesando audio: {e}")
        await update.message.reply_text(f"Error procesando el audio: {str(e)}")
    finally:
        try:
            os.remove(file_path)
        except Exception:
            pass


async def _maybe_send_voice_response(update: Update, user_id: str, response: str):
    """Si el modo voz está activo, sintetiza y envía audio de la respuesta."""
    voice_mode = _agent.deep_memory.get_pattern(user_id, "voice_mode")
    if not voice_mode.get("enabled"):
        return

    audio_path = f"{DOWNLOADS_DIR}/response_{user_id}.mp3"
    lang = voice_tool.detect_lang(response)

    if await voice_tool.synthesize(response, audio_path, lang=lang):
        try:
            with open(audio_path, "rb") as f:
                await update.message.reply_voice(voice=f)
        except Exception as e:
            logger.error(f"❌ Error enviando audio: {e}")
        finally:
            try:
                os.remove(audio_path)
            except Exception:
                pass


async def _reply_unknown_command(update: Update):
    """Respuesta determinista a un intento de comando que ningún handler atendió.
    El mensaje NUNCA llega al modelo: un LLM no puede 'confirmar' la ejecución
    de un comando (incidente real del 6 jul: '/voz on' → 'Hecho, el modo de voz
    está activado', falso). El texto vive en el catálogo multilingüe (#6 fase 1);
    el idioma es el ÚLTIMO idioma de turno conocido de la sesión (estado que core
    persiste al clasificar cada turno real) — NO se re-detecta sobre el propio
    comando: py3langid probado ruidoso en textos cortos ('hola qué tal' → 'fr',
    token inventado → 'pt'; batería 7 jul). Sesión virgen sin ningún turno
    clasificado aún → reserva del catálogo (inglés), por diseño."""
    logger.info("🚧 Comando no reconocido — respuesta determinista, sin pasar al modelo")
    lang = (
        _agent.get_session(str(update.effective_user.id), "last_turn_language")
        if _agent and update.effective_user else None
    )
    await update.message.reply_text(catalog_msg("unknown_command", lang=lang))


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catch-all para comandos CON entidad de Telegram pero no registrados
    (p.ej. /foobar). Sin esto, PTB los ignora en silencio total."""
    await _reply_unknown_command(update)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_message = update.message.text

    # Wizard routing
    if _wizard:
        state = _wizard.get_state(user_id)
        step = state.get("step")

        if step == WizardStep.API_KEY.value:
            await _wizard.handle_api_key(update, context)
            return
        elif step == WizardStep.NAME.value:
            await _wizard.handle_name(update, context)
            return
        elif step == WizardStep.INTERESTS.value:
            await _wizard.handle_interests(update, context)
            return

        if _wizard.is_new_user(user_id):
            await _wizard.start(update, context)
            return

    # Gate determinista anti-fabricación (cara b del bug de fabricación, 6 jul):
    # un texto que EMPIEZA con "/" es un intento de comando por convención del
    # protocolo Telegram. Si llegó hasta aquí es que ningún CommandHandler lo
    # atendió (p.ej. sin entidad de comando — el caso real "/voz on" que el
    # modelo "confirmó" sin ejecutar). JAMÁS se le pasa al LLM: respuesta
    # determinista. "/" es un marcador de protocolo, no una lista de palabras.
    if (user_message or "").lstrip().startswith("/"):
        await _reply_unknown_command(update)
        return

    logger.info(f"📨 Mensaje de {user_id}: {user_message[:80]}...")

    # Registrar callback de progreso para que el CodingAgent pueda notificar al usuario
    # si el planner decide que es una coding_request. Si no lo es, este callback se ignora.
    async def coding_progress(text: str):
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception:
            try:
                await update.message.reply_text(text)
            except Exception:
                pass

    _agent.set_session(user_id, "coding_progress_callback", coding_progress)

    # Feedback durante esperas largas (research/coding tardan 20-40s): se mantiene
    # vivo el "escribiendo…" nativo de Telegram mientras el agente trabaja (ver
    # _keep_typing_alive — compartida con handle_voice, no duplicada).
    typing_task = asyncio.create_task(_keep_typing_alive(update, context))
    try:
        response, used_cloud = await _agent.handle(user_id, user_message)
    except Exception as e:
        logger.opt(exception=e).error(f"❌ Error no manejado procesando mensaje de {user_id}: {e}")
        try:
            await update.message.reply_text(
                "⚠️ Ocurrió un error inesperado procesando tu mensaje. Intenta de nuevo."
            )
        except Exception:
            pass
        return
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass
        _agent.clear_session(user_id, "coding_progress_callback")

    # Si el flujo fue una coding_request, los archivos están en session state
    coding_result = _agent.get_session(user_id, "coding_last_result")
    if coding_result:
        _agent.clear_session(user_id, "coding_last_result")
        await send_response(update, response)
        await _send_coding_files(update, user_id, coding_result)
        await _maybe_send_voice_response(update, user_id, response)
        return

    await send_response(update, response)
    await _maybe_send_voice_response(update, user_id, response)
    await _maybe_send_approval_buttons(update, user_id)
    await _maybe_send_comparison_buttons(update, user_id)
    await _maybe_send_suggestion(update, user_id)


async def _maybe_send_approval_buttons(update: Update, user_id: str):
    """
    Si ESTE turno creó o editó un borrador esperando aprobación, manda botones
    ✅/❌. Robustece la confirmación: un tap en vez de adivinar si el texto
    del usuario contaba como "sí". El matching de texto sigue como fallback.

    Se dispara SOLO si el borrador es de este turno (approval_just_created,
    marca de sesión que core.py resetea al inicio de cada handle() y activa en
    los puntos donde crea/edita un borrador) — no basta con que exista un
    'awaiting_approval' persistente, porque un borrador de turnos anteriores que
    el usuario dejó sin resolver no debe reaparecer pegado a cada respuesta
    posterior sin relación. El botón original sigue vivo y pulsable en Telegram
    aunque no se reenvíe el recordatorio.
    """
    if not _agent.get_session(user_id, "approval_just_created"):
        return
    pending = _agent.deep_memory.get_pattern(user_id, "awaiting_approval")
    if not pending:
        return
    kind = pending.get("kind", "")
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅", callback_data=f"draft_yes_{kind}"),
        InlineKeyboardButton("❌", callback_data=f"draft_no_{kind}"),
    ]])
    try:
        await update.message.reply_text("👆", reply_markup=keyboard)
    except Exception as e:
        logger.debug(f"No se pudieron enviar botones de aprobación: {e}")


async def _maybe_send_comparison_buttons(update: Update, user_id: str):
    """
    Si ESTE turno pausó una comparación con el disclaimer de soberanía, manda
    los botones de decisión. Mismo patrón que _maybe_send_approval_buttons:
    señal de sesión (comparison_disclaimer_just_created, que core.py resetea al
    inicio de cada handle() y activa solo al crear el disclaimer) — no basta con
    que exista un 'pending_comparison' persistente, porque una comparación de
    turnos anteriores que el usuario dejó sin resolver no debe reaparecer
    pegada a respuestas posteriores sin relación. Deliberadamente NO se detecta
    por el TEXTO de la respuesta (frágil: se rompe si el texto cambia de
    idioma o redacción) — se detecta por estado, igual que el resto del bot.
    """
    if not _agent.get_session(user_id, "comparison_disclaimer_just_created"):
        return
    pending = _agent.deep_memory.get_pattern(user_id, "pending_comparison")
    if not pending:
        return
    lang = _session_lang(update)
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(catalog_msg("cloud_yes_button", lang=lang), callback_data="cmp_cloud_yes"),
        InlineKeyboardButton(catalog_msg("cloud_no_button", lang=lang), callback_data="cmp_cloud_no"),
    ]])
    try:
        await update.message.reply_text("👆", reply_markup=keyboard)
    except Exception as e:
        logger.debug(f"No se pudieron enviar botones de comparación: {e}")


async def _maybe_send_suggestion(update: Update, user_id: str):
    """
    Capa de anticipación: si el suggester detectó una oportunidad de automatización
    no pedida, la ofrece con un botón Sí/No. La detección corre en background
    durante handle(); aquí solo recogemos el draft si quedó listo a tiempo.
    """
    draft = _agent.deep_memory.get_pattern(user_id, "watch_suggestion_draft")
    if not draft:
        return
    # Consumir el draft: se ofrece una sola vez.
    _agent.deep_memory.clear_pattern(user_id, "watch_suggestion_draft")

    offer = draft.get("offer", "")
    if not offer:
        return

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Sí, vigílalo", callback_data="sugg_yes"),
        InlineKeyboardButton("❌ No", callback_data="sugg_no"),
    ]])
    try:
        await update.message.reply_text(f"💡 {offer}", reply_markup=keyboard)
        # Guardar el draft activo para que el callback sepa qué crear.
        _agent.deep_memory.set_pattern(user_id, "watch_suggestion_active", draft)
    except Exception as e:
        logger.debug(f"No se pudo enviar sugerencia proactiva: {e}")


async def _send_coding_files(update: Update, user_id: str, result: dict):
    """Envía los archivos generados por CodingAgent como documentos descargables."""
    import tempfile
    import shutil

    files = result.get("files", {})
    if not files:
        return

    tmp_dir = tempfile.mkdtemp(prefix=f"clawlite_send_{user_id}_")
    try:
        for file_path, content in files.items():
            if not isinstance(content, str):
                continue

            safe_name = file_path.replace("/", "_").replace("\\", "_")
            local_path = os.path.join(tmp_dir, safe_name)
            try:
                with open(local_path, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                logger.debug(f"Could not write {file_path}: {e}")
                continue

            try:
                with open(local_path, "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename=file_path,
                        caption=f"📄 `{file_path}`",
                        parse_mode="Markdown",
                    )
            except Exception as e:
                logger.debug(f"Could not send {file_path} as document: {e}")
                if len(content) <= 3500:
                    try:
                        await update.message.reply_text(
                            f"📄 *{file_path}*\n```\n{content}\n```",
                            parse_mode="Markdown",
                        )
                    except Exception:
                        pass

        execution_log = result.get("execution_log")
        if execution_log:
            preview = execution_log[:1500]
            try:
                await update.message.reply_text(
                    f"📋 *Output de la ejecución:*\n```\n{preview}\n```",
                    parse_mode="Markdown",
                )
            except Exception:
                await update.message.reply_text(f"Output de la ejecución:\n{preview}")
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# COMANDOS DE JOBS ASÍNCRONOS
# ─────────────────────────────────────────────────────────────────────────────


async def _infer_job_type(request: str) -> str:
    """
    Clasifica el tipo de job (`/job` explícito) en UNA de tres categorías
    cerradas — juicio semántico agnóstico de idioma, no listas de palabras
    (antes _JOB_TYPE_KEYWORDS solo reconocía es/en). Mismo patrón ya validado
    en el resto del proyecto (is_news, user_asserts, weekday, confirmar/
    cancelar). Fail-safe: ante cualquier duda o fallo → "research" — mismo
    default seguro y genérico que ya tenía la versión anterior.
    """
    prompt = (
        "Classify what kind of background task this request is asking for. "
        "Choose EXACTLY ONE:\n"
        '- "coding": building/writing code, an app, a script, a tool, a function\n'
        '- "brand_calendar": a social media content calendar or content plan\n'
        '- "research": anything else — deep research, analysis, investigation (the default)\n\n'
        f'Request:\n"""\n{request}\n"""\n\n'
        'Return ONLY JSON: {"job_type": "coding"|"brand_calendar"|"research"}'
    )
    try:
        raw, _ = await llm.complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20, structured=True, task_type="factcheck",
        )
        data = extract_json(raw, expect="object")
        job_type = (data or {}).get("job_type", "research")
        return job_type if job_type in ("coding", "brand_calendar", "research") else "research"
    except Exception as e:
        logger.debug(f"Clasificación de tipo de job falló → 'research' (fail-safe): {e}")
        return "research"


# Piso de palabras para pedir confirmación antes de crear el job (agnóstico de
# idioma, puro conteo — no interpreta el contenido). Causa raíz real: "/job
# status abc" (typo de "/job_status abc", 2 palabras) disparó el pipeline
# COMPLETO (sandbox Docker + CodingAgent) sobre una frase sin sentido. En vez
# de adivinar si es un typo (juicio nuevo del modelo, prohibido para esta
# decisión), se pide confirmación por botón para cualquier petición corta —
# el costo de un tap extra en un job legítimo corto es mucho menor que el de
# disparar el pipeline caro sobre ruido.
_JOB_MIN_WORDS = 3


async def job_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /job <descripción> — crea un job asíncrono.
    Ejemplo: /job investiga el mercado de drones de carga en LATAM
    """
    if _job_store is None:
        await update.message.reply_text(catalog_msg("jobs_not_initialized", lang=_session_lang(update)))
        return

    user_id = str(update.effective_user.id)
    request = " ".join(context.args).strip() if context.args else ""

    if not request:
        await update.message.reply_text(
            catalog_msg("job_usage", lang=_session_lang(update)),
            parse_mode="Markdown",
        )
        return

    job_type = await _infer_job_type(request)
    # Título corto y descriptivo para mostrar en listados
    title = request[:80] + ("…" if len(request) > 80 else "")

    if len(request.split()) < _JOB_MIN_WORDS:
        lang = _session_lang(update)
        _agent.deep_memory.set_pattern(user_id, "job_draft", {
            "title": title, "request": request, "job_type": job_type, "lang": lang,
        })
        _agent.deep_memory.set_pattern(user_id, "awaiting_approval", {"kind": "job"})
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅", callback_data="draft_yes_job"),
            InlineKeyboardButton("❌", callback_data="draft_no_job"),
        ]])
        await update.message.reply_text(
            catalog_msg("job_confirm_short", lang=lang, request=request, job_type=job_type),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        return

    job_id = _job_store.create(
        user_id=user_id,
        title=title,
        request=request,
        job_type=job_type,
    )

    await update.message.reply_text(
        f"✅ `#{job_id}` [{job_type}]\n"
        f"_{title}_\n\n"
        f"⏳ `queued`\n"
        f"`/job_status {job_id}` · `/jobs`",
        parse_mode="Markdown",
    )


async def jobs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/jobs — lista los jobs del usuario (activos + últimos 5 finalizados)."""
    if _job_store is None:
        await update.message.reply_text(catalog_msg("jobs_not_initialized", lang=_session_lang(update)))
        return

    user_id = str(update.effective_user.id)
    jobs = _job_store.list_by_user(user_id, finished_limit=5)

    if not jobs:
        await update.message.reply_text(
            catalog_msg("jobs_empty", lang=_session_lang(update)),
            parse_mode="Markdown",
        )
        return

    status_emoji = {
        "queued": "⏳",
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
        "cancelled": "🚫",
    }

    lines = ["📋\n"]
    for job in jobs:
        emoji = status_emoji.get(job["status"], "•")
        title = job["title"][:60]
        lines.append(f"{emoji} `#{job['id']}` [{job['job_type']}] — {title}")

        if job["status"] == "running" and job.get("progress"):
            lines.append(f"     _{job['progress'][:80]}_")

    lines.append("\n`/job_status <id>`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def job_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/job_status <id> — muestra estado y progreso de un job."""
    if _job_store is None:
        await update.message.reply_text(catalog_msg("jobs_not_initialized", lang=_session_lang(update)))
        return

    if not context.args:
        await update.message.reply_text(catalog_msg("job_status_usage", lang=_session_lang(update)), parse_mode="Markdown")
        return

    try:
        job_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(catalog_msg("job_id_not_numeric", lang=_session_lang(update)))
        return

    user_id = str(update.effective_user.id)
    job = _job_store.get(job_id)

    if not job or job["user_id"] != user_id:
        await update.message.reply_text(catalog_msg("job_not_found", lang=_session_lang(update), job_id=job_id))
        return

    status_emoji = {
        "queued": "⏳", "running": "🔄", "completed": "✅",
        "failed": "❌", "cancelled": "🚫",
    }
    emoji = status_emoji.get(job["status"], "•")

    lines = [
        f"{emoji} *#{job['id']}* [{job['job_type']}]",
        f"_{job['title']}_\n",
        f"`{job['status']}`",
        f"🕐 {job['created_at']}",
    ]

    if job.get("started_at"):
        lines.append(f"▶️ {job['started_at']}")
    if job.get("finished_at"):
        lines.append(f"🏁 {job['finished_at']}")
    if job.get("progress"):
        lines.append(f"\n📊 _{job['progress']}_")
    if job["status"] == "completed" and job.get("result"):
        # Truncamos el resultado en el status; ya se envió completo al terminar
        preview = job["result"][:1500]
        lines.append(f"\n📄 {preview}")
        if len(job["result"]) > 1500:
            lines.append("…")
    if job["status"] == "failed" and job.get("error"):
        lines.append(f"\n⚠️ `{job['error'][:500]}`")

    try:
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception:
        # Fallback sin Markdown por si hay caracteres conflictivos en el contenido
        await update.message.reply_text("\n".join(lines))


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /cancel <id> — cancela un job en estado queued.
    No funciona con jobs running (decisión arquitectónica para evitar
    sandboxes Docker huérfanos y llamadas LLM medio respondidas).
    """
    if _job_store is None:
        await update.message.reply_text(catalog_msg("jobs_not_initialized", lang=_session_lang(update)))
        return

    if not context.args:
        await update.message.reply_text(catalog_msg("job_cancel_usage", lang=_session_lang(update)), parse_mode="Markdown")
        return

    try:
        job_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(catalog_msg("job_id_not_numeric", lang=_session_lang(update)))
        return

    user_id = str(update.effective_user.id)
    job = _job_store.get(job_id)

    if not job or job["user_id"] != user_id:
        await update.message.reply_text(catalog_msg("job_not_found", lang=_session_lang(update), job_id=job_id))
        return

    if job["status"] != "queued":
        await update.message.reply_text(
            catalog_msg("job_cancel_not_queued", lang=_session_lang(update),
                        job_id=job_id, status=job["status"]),
            parse_mode="Markdown",
        )
        return

    cancelled = _job_store.cancel(job_id, user_id)
    if cancelled:
        await update.message.reply_text(
            catalog_msg("job_cancelled", lang=_session_lang(update), job_id=job_id)
        )
    else:
        await update.message.reply_text(
            catalog_msg("job_cancel_failed", lang=_session_lang(update), job_id=job_id)
        )


async def job_files_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /job_files <id> — descarga los archivos generados por un job de tipo coding.
    Reutiliza el patrón de _send_coding_files que ya está validado.
    """
    if _job_store is None:
        await update.message.reply_text(catalog_msg("jobs_not_initialized", lang=_session_lang(update)))
        return

    if not context.args:
        await update.message.reply_text(catalog_msg("job_files_usage", lang=_session_lang(update)), parse_mode="Markdown")
        return

    try:
        job_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(catalog_msg("job_id_not_numeric", lang=_session_lang(update)))
        return

    user_id = str(update.effective_user.id)
    job = _job_store.get(job_id)

    if not job or job["user_id"] != user_id:
        await update.message.reply_text(catalog_msg("job_not_found", lang=_session_lang(update), job_id=job_id))
        return

    if job["status"] != "completed":
        await update.message.reply_text(
            catalog_msg("job_files_not_completed", lang=_session_lang(update),
                        job_id=job_id, status=job["status"]),
            parse_mode="Markdown",
        )
        return

    if job["job_type"] != "coding":
        await update.message.reply_text(
            catalog_msg("job_files_wrong_type", lang=_session_lang(update),
                        job_id=job_id, job_type=job["job_type"]),
            parse_mode="Markdown",
        )
        return

    # Para jobs de coding, el resultado completo (incluyendo files) se persiste
    # en el campo result pero también en la config_json si lo guardamos así.
    # Estrategia: serializamos el dict completo de result en config_json al completar
    # el job. Aquí lo recuperamos.
    import json
    config_data = job.get("config", {})
    coding_result = config_data.get("coding_result", {})

    if not coding_result or not coding_result.get("files"):
        await update.message.reply_text(
            catalog_msg("job_files_none", lang=_session_lang(update), job_id=job_id)
        )
        return

    await _send_coding_files(update, user_id, coding_result)
