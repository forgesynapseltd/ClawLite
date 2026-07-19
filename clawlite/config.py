"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

config.py — Configuración centralizada de ClawLite
Todas las variables de entorno pasan por aquí. Ningún otro módulo
lee el .env directamente.
"""

import os
from dotenv import load_dotenv
from loguru import logger
from clawlite.security.vault import CredentialVault

load_dotenv()

# Nombres de las credenciales sensibles que viven en la bóveda cifrada, NO en
# texto plano. Cualquier clave aquí se migra del .env a la bóveda al arrancar y
# desde entonces se lee descifrada bajo demanda. Añadir una credencial nueva al
# sistema = añadir su nombre aquí.
_SECRET_KEYS = [
    "TELEGRAM_BOT_TOKEN",
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "TAVILY_API_KEY",
    "MCP_TOKEN",
]

# Claves imprescindibles para que el proceso arranque siquiera (setup.py las
# pide interactivamente si faltan). Única fuente de verdad — validate() y
# clawlite/setup.py leen de aquí, nunca duplicar esta lista en otro sitio.
BOOTSTRAP_REQUIRED_KEYS = {
    "TELEGRAM_BOT_TOKEN": "TELEGRAM_BOT_TOKEN no está configurado",
    "TAVILY_API_KEY": "TAVILY_API_KEY no está configurado",
    "TELEGRAM_OWNER_ID": "TELEGRAM_OWNER_ID no está configurado (restringe el bot a vos)",
}

# Única fuente de verdad para el comando de invocación del bootstrap — si el
# mecanismo de arranque cambia (instalador, entry point empaquetado), se
# actualiza UNA vez acá y todo el que la referencie queda al día.
SETUP_COMMAND_HINT = "python -m clawlite.setup"


class Config:
    # LLM — Ollama (no son secretos: URL y nombres de modelo)
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2")
    OLLAMA_MODEL_STRUCTURED: str = os.getenv("OLLAMA_MODEL_STRUCTURED", "")
    # Modelo Ollama dedicado a CODING (cascada interna). Vacío → usa OLLAMA_MODEL.
    # Recomendado un modelo de código capaz (p.ej. 'qwen2.5-coder'); llama3.2 es
    # demasiado débil para escribir y corregir proyectos de forma fiable.
    OLLAMA_MODEL_CODING: str = os.getenv("OLLAMA_MODEL_CODING", "")
    # Modelo Ollama para razonamiento de MEMORIA (reconciliación: duplicado/actualiza/
    # distinto). Es razonamiento, no charla: un modelo débil produce supersedes erróneos.
    # Vacío → reutiliza OLLAMA_MODEL_CODING si está; si no, el modelo de JSON.
    OLLAMA_MODEL_MEMORY: str = os.getenv("OLLAMA_MODEL_MEMORY", "")
    # Modelo Ollama para el PLANNER (clasificación de intención). Es razonamiento/juicio
    # focalizado, no charla: un modelo débil clasifica mal frases cortas de identidad
    # ("soy desarrollador" → coding; "tengo 34 años" → memory_recall). Vacío → reutiliza
    # OLLAMA_MODEL_CODING si está; si no, el modelo de JSON (= comportamiento actual,
    # sin regresión).
    OLLAMA_MODEL_PLANNER: str = os.getenv("OLLAMA_MODEL_PLANNER", "")
    # Modelo Ollama para el FACTCHECKER (cruce de fuentes: agrupar la misma afirmación
    # dicha distinto entre fuentes y marcar corroboración 2+). Es razonamiento, no charla:
    # un modelo débil subcuenta verificaciones (agrupa mal). Vacío → reutiliza
    # OLLAMA_MODEL_CODING si está; si no, el modelo de JSON (= comportamiento actual).
    OLLAMA_MODEL_FACTCHECK: str = os.getenv("OLLAMA_MODEL_FACTCHECK", "")
    # Modelo Ollama para CONVERSACIÓN LIBRE (direct_answer/memory_recall) Y para la
    # síntesis final de research — ambas comparten hoy el mismo task_type por
    # defecto ("conversational") en llm/client.py. Un modelo débil aquí inventa
    # datos de entidades poco conocidas con total confianza (ej. una entidad real
    # y específica descrita con detalles fabricados) pese a la regla de honestidad
    # del prompt — regla blanda que el modelo débil ignora. Vacío → comportamiento
    # actual (modelo base), sin regresión.
    OLLAMA_MODEL_CONVERSATIONAL: str = os.getenv("OLLAMA_MODEL_CONVERSATIONAL", "")

    # Modelos (no secretos)
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")

    # ── Credenciales sensibles: leídas de la bóveda cifrada, nunca en claro ──
    # Se exponen como propiedades para que cada acceso consulte la bóveda. El
    # vault se instancia en __init__ y migra el .env la primera vez.
    @property
    def TELEGRAM_BOT_TOKEN(self) -> str:
        return self._vault.get("TELEGRAM_BOT_TOKEN") or ""

    # Único ID de Telegram autorizado a usar el bot. Sin esto, cualquiera que
    # llegue a hablarle al bot (username descubierto, un enlace, un grupo,
    # una invitación) pasa a ser tratado como un usuario legítimo más --
    # incluida la capacidad de pedir ejecución de código en el sandbox. Cada
    # instalación de ClawLite es de un solo dueño (confirmado, no se comparte
    # entre personas), así que un único ID alcanza -- no una lista. No es un
    # secreto (no necesita el vault), es solo un identificador.
    TELEGRAM_OWNER_ID: str = os.getenv("TELEGRAM_OWNER_ID", "")

    @property
    def vault(self):
        """Acceso directo a la bóveda cifrada (solo lectura -- no expone
        setter, no permite reemplazar la instancia), para módulos que
        necesitan guardar credenciales propias no cubiertas por las
        propiedades de arriba (ej. el token OAuth de Gmail, que se
        refresca y reescribe en tiempo de ejecución, no solo se lee una
        vez desde .env)."""
        return self._vault

    @property
    def GROQ_API_KEY(self) -> str:
        return self._vault.get("GROQ_API_KEY") or ""

    @property
    def OPENAI_API_KEY(self) -> str:
        return self._vault.get("OPENAI_API_KEY") or ""

    @property
    def ANTHROPIC_API_KEY(self) -> str:
        return self._vault.get("ANTHROPIC_API_KEY") or ""

    @property
    def TAVILY_API_KEY(self) -> str:
        return self._vault.get("TAVILY_API_KEY") or ""

    # LLM — proveedor preferido (ollama | groq | openai | anthropic)
    # Solo se usa como fallback si no hay cascada definida para el task_type
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama")

    # LLM — cascadas por task_type (lista separada por comas)
    # Cada call al LLM declara su task_type y el cliente recorre la cascada correspondiente.
    # Cualquier proveedor de la cascada que no esté configurado se omite automáticamente.
    LLM_TASK_CONVERSATIONAL: str = os.getenv(
        "LLM_TASK_CONVERSATIONAL", "ollama,groq,anthropic,openai"
    )
    LLM_TASK_CODING: str = os.getenv(
        "LLM_TASK_CODING", "anthropic,openai,groq,ollama"
    )
    # El planner corre en CADA mensaje: local-first con la MISMA resiliencia que la charla
    # (si Ollama cae, cascada a la nube). Mismo default que conversational a propósito.
    LLM_TASK_PLANNER: str = os.getenv(
        "LLM_TASK_PLANNER", "ollama,groq,anthropic,openai"
    )
    # El factchecker es razonamiento de corroboración (corre por cada research): local-first
    # con la MISMA resiliencia que la charla (si Ollama cae, cascada a la nube).
    LLM_TASK_FACTCHECK: str = os.getenv(
        "LLM_TASK_FACTCHECK", "ollama,groq,anthropic,openai"
    )

    def __init__(self):
        # La bóveda vive junto a la DB. Se instancia una vez al crear Config.
        db_path = os.getenv("DB_PATH", "./data/clawlite.db")
        vault_path = os.path.join(os.path.dirname(db_path) or ".", "credentials.vault")
        self._vault = CredentialVault(vault_path)

        # Migración automática: la primera vez, las claves que estén en el .env se
        # cifran en la bóveda. Desde entonces la bóveda es la fuente de verdad y el
        # .env puede borrarse. Idempotente: no re-migra lo que ya está cifrado.
        migrated = self._vault.import_from_env(_SECRET_KEYS)
        if migrated:
            logger.info(f"🔐 Vault: {len(migrated)} credenciales migradas del .env a la bóveda cifrada: "
                        f"{', '.join(migrated)}")
            logger.info("🔐 Ya puedes borrar esas claves de tu .env — la bóveda es la fuente de verdad.")
        logger.info(f"🔐 Vault activo — nivel de protección: {self._vault.protection_level()}")

    def get_user_llm_preference(self) -> dict:
        """
        Lee la preferencia de modelo elegida por el usuario (vía wizard o /modelo).
        Preferencia GLOBAL de la instancia (un dueño por instancia). Lee directo de
        la tabla patterns para no acoplar config a la capa de memoria.
        Devuelve {"provider": str|"", "fallback": bool}. Vacío si no hay preferencia.
        """
        try:
            import sqlite3
            with sqlite3.connect(self.DB_PATH) as conn:
                row = conn.execute(
                    "SELECT data FROM patterns WHERE pattern = 'llm_preference' "
                    "ORDER BY updated_at DESC, id DESC LIMIT 1"
                ).fetchone()
            if not row:
                return {"provider": "", "fallback": True}
            import json
            data = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
            return {
                "provider": data.get("provider", ""),
                "fallback": data.get("fallback", True),
            }
        except Exception:
            # Sin tabla aún, o cualquier fallo: comportamiento por defecto del sistema.
            return {"provider": "", "fallback": True}

    def ollama_model_for(self, task_type: str, structured: bool) -> str:
        """Modelo Ollama para una tarea (cascada interna por modelo). Un override por
        tarea (OLLAMA_MODEL_<TASK>, p.ej. OLLAMA_MODEL_CODING) tiene prioridad y aplica
        tanto a generación como a salida estructurada de esa tarea — así coding puede
        usar qwen2.5-coder entero. Sin ningún override configurado, TODAS las tareas
        resuelven al mismo OLLAMA_MODEL — un solo modelo cargado en Ollama a la vez,
        para no reventar la VRAM en hardware modesto (causa raíz del colapso con
        qwen3.5:9b + qwen2.5-coder cargados a la vez)."""
        override = getattr(self, f"OLLAMA_MODEL_{task_type.upper()}", "")
        if override:
            return override
        # Memoria (reconciliación) y Planner (clasificación de intención) son RAZONAMIENTO,
        # no charla: si hay un modelo de coding capaz configurado, reutilízalo para juzgar
        # mejor —sin exigir otra descarga—. Sin él, caen al modelo de JSON estructurado
        # (comportamiento actual, sin regresión).
        if task_type in ("memory", "planner", "factcheck") and self.OLLAMA_MODEL_CODING:
            return self.OLLAMA_MODEL_CODING
        if structured and self.OLLAMA_MODEL_STRUCTURED:
            return self.OLLAMA_MODEL_STRUCTURED
        return self.OLLAMA_MODEL

    def get_task_cascade(self, task_type: str) -> list[str]:
        """
        Devuelve la cascada de proveedores para un task_type.

        Si el usuario eligió un proveedor (wizard o /modelo):
        - fallback=False → SOLO ese proveedor (su elección manda; si falla, el
          cliente notifica y para, no cae a otro).
        - fallback=True  → ese proveedor primero, y el resto de la cascada del
          .env detrás como respaldo (el usuario reactivó el fallback).
        Sin preferencia → la cascada del .env tal cual (resiliencia automática).
        """
        attr_name = f"LLM_TASK_{task_type.upper()}"
        raw = getattr(self, attr_name, "")
        if not raw:
            base = [self.LLM_PROVIDER]
        else:
            base = [p.strip().lower() for p in raw.split(",") if p.strip()]

        pref = self.get_user_llm_preference()
        chosen = pref.get("provider", "")
        if not chosen:
            return base  # sin preferencia explícita → cascada del sistema

        if not pref.get("fallback", True):
            # El usuario quiere SOLO su proveedor. Sin fallback.
            return [chosen]

        # Con fallback: su proveedor primero, el resto detrás (sin duplicar).
        return [chosen] + [p for p in base if p != chosen]

    # Tavily
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

    # Base de datos
    DB_PATH: str = os.getenv("DB_PATH", "./data/clawlite.db")

    # Sandbox
    SANDBOX_MODE: str = os.getenv("SANDBOX_MODE", "strict")

    # Jobs asíncronos largos
    # Concurrencia máxima del JobRunner. Más jobs simultáneos = más API quemada
    # en paralelo. 2 es un balance razonable para uso personal.
    MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))

    # MCP Server — expone ClawLite como servidor MCP para clientes externos
    # (Claude Desktop, Cursor, agentes de terceros). Comparte memoria, workflows,
    # sandbox y jobs con el bot Telegram (mismo proceso, estado vivo).
    # Desactivado por defecto: solo se activa si MCP_ENABLED=true.
    MCP_ENABLED: bool = os.getenv("MCP_ENABLED", "false").lower() == "true"
    MCP_TRANSPORT: str = os.getenv("MCP_TRANSPORT", "stdio")  # stdio | sse
    MCP_HOST: str = os.getenv("MCP_HOST", "127.0.0.1")
    MCP_PORT: int = int(os.getenv("MCP_PORT", "8765"))
    MCP_DEFAULT_USER_ID: str = os.getenv("MCP_DEFAULT_USER_ID", "")

    @property
    def MCP_TOKEN(self) -> str:
        return self._vault.get("MCP_TOKEN") or ""

    # Daily Brief
    BRIEF_ENABLED: bool = os.getenv("BRIEF_ENABLED", "false").lower() == "true"
    BRIEF_HOUR: int = int(os.getenv("BRIEF_HOUR", "8"))
    BRIEF_TIMEZONE: str = os.getenv("BRIEF_TIMEZONE", "UTC")

    # Logs
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    def validate(self) -> None:
        """Valida que las variables obligatorias estén presentes al arrancar."""
        errors = []

        for key, message in BOOTSTRAP_REQUIRED_KEYS.items():
            if not getattr(self, key, ""):
                errors.append(message)

        if not self.OLLAMA_BASE_URL and not self.GROQ_API_KEY:
            errors.append("Necesitas configurar Ollama o GROQ_API_KEY")

        # MCP: solo se valida si está habilitado. Fail-fast: sin user_id el server
        # no sabría a quién pertenece la memoria que expone (riesgo de privacidad).
        if self.MCP_ENABLED:
            if not self.MCP_DEFAULT_USER_ID:
                errors.append("MCP_ENABLED=true pero MCP_DEFAULT_USER_ID no está configurado")
            if self.MCP_TRANSPORT == "sse" and not self.MCP_TOKEN:
                errors.append("MCP_TRANSPORT=sse pero MCP_TOKEN no está configurado (seguridad)")

        if errors:
            for e in errors:
                logger.error(f"❌ Config error: {e}")
            raise EnvironmentError(
                "ClawLite no puede arrancar. Revisa tu .env:\n" + "\n".join(errors)
            )

        logger.info("✅ Configuración validada correctamente")


config = Config()  # __init__ instancia el vault y migra el .env automáticamente
