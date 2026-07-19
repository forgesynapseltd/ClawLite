"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

mcp/server.py — MCP Server que expone las capacidades de ClawLite a clientes externos
(Claude Desktop, Cursor, agentes de terceros, etc.)

Modos de transport:
  - stdio: el cliente arranca ClawLite como subproceso y se comunica por stdin/stdout.
    Es el modo estándar para Claude Desktop. Cero exposición de red.
  - sse:   ClawLite escucha en un puerto HTTP. Útil para conectar Claude Desktop
    en otro PC al mismo ClawLite en LAN. Requiere token bearer obligatorio.

Diseño: el server se registra como asyncio.Task paralela al bot Telegram. Comparten
la misma memoria, workflows, sandbox, etc. Una sola DB, dos interfaces.
"""

import asyncio
import json
from loguru import logger
from mcp.server import Server
from mcp import types
from clawlite.mcp.tools import ToolContext, TOOL_DEFINITIONS


class ClawLiteMCPServer:
    """
    Wrapper sobre el Server de la SDK MCP. Construye el ToolContext con las
    dependencias inyectadas y registra las 9 tools de ClawLite.

    La instancia es reutilizable entre transports (stdio o sse): se construye una
    sola vez y luego se llama a serve_stdio() o serve_sse() según config.
    """

    SERVER_NAME = "clawlite"
    SERVER_VERSION = "1.0.0"

    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self.server: Server = Server(self.SERVER_NAME)
        self._register_handlers()

    def _register_handlers(self):
        """Registra los handlers de list_tools y call_tool sobre la instancia Server."""

        # Mapa nombre → handler para dispatch O(1)
        handlers = {td["name"]: td["handler"] for td in TOOL_DEFINITIONS}

        @self.server.list_tools()
        async def handle_list_tools() -> list[types.Tool]:
            """Devuelve la lista de tools disponibles en formato MCP."""
            return [
                types.Tool(
                    name=td["name"],
                    description=td["description"],
                    inputSchema=td["inputSchema"],
                )
                for td in TOOL_DEFINITIONS
            ]

        @self.server.call_tool()
        async def handle_call_tool(
            name: str,
            arguments: dict | None,
        ) -> list[types.TextContent]:
            """
            Recibe una invocación del cliente, dispatch al handler correspondiente,
            devuelve el resultado envuelto en TextContent (formato MCP).

            Cualquier excepción del handler se captura aquí y se devuelve como
            mensaje de error legible (nunca propagamos crashes al cliente).
            """
            arguments = arguments or {}
            logger.info(f"🔌 MCP call: {name} args={list(arguments.keys())}")

            handler = handlers.get(name)
            if handler is None:
                return [types.TextContent(
                    type="text",
                    text=f"❌ Tool desconocida: '{name}'. Disponibles: {list(handlers.keys())}",
                )]

            try:
                result = await handler(self.ctx, **arguments)
                # Garantizar que el resultado sea string (las tools devuelven str o JSON-str)
                if not isinstance(result, str):
                    result = json.dumps(result, ensure_ascii=False, default=str)
                return [types.TextContent(type="text", text=result)]

            except TypeError as e:
                # Argumentos faltantes o tipos incorrectos del cliente
                return [types.TextContent(
                    type="text",
                    text=f"❌ Argumentos inválidos para '{name}': {e}",
                )]

            except Exception as e:
                logger.exception(f"MCP tool '{name}' falló")
                return [types.TextContent(
                    type="text",
                    text=f"❌ Error ejecutando '{name}': {type(e).__name__}: {e}",
                )]

    # ── Transports ───────────────────────────────────────────────────────────

    async def serve_stdio(self):
        """
        Sirve por stdio. Bloquea hasta que el cliente cierra la conexión.
        Es el modo recomendado para Claude Desktop y similares: el cliente arranca
        ClawLite como subproceso y se comunica por stdin/stdout.
        """
        from mcp.server.stdio import stdio_server

        logger.info(f"🔌 MCP server arrancando en modo stdio ({len(TOOL_DEFINITIONS)} tools)")

        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )

    async def serve_sse(self, host: str = "127.0.0.1", port: int = 8765, token: str = ""):
        """
        Sirve por SSE/HTTP. Útil para conectar clientes en LAN.
        Requiere token bearer en cada request (validado en auth.py).
        """
        from clawlite.mcp.auth import build_authenticated_sse_app

        app = build_authenticated_sse_app(self.server, token)

        # Servir con uvicorn programáticamente (no bloquea el event loop principal)
        import uvicorn
        cfg = uvicorn.Config(
            app, host=host, port=port, log_level="warning", access_log=False
        )
        server = uvicorn.Server(cfg)
        logger.info(
            f"🔌 MCP server arrancando en modo SSE en http://{host}:{port}/sse "
            f"({len(TOOL_DEFINITIONS)} tools, token requerido)"
        )
        await server.serve()


# ── Helper público: crear y arrancar el server desde main.py ────────────────

def build_tool_context(
    user_id: str,
    memory_store,
    deep_memory,
    profile,
    brand_manager,
    dual_index,
    workflow_store,
    workflow_executor,
    orchestrator,
    job_store,
    agent_sandbox_cls,
) -> ToolContext:
    """Construye el ToolContext con todas las dependencias inyectadas."""
    return ToolContext(
        user_id=user_id,
        memory_store=memory_store,
        deep_memory=deep_memory,
        profile=profile,
        brand_manager=brand_manager,
        dual_index=dual_index,
        workflow_store=workflow_store,
        workflow_executor=workflow_executor,
        orchestrator=orchestrator,
        job_store=job_store,
        agent_sandbox_cls=agent_sandbox_cls,
    )


async def run_mcp_server(
    ctx: ToolContext,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str = "",
):
    """
    Arranca el MCP server. Llamar como asyncio.Task desde main.py.

    transport: 'stdio' o 'sse'
    """
    server = ClawLiteMCPServer(ctx)

    if transport == "stdio":
        await server.serve_stdio()
    elif transport == "sse":
        if not token:
            raise ValueError("MCP_TOKEN es obligatorio en modo SSE (seguridad)")
        await server.serve_sse(host=host, port=port, token=token)
    else:
        raise ValueError(f"Transport MCP desconocido: '{transport}'. Use 'stdio' o 'sse'.")
