"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

main.py — Punto de entrada de ClawLite
"""

from loguru import logger
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, CallbackQueryHandler, TypeHandler, filters, ContextTypes
)
from telegram.error import NetworkError, TimedOut
from clawlite.config import config
from clawlite.memory.store import MemoryStore
from clawlite.memory.profile import DeepMemory
from clawlite.agent.core import Agent
from clawlite.proactivity.engine import ProactivityEngine
from clawlite.proactivity.scheduler import ProactivityScheduler
from clawlite.bot.wizard import OnboardingWizard
from clawlite.bot.handlers import (
    start, help_command, status,
    memory_command, brief_command, profile_command, olvida_command,
    handle_message, handle_document, handle_photo,
    handle_callback, set_agent, set_wizard, gmail_command,
    voice_command, handle_voice,
    job_command, jobs_command, job_status_command, cancel_command, job_files_command,
    set_job_store,
    watches_command, set_watch_store,
    recordatorios_command,
    modelo_command,
    unknown_command,
    split_telegram_message,
)
from clawlite.jobs.store import JobStore
from clawlite.jobs.runner import JobRunner, set_orchestrator as runner_set_orchestrator, set_brand_manager as runner_set_brand_manager
from clawlite.jobs.executor import register_default_executors
from clawlite.watches.store import WatchStore
from clawlite.watches.sources import register_default_sources
from clawlite.mcp.server import build_tool_context, run_mcp_server
from clawlite.sandbox.agent_sandbox import AgentSandbox


def _require_valid_config():
    """Valida la config y sale con un mensaje accionable si falta algo
    imprescindible para arrancar — compartido por main() y run_mcp_only().
    Usa logger.error() para AMBAS líneas, nunca print(): run_mcp_only()
    reserva stdout exclusivamente para el protocolo JSON-RPC (ver su propio
    docstring) — un print() aquí lo corrompería si la validación falla en
    ese modo."""
    import sys
    from clawlite.config import SETUP_COMMAND_HINT
    try:
        config.validate()
    except EnvironmentError as e:
        logger.error(str(e))
        logger.error(f"👉 Ejecuta primero: {SETUP_COMMAND_HINT}")
        sys.exit(1)


def main():
    _require_valid_config()

    from clawlite.llm.bootstrap import ensure_ollama_models_available
    ensure_ollama_models_available()

    from clawlite._version import __version__
    logger.info(f"🚀 Arrancando ClawLite v{__version__}...")

    from clawlite.memory.embeddings import EmbeddingEngine
    embedding_engine = EmbeddingEngine()  # motor único, compartido por store y multimodal
    memory = MemoryStore(db_path=config.DB_PATH, embedding_engine=embedding_engine)
    deep_memory = DeepMemory(db_path=config.DB_PATH)
    agent = Agent(memory=memory, deep_memory=deep_memory)
    wizard = OnboardingWizard(deep_memory=deep_memory)

    from clawlite.agents.orchestrator import Orchestrator
    from clawlite.agent.tools.brand import BrandManager
    # Instancia única de BrandManager reutilizada en todo ClawLite (orchestrator,
    # workflow registry, job runner, MCP). Fuente única de verdad: todas comparten
    # el mismo deep_memory y la misma instancia, no copias paralelas.
    brand_manager = BrandManager(deep_memory)
    orchestrator = Orchestrator(
        profile=agent.profile,
        brand_manager=brand_manager,
        db_path=config.DB_PATH,
        memory_store=memory,
    )
    agent.set_orchestrator(orchestrator)

    from clawlite.memory.multimodal import MultimodalMemory
    from clawlite.memory.dual_index import DualIndex
    multimodal = MultimodalMemory(db_path=config.DB_PATH, embedding_engine=embedding_engine)
    dual_index = DualIndex(memory_store=memory, multimodal_memory=multimodal)
    agent.set_multimodal(multimodal, dual_index)

    from clawlite.workflows.registry import ActionRegistry
    from clawlite.workflows.store import WorkflowStore
    from clawlite.workflows.executor import WorkflowExecutor
    from clawlite.workflows.extractor import WorkflowExtractor
    from clawlite.agent.tools.gmail import gmail_tool

    workflow_registry = ActionRegistry(
        brand_manager=brand_manager,
        gmail_tool=gmail_tool,
        deep_memory=deep_memory,
    )
    workflow_store = WorkflowStore(db_path=config.DB_PATH, embedding_engine=embedding_engine)
    workflow_executor = WorkflowExecutor(workflow_registry, workflow_store)
    workflow_extractor = WorkflowExtractor(
        registry=workflow_registry,
        store=workflow_store,
        skill_store=orchestrator.skill_store,
    )
    agent.set_workflows(workflow_store, workflow_executor, workflow_extractor, orchestrator.skill_store)

    set_agent(agent)
    set_wizard(wizard)

    # ── Jobs asíncronos ──────────────────────────────────────────────────────
    # JobStore persiste; JobRunner es el loop que ejecuta jobs en background.
    # Los ejecutores se registran como plugins → añadir tipos nuevos no toca código del runner.
    job_store = JobStore(db_path=config.DB_PATH)
    job_runner = JobRunner(store=job_store, max_concurrent=config.MAX_CONCURRENT_JOBS)

    # Inyectar dependencias al runner (resuelve dependencias circulares de import)
    runner_set_orchestrator(orchestrator)
    runner_set_brand_manager(brand_manager)
    # Idioma para las notificaciones del runner (corre fuera de cualquier
    # turno, sin ContextVar vigente): misma cadena de idioma persistente que
    # el resto del proyecto (hallazgo "notificaciones async en español fijo",
    # 10 jul).
    job_runner.set_lang_resolver(lambda uid: agent._display_lang(uid, None))

    # Registrar los 3 ejecutores que vienen con ClawLite (research, coding, brand_calendar)
    register_default_executors()

    # Exponer el job_store a los handlers de Telegram (/job, /jobs, /cancel, etc.)
    set_job_store(job_store)

    # Exponer el job_store al agente para detección automática de intent async_job
    agent.set_job_store(job_store)

    # ── Watches (cron en lenguaje natural por evento) ──────────────────────────
    # WatchStore persiste las suscripciones condición→acción (misma DB local).
    # Las fuentes de evento se registran como plugins → añadir fuentes nuevas
    # (calendario, webhook, archivo) no toca código del trigger ni del core.
    # El WatchTrigger (en el ProactivityEngine) las evalúa cada ciclo; instancia
    # su propio WatchStore desde profile.memory.db_path, así que no requiere
    # inyección al engine. Aquí solo cableamos la creación de watches por el agente.
    watch_store = WatchStore(db_path=config.DB_PATH)
    register_default_sources()
    agent.set_watch_store(watch_store)
    set_watch_store(watch_store)

    # ── MCP Server (opcional) ─────────────────────────────────────────────────
    # Si MCP_ENABLED=true, exponemos ClawLite como MCP server para clientes externos
    # (Claude Desktop, Cursor, etc.). Comparte memoria/workflows/sandbox/jobs VIVOS
    # con el bot — mismo proceso, mismas instancias, sin duplicar estado.
    # mcp_task se declara aquí (scope de main) para que los hooks _on_startup /
    # _on_shutdown lo vean por closure. Se arranca en _on_startup, se cancela en
    # _on_shutdown.
    mcp_task = None
    mcp_ctx = None
    if config.MCP_ENABLED:
        mcp_ctx = build_tool_context(
            user_id=config.MCP_DEFAULT_USER_ID,
            memory_store=memory,
            deep_memory=deep_memory,
            profile=agent.profile,
            brand_manager=brand_manager,
            dual_index=dual_index,
            workflow_store=workflow_store,
            workflow_executor=workflow_executor,
            orchestrator=orchestrator,
            job_store=job_store,
            agent_sandbox_cls=AgentSandbox,
        )
        logger.info(
            f"🔌 MCP Server habilitado (transport={config.MCP_TRANSPORT}, "
            f"user={config.MCP_DEFAULT_USER_ID})"
        )

    # ── Bot Telegram ─────────────────────────────────────────────────────────
    async def _on_startup(app):
        """Hook que se ejecuta cuando el bot está listo. Registra el notificador
        Telegram y arranca el loop del JobRunner. Multi-canal: si mañana añades
        Discord, registras otro notificador aquí y listo."""
        async def telegram_notifier(user_id: str, text: str):
            # Mismo límite real de Telegram que send_response ya resolvía --
            # las notificaciones de job pasaban por send_message directo,
            # sin chunking. Causa raíz real de "Message is too long" en jobs
            # con resultado largo (visto en pantalla real, job #11
            # brand_calendar, 5476 caracteres): el reintento sin Markdown
            # fallaba IGUAL porque el problema era el largo, no el formato.
            for chunk in split_telegram_message(text):
                try:
                    await app.bot.send_message(
                        chat_id=int(user_id),
                        text=chunk,
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    try:
                        await app.bot.send_message(chat_id=int(user_id), text=chunk)
                    except Exception as e2:
                        logger.warning(f"⚠️  No pude notificar a {user_id}: {e2}")

        job_runner.register_notifier(telegram_notifier)
        await job_runner.start()

        # Arrancar MCP server como task paralelo si está habilitado.
        # IMPORTANTE: stdio NO puede convivir con el bot en el mismo proceso —
        # stdio posee stdin/stdout del proceso para JSON-RPC, y el bot/logs los
        # contaminarían. stdio exige proceso dedicado: usar `python -m clawlite.main --mcp-only`.
        # SSE sí convive (abre un puerto HTTP, no toca stdin/stdout).
        if config.MCP_ENABLED:
            if config.MCP_TRANSPORT == "stdio":
                logger.error(
                    "🔌 MCP_TRANSPORT=stdio no puede correr junto al bot Telegram "
                    "(stdio necesita stdin/stdout exclusivos). Para usar stdio con "
                    "Claude Desktop, arranca en modo dedicado: "
                    "`python -m clawlite.main --mcp-only`. El bot sigue funcionando normal; "
                    "el MCP server NO se ha arrancado."
                )
            else:
                import asyncio
                nonlocal mcp_task
                mcp_task = asyncio.create_task(
                    run_mcp_server(
                        ctx=mcp_ctx,
                        transport=config.MCP_TRANSPORT,
                        host=config.MCP_HOST,
                        port=config.MCP_PORT,
                        token=config.MCP_TOKEN,
                    )
                )

    async def _on_shutdown(app):
        """Hook que se ejecuta al cerrar. Detiene el JobRunner y los workers
        aislados del Orchestrator limpiamente."""
        await job_runner.stop()
        orchestrator.worker_pool.shutdown()

        # Cancelar el MCP server limpiamente si estaba corriendo.
        if mcp_task and not mcp_task.done():
            import asyncio
            mcp_task.cancel()
            try:
                await mcp_task
            except asyncio.CancelledError:
                pass

    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )

    engine = ProactivityEngine(bot=app.bot, deep_memory=deep_memory)
    scheduler = ProactivityScheduler(engine=engine)

    # Control de acceso: SOLO el dueño configurado puede usar el bot.
    # group=-2 corre antes que TODO lo demás, incluido el rate limiter.
    from clawlite.bot.middleware import owner_only_middleware, rate_limit_middleware
    app.add_handler(TypeHandler(Update, owner_only_middleware), group=-2)
    app.add_handler(TypeHandler(Update, rate_limit_middleware), group=-1)

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("brief", brief_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("olvida", olvida_command))
    app.add_handler(CommandHandler("watches", watches_command))
    app.add_handler(CommandHandler("recordatorios", recordatorios_command))
    app.add_handler(CommandHandler("gmail", gmail_command))
    app.add_handler(CommandHandler("voz", voice_command))
    app.add_handler(CommandHandler("modelo", modelo_command))

    # Jobs asíncronos largos
    app.add_handler(CommandHandler("job", job_command))
    app.add_handler(CommandHandler("jobs", jobs_command))
    app.add_handler(CommandHandler("job_status", job_status_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("job_files", job_files_command))

    # Callbacks de botones inline
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Documentos (PDF, TXT, etc.)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Imágenes
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Audio / voz
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

    # Mensajes de texto
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Comandos CON entidad de Telegram que ningún CommandHandler registrado
    # atendió (p.ej. /foobar): respuesta determinista en vez de silencio mudo.
    # Registrado DESPUÉS de todos los CommandHandlers — solo recibe lo no atendido.
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    # Errores transitorios de red con la API de Telegram (NetworkError/TimedOut):
    # el polling reintenta solo. Se loguean en UNA línea, sin traceback, para no
    # ensuciar la consola ni hacer dudar de que algo esté roto. Cualquier OTRO error
    # se loguea completo (con traza) — no se oculta nada real.
    async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = context.error
        if isinstance(err, (NetworkError, TimedOut)):
            logger.warning(
                f"🌐 Telegram transitorio ({type(err).__name__}): {err} — el polling reintenta solo"
            )
            return
        logger.opt(exception=err).error(f"❌ Error no manejado: {err}")

    app.add_error_handler(handle_error)

    scheduler.start()

    logger.info("✅ ClawLite listo. Esperando mensajes en Telegram...")
    app.run_polling(drop_pending_updates=True)


def run_mcp_only():
    """
    Modo MCP dedicado (stdio). Arranca SOLO el MCP server, sin bot Telegram,
    sin scheduler, sin proactivity. Para que Claude Desktop (u otro cliente MCP)
    arranque ClawLite como subproceso: `python -m clawlite.main --mcp-only`.

    Por qué un modo aparte: stdio posee stdin/stdout del proceso para JSON-RPC.
    El bot, el scheduler y cualquier escritura a stdout corromperían el protocolo.
    Este modo garantiza stdout limpio y todos los logs a stderr.
    """
    import sys
    import asyncio

    # Blindaje: forzar TODOS los logs a stderr. stdout queda reservado para JSON-RPC.
    # (loguru por defecto ya va a stderr, pero lo reafirmamos por si alguna lib
    # de terceros intentara escribir a stdout, y para dejarlo explícito.)
    logger.remove()
    logger.add(sys.stderr, level=config.LOG_LEVEL)

    _require_valid_config()

    if not config.MCP_ENABLED:
        logger.error("🔌 --mcp-only requiere MCP_ENABLED=true en el .env")
        sys.exit(1)
    if config.MCP_TRANSPORT != "stdio":
        logger.error(
            f"🔌 --mcp-only es solo para stdio (transport actual: {config.MCP_TRANSPORT}). "
            "Para SSE, arranca ClawLite normal: `python -m clawlite.main`"
        )
        sys.exit(1)

    logger.info("🚀 Arrancando ClawLite en modo MCP-only (stdio)...")

    # Construir SOLO las dependencias que las tools necesitan. Sin bot, sin scheduler.
    from clawlite.memory.embeddings import EmbeddingEngine
    embedding_engine = EmbeddingEngine()
    memory = MemoryStore(db_path=config.DB_PATH, embedding_engine=embedding_engine)
    deep_memory = DeepMemory(db_path=config.DB_PATH)
    agent = Agent(memory=memory, deep_memory=deep_memory)

    from clawlite.agents.orchestrator import Orchestrator
    from clawlite.agent.tools.brand import BrandManager
    brand_manager = BrandManager(deep_memory)
    orchestrator = Orchestrator(
        profile=agent.profile,
        brand_manager=brand_manager,
        db_path=config.DB_PATH,
        memory_store=memory,
    )
    agent.set_orchestrator(orchestrator)

    from clawlite.memory.multimodal import MultimodalMemory
    from clawlite.memory.dual_index import DualIndex
    multimodal = MultimodalMemory(db_path=config.DB_PATH, embedding_engine=embedding_engine)
    dual_index = DualIndex(memory_store=memory, multimodal_memory=multimodal)
    agent.set_multimodal(multimodal, dual_index)

    from clawlite.workflows.registry import ActionRegistry
    from clawlite.workflows.store import WorkflowStore
    from clawlite.workflows.executor import WorkflowExecutor
    from clawlite.agent.tools.gmail import gmail_tool

    workflow_registry = ActionRegistry(
        brand_manager=brand_manager,
        gmail_tool=gmail_tool,
        deep_memory=deep_memory,
    )
    workflow_store = WorkflowStore(db_path=config.DB_PATH, embedding_engine=embedding_engine)
    workflow_executor = WorkflowExecutor(workflow_registry, workflow_store)

    job_store = JobStore(db_path=config.DB_PATH)

    from clawlite.sandbox.agent_sandbox import AgentSandbox

    mcp_ctx = build_tool_context(
        user_id=config.MCP_DEFAULT_USER_ID,
        memory_store=memory,
        deep_memory=deep_memory,
        profile=agent.profile,
        brand_manager=brand_manager,
        dual_index=dual_index,
        workflow_store=workflow_store,
        workflow_executor=workflow_executor,
        orchestrator=orchestrator,
        job_store=job_store,
        agent_sandbox_cls=AgentSandbox,
    )

    logger.info(f"🔌 MCP-only listo (user={config.MCP_DEFAULT_USER_ID}). Sirviendo por stdio...")
    try:
        asyncio.run(run_mcp_server(ctx=mcp_ctx, transport="stdio"))
    finally:
        orchestrator.worker_pool.shutdown()


if __name__ == "__main__":
    import sys
    if "--mcp-only" in sys.argv:
        run_mcp_only()
    else:
        main()

