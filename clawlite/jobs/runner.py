"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

jobs/runner.py — Loop asíncrono que ejecuta jobs en background
Vive en el proceso principal de ClawLite. Cada POLL_INTERVAL segundos
recoge jobs queued, los ejecuta con concurrencia limitada y notifica al
usuario por los canales registrados.
"""

import asyncio
import traceback
from typing import Awaitable, Callable
from loguru import logger
from clawlite.jobs.store import JobStore
from clawlite.jobs.executor import job_registry, JobContext
from clawlite.personality.catalog import msg as catalog_msg


# Firma de un notificador: async fn(user_id, text) -> None
NotifierFn = Callable[[str, str], Awaitable[None]]

# Firma del resolvedor de idioma: fn(user_id) -> código ISO o None. El runner
# corre en background, fuera de cualquier turno — no hay ContextVar de idioma
# vigente. Se inyecta desde main.py como Agent._display_lang (misma cadena de
# idioma persistente: sesión → user_language en deep_memory → detect_language)
# para que las notificaciones no queden fijas en español (10 jul).
LangResolverFn = Callable[[str], str | None]


# ── Referencias globales inyectadas desde main.py ────────────────────────────
# Necesarias para que los ejecutores accedan al orchestrator y brand_manager
# sin crear dependencias circulares.
_orchestrator = None
_brand_manager = None


def set_orchestrator(orchestrator):
    global _orchestrator
    _orchestrator = orchestrator


def set_brand_manager(brand_manager):
    global _brand_manager
    _brand_manager = brand_manager


def get_orchestrator():
    return _orchestrator


def get_brand_manager():
    return _brand_manager


class JobRunner:
    """
    Loop que ejecuta jobs en background con concurrencia limitada.
    Diseñado para ser agnóstico del canal de notificación: los notificadores
    se registran como callbacks. Hoy puede ser Telegram, mañana Discord, etc.
    """

    POLL_INTERVAL_SECONDS = 3
    MAX_JOB_DURATION_SECONDS = 7200  # 2 horas

    def __init__(
        self,
        store: JobStore,
        max_concurrent: int = 2,
    ):
        self.store = store
        self.max_concurrent = max_concurrent
        self._notifiers: list[NotifierFn] = []
        self._lang_resolver: LangResolverFn | None = None
        self._running_tasks: dict[int, asyncio.Task] = {}
        self._loop_task: asyncio.Task | None = None
        self._stopping = False

    # ── Registro de notificadores ────────────────────────────────────────────

    def register_notifier(self, notifier: NotifierFn):
        """
        Registra un canal de notificación. El runner llamará a cada notificador
        cuando un job termine. Permite multi-canal sin acoplar el runner a uno.
        """
        self._notifiers.append(notifier)
        logger.info(f"📡 JobRunner: notificador registrado (total: {len(self._notifiers)})")

    def set_lang_resolver(self, resolver: LangResolverFn):
        """Inyecta el resolvedor de idioma (ver LangResolverFn) para los
        mensajes de sistema generados por el propio runner (timeout, tipo de
        job desconocido)."""
        self._lang_resolver = resolver

    def _lang_for(self, user_id: str) -> str | None:
        if not self._lang_resolver:
            return None
        try:
            return self._lang_resolver(user_id)
        except Exception as e:
            logger.debug(f"JobRunner lang_resolver falló (fail-safe None): {e}")
            return None

    async def _notify_all(self, user_id: str, text: str):
        """Envía la notificación por todos los canales registrados."""
        for notifier in self._notifiers:
            try:
                await notifier(user_id, text)
            except Exception as e:
                logger.warning(f"⚠️  Notificador falló: {e}")

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    async def start(self):
        """Arranca el loop. Maneja orphans del reinicio anterior y arranca polling."""
        # Robustez 24/7: los jobs que quedaron 'running' tras un reinicio NO se
        # pierden. Se reencolan para ejecutarse solos otra vez (invisible para el
        # usuario). Solo se avisa de los que agotaron reintentos — esos sí murieron.
        requeued, exhausted = self.store.requeue_orphans()
        for job in exhausted:
            await self._notify_all(
                job["user_id"],
                f"❌ `#{job['id']}`\n_{job['title']}_",
            )

        self._stopping = False
        self._loop_task = asyncio.create_task(self._loop())
        logger.info(f"🚀 JobRunner iniciado (concurrencia={self.max_concurrent})")

    async def stop(self):
        """Detiene el loop. Los jobs en curso terminan o expiran por timeout."""
        self._stopping = True
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 JobRunner detenido")

    # ── Loop principal ───────────────────────────────────────────────────────

    async def _loop(self):
        try:
            while not self._stopping:
                # Limpiar tasks terminadas del registro
                self._reap_finished_tasks()

                # Recoger jobs nuevos si hay capacidad
                free_slots = self.max_concurrent - len(self._running_tasks)
                if free_slots > 0:
                    claimed = self.store.claim_next_queued(limit=free_slots)
                    for job in claimed:
                        task = asyncio.create_task(self._execute_job(job))
                        self._running_tasks[job["id"]] = task

                await asyncio.sleep(self.POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.debug("JobRunner loop cancelled")
            raise
        except Exception as e:
            logger.error(f"❌ JobRunner loop crash: {e}\n{traceback.format_exc()}")

    def _reap_finished_tasks(self):
        finished = [jid for jid, task in self._running_tasks.items() if task.done()]
        for jid in finished:
            del self._running_tasks[jid]

    # ── Ejecución de un job ──────────────────────────────────────────────────

    async def _execute_job(self, job: dict):
        """
        Ejecuta un job individual. Maneja timeout, errores, notificación.
        Esta función es la unidad de trabajo atómica del runner.
        """
        job_id = job["id"]
        user_id = job["user_id"]
        job_type = job["job_type"]
        title = job["title"]

        logger.info(f"▶️  Iniciando job #{job_id} [{job_type}]: {title}")
        await self._notify_all(
            user_id,
            f"🔄 `#{job_id}`\n_{title}_",
        )

        executor = job_registry.get(job_type)
        if executor is None:
            err = f"Unknown job type: '{job_type}'"
            self.store.set_failed(job_id, err)
            user_msg = catalog_msg("job_unknown_type", lang=self._lang_for(user_id), job_type=job_type)
            await self._notify_all(user_id, f"❌ `#{job_id}`\n`{user_msg}`")
            return

        # Callback de progreso: actualiza el store, no notifica al usuario
        # (los progress son frecuentes; notificar cada uno spammearía Telegram).
        # El usuario puede ver progreso con /job_status <id>.
        async def progress_fn(text: str):
            self.store.update_progress(job_id, text)

        # Construir contexto del job. Encapsula todo lo que el ejecutor necesita
        # (incluyendo update_config para persistir estado intermedio).
        ctx = JobContext(
            job_id=job_id,
            user_id=user_id,
            request=job["request"],
            config=job.get("config", {}),
            progress=progress_fn,
            _store=self.store,
        )

        try:
            result = await asyncio.wait_for(
                executor(ctx),
                timeout=self.MAX_JOB_DURATION_SECONDS,
            )

            self.store.set_completed(job_id, result)
            await self._notify_all(
                user_id,
                f"✅ `#{job_id}`\n_{title}_\n\n{result}",
            )

        except asyncio.TimeoutError:
            err = f"Job exceeded the {self.MAX_JOB_DURATION_SECONDS}s timeout"
            will_retry = self.store.set_failed(job_id, err)
            if not will_retry:
                user_msg = catalog_msg(
                    "job_timeout", lang=self._lang_for(user_id),
                    seconds=self.MAX_JOB_DURATION_SECONDS,
                )
                await self._notify_all(user_id, f"⏱ `#{job_id}`\n{user_msg}")

        except asyncio.CancelledError:
            self.store.set_failed(job_id, "Job cancelado durante ejecución (shutdown del runner)")
            raise

        except Exception as e:
            err_trace = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            will_retry = self.store.set_failed(job_id, err_trace)
            logger.error(f"❌ Job #{job_id} falló:\n{err_trace}")
            # Si se reintentará, el reintento es invisible: no notificamos. Solo
            # avisamos cuando el fallo es definitivo (agotó intentos).
            if not will_retry:
                await self._notify_all(
                    user_id,
                    f"❌ `#{job_id}`\n_{title}_\n\n`{type(e).__name__}: {str(e)[:200]}`",
                )
