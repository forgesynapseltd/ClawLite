"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

mcp/auth.py — Autenticación por token bearer para el transport SSE
En modo stdio no hace falta auth (el cliente es un subproceso local del mismo
usuario). En modo SSE el server escucha en un puerto, así que validamos cada
request contra un token bearer configurado en MCP_TOKEN.
"""

import secrets
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from loguru import logger
from mcp.server.sse import SseServerTransport


def generate_token() -> str:
    """Genera un token random seguro de 32 bytes (usado si MCP_TOKEN no está set)."""
    return secrets.token_urlsafe(32)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """
    Valida que cada request traiga Authorization: Bearer <token> con el token
    esperado. Si falta o no coincide, devuelve 401 sin filtrar información.
    Logea intentos fallidos para auditoría.
    """

    def __init__(self, app, expected_token: str):
        super().__init__(app)
        self.expected_token = expected_token

    async def dispatch(self, request: Request, call_next):
        auth_header = request.headers.get("authorization", "")
        client_ip = request.client.host if request.client else "?"

        if not auth_header.startswith("Bearer "):
            logger.warning(f"🚫 MCP SSE auth missing from {client_ip} on {request.url.path}")
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        token = auth_header[len("Bearer "):].strip()
        # Comparación constante en el tiempo para evitar timing attacks
        if not secrets.compare_digest(token, self.expected_token):
            logger.warning(f"🚫 MCP SSE auth invalid from {client_ip} on {request.url.path}")
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        return await call_next(request)


def build_authenticated_sse_app(mcp_server, token: str) -> Starlette:
    """
    Construye una app Starlette que sirve el MCP server por SSE con autenticación
    bearer obligatoria. Devuelve la app lista para uvicorn.
    """
    if not token:
        raise ValueError("Token bearer es obligatorio para servir MCP por SSE")

    # SSE transport del SDK MCP
    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> Response:
        """Endpoint que mantiene la conexión SSE viva con un cliente."""
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send,
        ) as (read_stream, write_stream):
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )
        return Response()

    app = Starlette(
        debug=False,
        middleware=[Middleware(BearerAuthMiddleware, expected_token=token)],
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ],
    )
    return app
