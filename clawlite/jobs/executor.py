"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

jobs/executor.py — Registro de tipos de job y dispatcher
Cada tipo de job (research, coding, brand_calendar, ...) se registra aquí
con su función ejecutora. Añadir un tipo nuevo es solo registrarlo, no tocar
el JobRunner.

Contrato del ejecutor:
  async def ejecutor(ctx: JobContext) -> str
  - ctx contiene: job_id, user_id, request, config, progress, store
  - retorna el resultado final como texto markdown listo para enviar
  - puede persistir estado intermedio en config via ctx.update_config()
"""

import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Any
from loguru import logger


@dataclass
class JobContext:
    """
    Contexto que recibe cada ejecutor de job. Encapsula todo lo que necesita:
    identificación, parámetros, progreso, y persistencia de estado intermedio.

    Diseñado así para que añadir capacidades nuevas al contrato (ej: cancelación
    cooperativa) no rompa los ejecutores existentes.
    """
    job_id: int
    user_id: str
    request: str
    config: dict
    progress: Callable[[str], Awaitable[None]]
    _store: Any = None  # JobStore — tipo abierto para evitar import circular

    async def update_config(self, key: str, value: Any):
        """
        Persiste un valor en config_json del job. Útil para guardar resultados
        intermedios (ej: archivos generados por coding) que comandos posteriores
        del usuario querrán recuperar.
        """
        if self._store is None:
            return
        self.config[key] = value
        # Persistir todo el dict config actualizado
        import sqlite3
        with sqlite3.connect(self._store.db_path) as conn:
            conn.execute(
                "UPDATE async_jobs SET config_json = ? WHERE id = ?",
                (json.dumps(self.config), self.job_id),
            )


JobExecutorFn = Callable[[JobContext], Awaitable[str]]


class JobRegistry:
    """
    Registro central de ejecutores por tipo de job. Agregar un tipo nuevo
    es una sola llamada a register(). El runner no necesita saber qué tipos existen.
    """

    def __init__(self):
        self._executors: dict[str, JobExecutorFn] = {}

    def register(self, job_type: str, executor: JobExecutorFn):
        """Registra un ejecutor para un tipo de job."""
        if job_type in self._executors:
            logger.warning(f"⚠️  Sobrescribiendo executor existente para job_type='{job_type}'")
        self._executors[job_type] = executor
        logger.info(f"📌 JobRegistry: tipo '{job_type}' registrado")

    def get(self, job_type: str) -> JobExecutorFn | None:
        return self._executors.get(job_type)

    def list_types(self) -> list[str]:
        return sorted(self._executors.keys())


# Instancia global del registro — se usa desde main.py para registrar tipos
job_registry = JobRegistry()


# ── Ejecutores concretos ────────────────────────────────────────────────────
# Cada uno envuelve un componente existente de ClawLite y lo expone como job.

async def execute_research(ctx: JobContext) -> str:
    """
    Ejecuta una investigación profunda usando el sistema multi-agente.
    Reutiliza Orchestrator.run() que ya existe.
    """
    from clawlite.jobs.runner import get_orchestrator

    orchestrator = get_orchestrator()
    if orchestrator is None:
        raise RuntimeError("Orchestrator no inicializado")

    await ctx.progress("Investigando con agentes en paralelo...")
    response, _used_cloud = await orchestrator.run(ctx.user_id, ctx.request)
    return response


async def execute_coding(ctx: JobContext) -> str:
    """
    Ejecuta un job de coding. Reutiliza orchestrator.run_coding(), que internamente
    arranca el CodingAgent con su sandbox Docker, tests, fix loop, etc.

    Persiste el resultado completo (con archivos) en config para que el comando
    /job_files pueda recuperarlos después.
    """
    from clawlite.jobs.runner import get_orchestrator

    orchestrator = get_orchestrator()
    if orchestrator is None:
        raise RuntimeError("Orchestrator no inicializado")

    async def coding_progress(text: str):
        await ctx.progress(text)

    result = await orchestrator.run_coding(
        user_id=ctx.user_id,
        request=ctx.request,
        progress_callback=coding_progress,
        scheduled=True,  # nace de un job que el usuario creó (mandato SYSTEM_SCHEDULED)
    )

    # Persistir el resultado completo (incluyendo archivos) para /job_files —
    # SIEMPRE, no solo si los tests pasaron. Antes, un proyecto con tests fallidos
    # hacía return aquí arriba y esta línea nunca corría: los archivos existían un
    # instante en el sandbox Docker (que se destruye enseguida) y se perdían para
    # siempre, mientras el resumen de abajo seguía listando sus nombres como si
    # estuvieran disponibles — una promesa falsa. Los archivos son reales y útiles
    # para revisar/arreglar aunque los tests no hayan pasado.
    await ctx.update_config("coding_result", result)

    summary = result.get("summary", "❌")
    n_files = len(result.get("files", {}))
    if n_files:
        summary += f"\n\n📎 {n_files} · `/job_files {ctx.job_id}`"
    return summary


async def execute_brand_calendar(ctx: JobContext) -> str:
    """
    Genera un calendario de contenido para la marca del usuario.
    Reutiliza BrandManager.generate_calendar() existente.

    Corrige un TypeError preexistente: esta función no pasaba el argumento
    `message` (requerido, sin default) a generate_calendar() — cualquier job
    real de este tipo fallaba antes de este cambio. Ahora pasa ctx.request,
    igual que el otro llamador existente (core.py:2138).

    Antes de generar, pide a BrandManager la query de investigación de
    tendencias (centrada en ctx.request, dominio de BrandManager, no del
    executor) y, si existe, corre esa investigación real vía
    Orchestrator.run() — mismo mecanismo que execute_research. Solo se
    considera grounding válido si orchestrator.last_research_meta confirma
    que NO fue synthesis_failed ni edge_message — mismo contrato ya usado en
    core.py:875 para decidir si una respuesta de research es genuina, no una
    adivinanza por contenido de texto. Fail-safe: cualquier fallo u
    hallazgo no confirmado como genuino deja research_findings en None — el
    calendario se genera igual, nunca bloqueado por la investigación.
    """
    from clawlite.jobs.runner import get_brand_manager, get_orchestrator

    brand_manager = get_brand_manager()
    if brand_manager is None:
        raise RuntimeError("BrandManager no inicializado")

    period = ctx.config.get("period", "week")

    research_findings = None
    try:
        research_query = brand_manager.build_calendar_research_query(ctx.user_id, period, ctx.request)
        if research_query:
            orchestrator = get_orchestrator()
            if orchestrator is not None:
                await ctx.progress("🔍 Investigando tendencias reales...")
                # include_personal_context=False: esta es investigación de
                # tendencias de mercado, no una respuesta personal al
                # usuario -- no debe mezclarse con sus hechos/tareas
                # propias (causa raíz real de contenido ajeno al negocio en
                # el calendario, visto en pantalla real, job #11, 17 jul
                # 2026). Preserva Research+Brand+Síntesis igual que antes.
                findings, _used_cloud = await orchestrator.run(
                    ctx.user_id, research_query, include_personal_context=False
                )
                meta = orchestrator.last_research_meta
                if (meta and not meta.get("synthesis_failed") and not meta.get("edge_message")
                        and findings and findings.strip()):
                    research_findings = findings
    except Exception as e:
        logger.warning(f"⚠️ Investigación de tendencias falló para calendario, sigue sin ella: {e}")
        research_findings = None

    await ctx.progress(f"📅 {period}")
    return await brand_manager.generate_calendar(
        ctx.user_id, ctx.request, period=period, research_findings=research_findings
    )


def register_default_executors():
    """
    Registra los ejecutores que vienen con ClawLite. Llamar una vez al arrancar.
    Plugins externos pueden registrar más tipos llamando directamente a
    job_registry.register().
    """
    job_registry.register("research", execute_research)
    job_registry.register("coding", execute_coding)
    job_registry.register("brand_calendar", execute_brand_calendar)
