"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

bot/wizard.py — Wizard de onboarding en Telegram
Guía al usuario paso a paso sin tocar una terminal.
Todo ocurre dentro del chat.
"""

import sqlite3
from enum import Enum
from loguru import logger
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from clawlite.memory.profile import DeepMemory
from clawlite.config import config


class WizardStep(Enum):
    WELCOME      = "welcome"
    USER_LEVEL   = "user_level"
    LLM_CHOICE   = "llm_choice"
    API_KEY      = "api_key"
    NAME         = "name"
    USE_CASE     = "use_case"
    INTERESTS    = "interests"
    TIMEZONE     = "timezone"
    DONE         = "done"


# Estado en memoria por usuario — no persiste entre reinicios (suficiente para onboarding)
_wizard_state: dict[str, dict] = {}


class OnboardingWizard:
    """
    Wizard de onboarding completo dentro de Telegram.
    Sin terminal. Sin archivos. Sin documentación.
    """

    LLM_OPTIONS = {
        "groq":      ("⚡ Groq (gratis, rápido)", "https://console.groq.com"),
        "openai":    ("🟢 OpenAI (GPT-4o)", "https://platform.openai.com/api-keys"),
        "anthropic": ("🟣 Anthropic (Claude)", "https://console.anthropic.com"),
        "xai":       ("⚫ xAI (Grok)", "https://console.x.ai"),
        "ollama":    ("🏠 Ollama (100% local, gratis)", None),
    }

    # N1 = simple/nube, N2 = local-first, N3 = avanzado (adenda producto 6 jul).
    # El segundo valor es el proveedor recomendado (None = sin preset, elección
    # libre). Único lugar donde vive esta tabla — wizard.py es dueño del default.
    USER_LEVELS = {
        "N1": ("📱 Simple — quiero que funcione ya, no me importa la nube", "groq"),
        "N2": ("🏠 Local-first — prefiero que mis datos no salgan de mi máquina", "ollama"),
        "N3": ("⚙️ Avanzado — elijo yo mismo cada cosa", None),
    }

    USE_CASES = {
        "research":   "📚 Investigación y estudio",
        "business":   "💼 Negocio o emprendimiento",
        "social":     "📱 Redes sociales y contenido",
        "developer":  "💻 Desarrollo de software",
        "personal":   "🏠 Uso personal y productividad",
    }

    def __init__(self, deep_memory: DeepMemory):
        self.memory = deep_memory

    def is_new_user(self, user_id: str) -> bool:
        """Detecta si el usuario nunca ha completado el onboarding."""
        try:
            with sqlite3.connect(self.memory.db_path) as conn:
                row = conn.execute(
                    "SELECT data FROM patterns WHERE user_id = ? AND pattern = 'onboarding_complete'",
                    (user_id,)
                ).fetchone()
            return row is None
        except Exception:
            return True

    def mark_complete(self, user_id: str):
        self.memory.set_pattern(user_id, "onboarding_complete", {"completed": True})

    def get_state(self, user_id: str) -> dict:
        return _wizard_state.get(user_id, {"step": WizardStep.WELCOME.value})

    def set_state(self, user_id: str, state: dict):
        _wizard_state[user_id] = state

    # ── PASO 1: Bienvenida ──────────────────────────────────────────────────

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        first_name = update.effective_user.first_name or "ahí"

        self.set_state(user_id, {"step": WizardStep.USER_LEVEL.value})

        await update.message.reply_text(
            f"👋 Hola {first_name}, soy ClawLite.\n\n"
            f"Soy tu asistente personal que vive en Telegram. "
            f"Busco en internet, recuerdo todo lo que me cuentas, "
            f"y te escribo primero cuando tengo algo útil que decirte.\n\n"
            f"Te configuro en 2 minutos. ¿Empezamos?\n\n"
            f"*Primer paso: ¿cómo prefieres usarme?*",
            parse_mode="Markdown",
            reply_markup=self._user_level_keyboard()
        )

    def _user_level_keyboard(self) -> InlineKeyboardMarkup:
        buttons = [
            [InlineKeyboardButton(label, callback_data=f"wizard_level_{key}")]
            for key, (label, _) in self.USER_LEVELS.items()
        ]
        return InlineKeyboardMarkup(buttons)

    def _llm_keyboard(self) -> InlineKeyboardMarkup:
        buttons = [
            [InlineKeyboardButton(label, callback_data=f"wizard_llm_{key}")]
            for key, (label, _) in self.LLM_OPTIONS.items()
        ]
        return InlineKeyboardMarkup(buttons)

    # Texto de recomendación por nivel — SOLO texto, nunca acción automática.
    # El usuario siempre elige de _llm_keyboard() con las 4 opciones visibles.
    LEVEL_RECOMMENDATION_TEXT = {
        "N1": "📱 Simple. Te recomiendo Groq (gratis, rápido) — pero elegí el que prefieras:",
        "N2": "🏠 Local-first. Te recomiendo Ollama (100% local) — pero elegí el que prefieras:",
        "N3": "⚙️ A tu ritmo. ¿Con qué modelo quieres que funcione?",
    }

    # ── PASO 2: Nivel de usuario ────────────────────────────────────────────

    async def handle_user_level_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE, level: str):
        user_id = str(update.effective_user.id)
        state = self.get_state(user_id)
        state["level"] = level
        state["step"] = WizardStep.LLM_CHOICE.value
        self.set_state(user_id, state)
        # Mismo patrón de persistencia que onboarding_complete (por user_id) —
        # distinto del caso especial de llm_preference, que es global de
        # instancia porque get_task_cascade()/get_user_llm_preference() no
        # reciben user_id.
        self.memory.set_pattern(user_id, "user_level", {"level": level})

        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            self.LEVEL_RECOMMENDATION_TEXT[level],
            parse_mode="Markdown",
            reply_markup=self._llm_keyboard()
        )

    # ── PASO 3: Elección de LLM ─────────────────────────────────────────────

    async def handle_llm_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE, llm: str):
        user_id = str(update.effective_user.id)
        state = self.get_state(user_id)
        state["llm"] = llm
        state["step"] = WizardStep.API_KEY.value
        self.set_state(user_id, state)

        # Persistir la elección como preferencia REAL (global de instancia) para que
        # la cascada la use. Sin esto la elección del wizard era decorativa: se
        # guardaba solo en estado volátil y get_task_cascade nunca la veía.
        self.memory.set_pattern("owner", "llm_preference",
                                {"provider": llm, "fallback": False})

        label, url = self.LLM_OPTIONS[llm]

        await update.callback_query.answer()

        if llm == "ollama":
            # Ollama no necesita API key
            state["api_key"] = "ollama_local"
            state["step"] = WizardStep.NAME.value
            self.set_state(user_id, state)
            await update.callback_query.message.reply_text(
                f"✅ Perfecto — Ollama local, zero cloud.\n\n"
                f"Asegúrate de que Ollama esté corriendo en tu máquina.\n\n"
                f"*¿Cómo te llamas?*",
                parse_mode="Markdown"
            )
        else:
            await update.callback_query.message.reply_text(
                f"✅ {label} seleccionado.\n\n"
                f"Necesito tu API key. Puedes obtenerla aquí:\n"
                f"👉 {url}\n\n"
                f"Cuando la tengas, pégala aquí. "
                f"_Solo yo la veo — no la comparto con nadie._",
                parse_mode="Markdown"
            )

    # ── PASO 3: API Key ─────────────────────────────────────────────────────

    async def handle_api_key(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        state = self.get_state(user_id)

        api_key = update.message.text.strip()

        # Validación básica de formato
        if len(api_key) < 20 or " " in api_key:
            await update.message.reply_text(
                "Eso no parece una API key válida. "
                "Suelen ser cadenas largas sin espacios. Inténtalo de nuevo."
            )
            return

        # Borrar el mensaje con la key por seguridad
        try:
            await update.message.delete()
        except Exception:
            pass

        state["api_key"] = api_key
        state["step"] = WizardStep.NAME.value
        self.set_state(user_id, state)

        # Guardar la key en la BÓVEDA CIFRADA, no en un pattern en claro. El mapa
        # proveedor→nombre de credencial es el mismo que config usa para leerla.
        _provider_key = {
            "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY", "xai": "XAI_API_KEY",
        }.get(state["llm"])
        if _provider_key:
            config._vault.set(_provider_key, api_key)
        logger.info(f"🔑 API key cifrada en la bóveda para {state['llm']}")

        await update.message.reply_text(
            "✅ API key guardada.\n\n*¿Cómo te llamas?*",
            parse_mode="Markdown"
        )

    # ── PASO 4: Nombre ──────────────────────────────────────────────────────

    async def handle_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        state = self.get_state(user_id)

        name = update.message.text.strip().split()[-1]
        state["name"] = name
        state["step"] = WizardStep.USE_CASE.value
        self.set_state(user_id, state)

        # Guardar en perfil
        self.memory.add_fact(user_id, f"Me llamo {name}")

        await update.message.reply_text(
            f"Perfecto, {name}. *¿Para qué lo vas a usar principalmente?*",
            parse_mode="Markdown",
            reply_markup=self._use_case_keyboard()
        )

    def _use_case_keyboard(self) -> InlineKeyboardMarkup:
        buttons = [
            [InlineKeyboardButton(label, callback_data=f"wizard_use_{key}")]
            for key, label in self.USE_CASES.items()
        ]
        return InlineKeyboardMarkup(buttons)

    # ── PASO 5: Caso de uso ─────────────────────────────────────────────────

    async def handle_use_case(self, update: Update, context: ContextTypes.DEFAULT_TYPE, use_case: str):
        user_id = str(update.effective_user.id)
        state = self.get_state(user_id)
        state["use_case"] = use_case
        state["step"] = WizardStep.INTERESTS.value
        self.set_state(user_id, state)

        label = self.USE_CASES[use_case]
        # El caso de uso es CONFIG del asistente, NO un hecho personal del usuario: no se
        # guarda como fact (contaminaba el perfil e inducía confabulación). Se persiste en
        # el snapshot onboarding_profile (campo 'use_case'), más abajo.

        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            f"✅ {label}.\n\n"
            f"*¿Sobre qué temas quieres que te mantenga informado?*\n\n"
            f"Escríbelos separados por comas. Ejemplo:\n"
            f"_tecnología, emprendimiento, recetas, fútbol_",
            parse_mode="Markdown"
        )

    # ── PASO 6: Intereses ───────────────────────────────────────────────────

    async def handle_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        state = self.get_state(user_id)

        raw = update.message.text.strip()
        interests = [i.strip() for i in raw.split(",") if i.strip()]

        for interest in interests[:10]:  # máximo 10
            self.memory.add_or_boost_interest(user_id, interest, boost=2.0)

        state["interests"] = interests
        state["step"] = WizardStep.DONE.value
        self.set_state(user_id, state)

        # Snapshot del SEED de identidad (mismo contenido que se insertó como facts e
        # intereses durante el onboarding). Es config preservable: sobrevive a /memory
        # clear y se re-planta (reseed_from_onboarding), para que la IA siga conociendo
        # lo básico del usuario tras un borrado de memoria, sin re-lanzar el wizard.
        # 'facts' contiene SOLO hechos personales reales (nombre). El caso de uso es
        # CONFIG del asistente, no un hecho de vida: va en su propio campo 'use_case'
        # para que reseed NO lo replante como fact ni build_context lo exponga como
        # "Known fact" (era la causa de la confabulación).
        seed_facts = []
        if state.get("name"):
            seed_facts.append(f"Me llamo {state['name']}")
        snapshot = {
            "facts": seed_facts,
            "interests": interests[:10],
        }
        if state.get("use_case") in self.USE_CASES:
            snapshot["use_case"] = self.USE_CASES[state["use_case"]]
        self.memory.set_pattern(user_id, "onboarding_profile", snapshot)

        self.mark_complete(user_id)

        name = state.get("name", "")
        interests_str = ", ".join(interests[:5])

        await update.message.reply_text(
            f"✅ *Todo listo, {name}.*\n\n"
            f"Esto es lo que sé de ti:\n"
            f"• Modelo: {self.LLM_OPTIONS[state['llm']][0]}\n"
            f"• Intereses: {interests_str}\n\n"
            f"Ya puedes escribirme cualquier cosa. "
            f"Te respondo, busco en internet cuando hace falta, "
            f"y te escribiré primero cuando tenga algo útil.\n\n"
            f"*Comandos útiles:*\n"
            f"/profile — lo que sé de ti\n"
            f"/brief on — resumen matutino activado\n"
            f"/help — todos los comandos\n\n"
            f"¿En qué te ayudo ahora?",
            parse_mode="Markdown"
        )

        logger.info(f"✅ Onboarding completado para {user_id} ({name})")
