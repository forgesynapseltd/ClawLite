"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

mcp/tools.py — Implementación de las MCP tools que expone ClawLite.
Cada tool es un wrapper fino sobre una capacidad existente. No reescribimos
lógica, solo la exponemos en formato MCP.

Cada wrapper recibe un ToolContext con las dependencias inyectadas, y devuelve
un string markdown o JSON serializado que el cliente MCP recibe como resultado.
"""

import json
from dataclasses import dataclass
from typing import Any
from loguru import logger
from clawlite.governance import action_guard, Mandate, MandateOrigin, GovernanceDenied


@dataclass
class ToolContext:
    """
    Dependencias que las tools necesitan para operar.
    Se construye una vez al arrancar el server y se pasa a cada handler.
    Esto evita variables globales y facilita testing.
    """
    user_id: str  # ID bajo el cual opera el cliente MCP (de MCP_DEFAULT_USER_ID)
    memory_store: Any
    deep_memory: Any
    profile: Any
    brand_manager: Any
    dual_index: Any
    workflow_store: Any
    workflow_executor: Any
    orchestrator: Any
    job_store: Any
    agent_sandbox_cls: Any  # AgentSandbox class (lazy instantiation)


# ── TOOLS DE MEMORIA ────────────────────────────────────────────────────────

async def tool_recall_memory(ctx: ToolContext, query: str, top_k: int = 5) -> str:
    """
    Busca en la memoria del usuario usando dual-index (texto + multimodal).
    Devuelve fragmentos relevantes formateados como contexto.
    """
    if ctx.dual_index:
        recall = await ctx.dual_index.recall(ctx.user_id, query, top_k=top_k)
        formatted = ctx.dual_index.format_recall_context(recall)
        return formatted or "(no se encontraron memorias relevantes)"

    # Fallback al memory_store básico si dual_index no está disponible
    recalled = ctx.memory_store.recall_similar(ctx.user_id, query, top_k=top_k)
    if not recalled:
        return "(no se encontraron memorias relevantes)"
    return "\n\n".join(f"- {r}" for r in recalled)


async def tool_get_profile(ctx: ToolContext) -> str:
    """Devuelve el perfil del usuario (preferencias, intereses, contexto)."""
    if not ctx.profile:
        return "(perfil no disponible)"
    context = ctx.profile.build_context(ctx.user_id)
    return context or "(perfil vacío — el usuario aún no ha compartido datos)"


# ── TOOLS DE MARCA ──────────────────────────────────────────────────────────

async def tool_get_brand(ctx: ToolContext) -> str:
    """Devuelve el perfil de marca configurado por el usuario (si existe)."""
    if not ctx.brand_manager:
        return "(brand manager no disponible)"

    brand_context = ctx.brand_manager.build_brand_context(ctx.user_id)
    if not brand_context:
        return "(el usuario no ha configurado un perfil de marca todavía)"
    return brand_context


# ── TOOLS DE WORKFLOWS ──────────────────────────────────────────────────────

async def tool_list_workflows(ctx: ToolContext) -> str:
    """Lista los workflows aprendidos del usuario."""
    if not ctx.workflow_store:
        return "(workflow store no disponible)"

    workflows = ctx.workflow_store.list_workflows(ctx.user_id)
    if not workflows:
        return "(no hay workflows aprendidos todavía)"

    lines = ["Workflows disponibles:\n"]
    for wf in workflows:
        name = wf.get("name", "(sin nombre)")
        desc = wf.get("description", "")[:100]
        lines.append(f"- `{name}`: {desc}")
    return "\n".join(lines)


async def tool_run_workflow(ctx: ToolContext, workflow_name: str, message: str = "") -> str:
    """
    Ejecuta un workflow aprendido por nombre.
    `message` es el texto de invocación (puede contener parámetros).
    """
    if not ctx.workflow_store or not ctx.workflow_executor:
        return "(sistema de workflows no disponible)"

    # Buscar workflow por nombre exacto
    workflows = ctx.workflow_store.list_workflows(ctx.user_id)
    matched = next((wf for wf in workflows if wf.get("name") == workflow_name), None)

    if not matched:
        available = ", ".join(wf.get("name", "?") for wf in workflows[:10])
        return f"No encontré el workflow '{workflow_name}'. Disponibles: {available}"

    output, success = await ctx.workflow_executor.execute(
        workflow=matched,
        user_id=ctx.user_id,
        user_message=message or workflow_name,
    )
    return output if success else f"Workflow falló: {output}"


# ── TOOLS DE JOBS ASÍNCRONOS ────────────────────────────────────────────────

async def tool_create_job(
    ctx: ToolContext,
    request: str,
    job_type: str = "research",
) -> str:
    """
    Crea un job asíncrono. El job correrá en background dentro de ClawLite.
    Devuelve el job_id para consultarlo después.
    """
    if not ctx.job_store:
        return "(sistema de jobs no disponible)"

    if job_type not in ("research", "coding", "brand_calendar"):
        return f"job_type inválido: '{job_type}'. Válidos: research, coding, brand_calendar"

    title = request[:80] + ("…" if len(request) > 80 else "")
    job_id = ctx.job_store.create(
        user_id=ctx.user_id,
        title=title,
        request=request,
        job_type=job_type,
    )
    return json.dumps({
        "job_id": job_id,
        "status": "queued",
        "type": job_type,
        "title": title,
        "message": f"Job #{job_id} creado. Consulta con claw_get_job_status({job_id}).",
    })


async def tool_get_job_status(ctx: ToolContext, job_id: int) -> str:
    """Consulta el estado y progreso de un job. Devuelve JSON con detalle."""
    if not ctx.job_store:
        return "(sistema de jobs no disponible)"

    job = ctx.job_store.get(job_id)
    if not job or job["user_id"] != ctx.user_id:
        return json.dumps({"error": f"job #{job_id} no encontrado"})

    return json.dumps({
        "job_id": job["id"],
        "status": job["status"],
        "type": job["job_type"],
        "title": job["title"],
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "progress": job.get("progress"),
        "result": job.get("result") if job["status"] == "completed" else None,
        "error": job.get("error") if job["status"] == "failed" else None,
    })


# ── TOOLS DE SANDBOX ────────────────────────────────────────────────────────

async def tool_execute_in_sandbox(
    ctx: ToolContext,
    code: str,
    networked: bool = False,
) -> str:
    """
    Ejecuta código Python en un sandbox Docker aislado.
    `networked=True` permite acceso a internet (necesario para pip install, APIs).
    Devuelve stdout + stderr del ejecutado.
    """
    if not ctx.agent_sandbox_cls:
        return "(sandbox no disponible)"

    # ── Compuerta de gobernanza (ActionGuard) ──────────────────────────────────
    # Ejecutar código vía MCP es ALTO impacto y, hasta ahora, esquivaba el kernel.
    # La llamada MCP es la integración del propio usuario (USER_DIRECT). Mediada y
    # auditada; si la política de execute_code se endurece, esta vía queda sujeta.
    try:
        action_guard.enforce(
            "execute_code",
            Mandate(
                origin=MandateOrigin.USER_DIRECT,
                user_id=str(getattr(ctx, "user_id", "mcp")),
                summary=f"MCP execute_in_sandbox (networked={networked})",
            ),
        )
    except GovernanceDenied as denied:
        return f"🚫 Ejecución bloqueada por la política de seguridad ({denied.decision.reason})."

    # Crear sandbox efímero solo para esta ejecución
    sandbox = ctx.agent_sandbox_cls(networked=networked)
    try:
        if not sandbox.start():
            return "❌ No pude iniciar el sandbox"

        # Escribir el script y ejecutarlo
        if not sandbox.write_file("script.py", code):
            return "❌ No pude escribir el script en el sandbox"

        result = sandbox.exec("python script.py", timeout=60)
        return json.dumps({
            "success": result.success,
            "exit_code": result.exit_code,
            "stdout": result.stdout[:5000],
            "stderr": result.stderr[:2000],
            "duration": round(result.duration, 2),
        })
    finally:
        sandbox.stop()


# ── TOOLS DE INVESTIGACIÓN ──────────────────────────────────────────────────

async def tool_research(ctx: ToolContext, query: str) -> str:
    """
    Investigación SÍNCRONA usando el sistema multi-agente (research + context +
    brand + fact-check). Para investigaciones largas, mejor usar claw_create_job
    con job_type='research'.
    """
    if not ctx.orchestrator:
        return "(orchestrator no disponible)"

    response, _ = await ctx.orchestrator.run(ctx.user_id, query)
    return response


# ── DEFINICIÓN DE TOOLS PARA EL PROTOCOLO MCP ───────────────────────────────
# Cada entry describe la tool al cliente MCP: nombre, descripción, parámetros.
# La estructura sigue JSON Schema (la usa el protocolo MCP).

TOOL_DEFINITIONS = [
    {
        "name": "claw_recall_memory",
        "description": (
            "Busca en la memoria personal del usuario usando dual-index (texto + "
            "multimodal). Útil para recordar conversaciones pasadas, documentos "
            "compartidos, contexto histórico."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Búsqueda en lenguaje natural"},
                "top_k": {"type": "integer", "default": 5, "description": "Cuántos resultados devolver"},
            },
            "required": ["query"],
        },
        "handler": tool_recall_memory,
    },
    {
        "name": "claw_get_profile",
        "description": (
            "Devuelve el perfil del usuario: preferencias, intereses, contexto, "
            "datos personales que ha compartido con ClawLite. Útil para personalizar respuestas."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_get_profile,
    },
    {
        "name": "claw_get_brand",
        "description": (
            "Devuelve el perfil de marca/negocio del usuario si lo ha configurado "
            "(nombre del negocio, tono, audiencia, productos, plataformas). Útil "
            "para generar contenido alineado a su marca."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_get_brand,
    },
    {
        "name": "claw_list_workflows",
        "description": (
            "Lista los workflows aprendidos automáticamente por ClawLite del comportamiento "
            "del usuario. Son recetas reutilizables como 'responder correos del banco' o "
            "'generar resumen semanal'."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_list_workflows,
    },
    {
        "name": "claw_run_workflow",
        "description": (
            "Ejecuta un workflow aprendido por su nombre. Devuelve el output del workflow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workflow_name": {"type": "string", "description": "Nombre del workflow a ejecutar"},
                "message": {"type": "string", "description": "Mensaje de invocación (opcional)", "default": ""},
            },
            "required": ["workflow_name"],
        },
        "handler": tool_run_workflow,
    },
    {
        "name": "claw_create_job",
        "description": (
            "Crea un job asíncrono que corre en background dentro de ClawLite. "
            "Tipos: 'research' (investigación profunda multi-agente), 'coding' (genera "
            "proyecto de código con tests en sandbox Docker), 'brand_calendar' (calendario "
            "de contenido para la marca del usuario). Devuelve el job_id para consultarlo después."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "Descripción de la tarea"},
                "job_type": {
                    "type": "string",
                    "enum": ["research", "coding", "brand_calendar"],
                    "default": "research",
                },
            },
            "required": ["request"],
        },
        "handler": tool_create_job,
    },
    {
        "name": "claw_get_job_status",
        "description": (
            "Consulta el estado de un job creado con claw_create_job. Devuelve estado "
            "(queued/running/completed/failed), progreso y resultado si está terminado."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer", "description": "ID del job"},
            },
            "required": ["job_id"],
        },
        "handler": tool_get_job_status,
    },
    {
        "name": "claw_execute_in_sandbox",
        "description": (
            "Ejecuta código Python en un sandbox Docker aislado (sin acceso al sistema host). "
            "Útil para cálculos, transformaciones de datos, scripts ad-hoc. Use networked=True "
            "para permitir acceso a internet (pip install, APIs externas)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Código Python a ejecutar"},
                "networked": {"type": "boolean", "default": False, "description": "Permitir red"},
            },
            "required": ["code"],
        },
        "handler": tool_execute_in_sandbox,
    },
    {
        "name": "claw_research",
        "description": (
            "Investigación síncrona profunda usando el sistema multi-agente de ClawLite "
            "(búsqueda web + scraping + fact-checking + síntesis). Toma ~30-60 segundos. "
            "Para investigaciones más largas o que no requieren respuesta inmediata, "
            "usa claw_create_job con job_type='research'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Pregunta o tema a investigar"},
            },
            "required": ["query"],
        },
        "handler": tool_research,
    },
]
