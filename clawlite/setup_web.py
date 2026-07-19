"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

setup_web.py — Wizard web local que reemplaza el wizard de terminal de
setup.py. Sirve en 127.0.0.1 únicamente, nunca expuesto a la red.

CONTRATO ARQUITECTÓNICO: el registro de tareas en memoria (TaskState/_tasks)
es válido SOLO porque este wizard es de una única instancia local -- un
solo usuario, proceso efímero, bind a 127.0.0.1. Si este componente se
reutilizara para múltiples usuarios simultáneos o se expusiera en red,
el modelo de 2 claves fijas en memoria dejaría de ser válido y haría
falta otro mecanismo de coordinación (por usuario/sesión).
"""

import asyncio
import shutil
from enum import Enum

from loguru import logger
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route
from starlette.requests import Request

from clawlite.setup import (
    ENV_PATH,
    ENV_EXAMPLE_PATH,
    _read_env_values,
    _read_env_hints,
    _write_env_value,
    _claim_telegram_owner_arm,
    _claim_telegram_owner_poll,
)
from clawlite.setup_checks import check_docker, check_ollama


class TaskState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"


# Registro de tareas -- ver CONTRATO ARQUITECTÓNICO en el docstring del módulo.
_tasks: dict = {
    "docker": {"state": TaskState.IDLE, "result": None},
    "ollama": {"state": TaskState.IDLE, "result": None},
}

_owner_claim_since: int | None = None


def _run_ollama_check():
    from clawlite.config import config
    return check_ollama(config.OLLAMA_MODEL)


_CHECK_FUNCS = {
    "docker": check_docker,
    "ollama": _run_ollama_check,
}


async def _run_check_background(name: str):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _CHECK_FUNCS[name])
    _tasks[name] = {"state": TaskState.DONE, "result": result}


def _task_json(name: str) -> dict:
    task = _tasks[name]
    result = task["result"]
    return {
        "state": task["state"].value,
        "status": result.status.value if result else None,
        "detail": result.detail if result else "",
    }


async def start_check(request: Request):
    name = request.path_params["name"]
    if name not in _tasks:
        return JSONResponse({"error": "unknown check"}, status_code=404)

    force = request.query_params.get("force") == "true"
    current = _tasks[name]

    if current["state"] == TaskState.RUNNING:
        pass  # idempotente: ya está corriendo, no lanzar otra
    elif current["state"] == TaskState.DONE and not force:
        pass  # idempotente: ya hay resultado cacheado, no volver a correr
    else:
        _tasks[name] = {"state": TaskState.RUNNING, "result": None}
        asyncio.create_task(_run_check_background(name))

    return JSONResponse(_task_json(name))


async def check_status(request: Request):
    name = request.path_params["name"]
    if name not in _tasks:
        return JSONResponse({"error": "unknown check"}, status_code=404)
    return JSONResponse(_task_json(name))


async def claim_owner_arm(request: Request):
    global _owner_claim_since
    current = _read_env_values(ENV_PATH)
    token = current.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return JSONResponse({"error": "TELEGRAM_BOT_TOKEN no configurado todavía"}, status_code=400)

    loop = asyncio.get_event_loop()
    since = await loop.run_in_executor(None, _claim_telegram_owner_arm, token)
    if since is None:
        return JSONResponse({"error": "No pude contactar la API de Telegram"}, status_code=502)

    _owner_claim_since = since
    return JSONResponse({"armed": True})


async def claim_owner_poll(request: Request):
    current = _read_env_values(ENV_PATH)
    token = current.get("TELEGRAM_BOT_TOKEN", "")
    if not token or _owner_claim_since is None:
        return JSONResponse({"error": "Todavía no se armó la captura -- llamá a /claim-telegram-owner/arm primero"}, status_code=400)

    loop = asyncio.get_event_loop()
    user_id = await loop.run_in_executor(None, _claim_telegram_owner_poll, token, _owner_claim_since)
    if user_id:
        _write_env_value(ENV_PATH, "TELEGRAM_OWNER_ID", user_id)
        return JSONResponse({"claimed": True, "user_id": user_id})
    return JSONResponse({"claimed": False})


def _page(body: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>ClawLite — Configuración inicial</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 20px; color: #222; }}
h1 {{ font-size: 1.4em; }}
.field {{ margin-bottom: 16px; }}
label {{ display: block; font-weight: 600; margin-bottom: 4px; }}
.hint {{ color: #666; font-size: 0.85em; margin-bottom: 4px; }}
input[type=text] {{ width: 100%; padding: 8px; box-sizing: border-box; }}
button {{ padding: 10px 20px; cursor: pointer; }}
.status {{ padding: 8px 12px; border-radius: 4px; margin: 8px 0; }}
.status.ok {{ background: #d4edda; }}
.status.running {{ background: #fff3cd; }}
.status.failed {{ background: #f8d7da; }}
</style></head>
<body>{body}</body></html>"""


async def home(request: Request):
    body = """
    <h1>🔧 ClawLite — Configuración inicial</h1>
    <p>Antes de empezar, verifico que Docker y Ollama estén listos.</p>
    <div id="docker-status" class="status">Docker: sin verificar</div>
    <div id="ollama-status" class="status">Ollama: sin verificar</div>
    <button onclick="startChecks()">Verificar</button>
    <p><a href="/form">Continuar a la configuración →</a></p>
    <script>
    async function poll(name) {
        const el = document.getElementById(name + '-status');
        while (true) {
            const r = await fetch('/check/' + name + '/status').then(r => r.json());
            if (r.state === 'done') {
                el.className = 'status ' + (r.status === 'ok' ? 'ok' : 'failed');
                el.textContent = name + ': ' + r.status + (r.detail ? ' — ' + r.detail : '');
                return;
            }
            el.className = 'status running';
            el.textContent = name + ': verificando…';
            await new Promise(res => setTimeout(res, 2000));
        }
    }
    async function startChecks() {
        await fetch('/check/docker/start', {method: 'POST'});
        await fetch('/check/ollama/start', {method: 'POST'});
        poll('docker');
        poll('ollama');
    }
    </script>
    """
    return HTMLResponse(_page(body))


def _owner_capture_body() -> str:
    """TELEGRAM_OWNER_ID no puede pedirse en el mismo paso que
    TELEGRAM_BOT_TOKEN: la captura automática necesita el token YA
    guardado en disco para poder llamar a la API real de Telegram (ver
    _claim_telegram_owner_arm/_poll en setup.py). Mismo orden que ya usa
    con éxito el flujo de terminal (setup.py:main() guarda cada clave
    apenas la obtiene, así que para cuando llega a OWNER_ID el token ya
    está escrito) -- esto replica esa misma secuencia para el wizard web."""
    return """
    <h1>🔧 Último paso: identificarte</h1>
    <p>Para que el bot responda solo a vos, necesito tu ID numérico de
    Telegram. Hacé click y después mandale <b>cualquier mensaje</b> a tu
    bot desde Telegram.</p>
    <button onclick="arm()">Armar captura automática</button>
    <div id="capture-status" class="status"></div>
    <p style="margin-top:24px;">¿Preferís hacerlo manual? Mandale un
    mensaje a <b>@userinfobot</b> para conseguir tu ID y
    <a href="#" onclick="showManual(); return false;">ingresalo acá</a>.</p>
    <form id="manual-form" method="post" action="/save" style="display:none;">
        <div class="field">
            <label for="TELEGRAM_OWNER_ID">TELEGRAM_OWNER_ID</label>
            <input type="text" id="TELEGRAM_OWNER_ID" name="TELEGRAM_OWNER_ID" required>
        </div>
        <button type="submit">Guardar</button>
    </form>
    <script>
    function showManual() {
        document.getElementById('manual-form').style.display = 'block';
    }
    async function arm() {
        const el = document.getElementById('capture-status');
        el.className = 'status running';
        el.textContent = 'Armando...';
        const r = await fetch('/claim-telegram-owner/arm', {method: 'POST'}).then(r => r.json());
        if (r.error) {
            el.className = 'status failed';
            el.textContent = r.error;
            return;
        }
        el.textContent = 'Listo -- mandale un mensaje a tu bot ahora.';
        poll();
    }
    async function poll() {
        const el = document.getElementById('capture-status');
        while (true) {
            const r = await fetch('/claim-telegram-owner/poll', {method: 'POST'}).then(r => r.json());
            if (r.claimed) {
                el.className = 'status ok';
                el.textContent = 'Capturado: ' + r.user_id;
                window.location.href = '/form';
                return;
            }
            await new Promise(res => setTimeout(res, 2000));
        }
    }
    </script>
    """


async def form_page(request: Request):
    from clawlite.config import BOOTSTRAP_REQUIRED_KEYS

    if not ENV_PATH.exists():
        if not ENV_EXAMPLE_PATH.exists():
            return HTMLResponse(_page("<h1>❌ No encontré .env.example — repo incompleto.</h1>"), status_code=500)
        shutil.copy(ENV_EXAMPLE_PATH, ENV_PATH)

    current = _read_env_values(ENV_PATH)
    missing = [k for k in BOOTSTRAP_REQUIRED_KEYS if not current.get(k)]

    if not missing:
        return RedirectResponse(url="/done", status_code=303)

    if missing == ["TELEGRAM_OWNER_ID"]:
        return HTMLResponse(_page(_owner_capture_body()))

    text_missing = [k for k in missing if k != "TELEGRAM_OWNER_ID"]
    hints = _read_env_hints(ENV_EXAMPLE_PATH)
    fields = []
    for key in text_missing:
        hint = hints.get(key, "")
        fields.append(f"""
        <div class="field">
            <label for="{key}">{key}</label>
            {f'<div class="hint">{hint}</div>' if hint else ''}
            <input type="text" id="{key}" name="{key}" required>
        </div>
        """)

    body = f"""
    <h1>🔧 Datos necesarios</h1>
    <form method="post" action="/save">
        {''.join(fields)}
        <button type="submit">Guardar</button>
    </form>
    """
    return HTMLResponse(_page(body))


async def save_form(request: Request):
    form = await request.form()
    for key, value in form.items():
        value = str(value).strip()
        if value:
            _write_env_value(ENV_PATH, key, value)
    return RedirectResponse(url="/form", status_code=303)


async def _shutdown_after_delay():
    """Da tiempo a que la respuesta HTTP de /done llegue al navegador antes
    de apagar el servidor -- si no, el launcher (que espera a que run()
    retorne para seguir con el bot real) podría cortar la conexión a
    mitad de la respuesta."""
    await asyncio.sleep(1.0)
    if _server is not None:
        _server.should_exit = True


async def done_page(request: Request):
    body = """
    <h1>✅ Configuración guardada</h1>
    <p>ClawLite ya está listo. Esta ventana se puede cerrar.</p>
    """
    asyncio.create_task(_shutdown_after_delay())
    return HTMLResponse(_page(body))


app = Starlette(
    routes=[
        Route("/", home),
        Route("/check/{name}/start", start_check, methods=["POST"]),
        Route("/check/{name}/status", check_status),
        Route("/claim-telegram-owner/arm", claim_owner_arm, methods=["POST"]),
        Route("/claim-telegram-owner/poll", claim_owner_poll, methods=["POST"]),
        Route("/form", form_page),
        Route("/save", save_form, methods=["POST"]),
        Route("/done", done_page),
    ]
)


_server = None  # instancia viva de uvicorn.Server, para que /done pueda pedirle que se apague


def run(host: str = "127.0.0.1", port: int = 8710):
    """
    Bloqueante -- retorna solo cuando /done pide el apagado (should_exit),
    o si el usuario corta el proceso. El launcher depende de que ESTA
    llamada retorne para recién ahí seguir con clawlite.main.main() --
    por eso no se usa uvicorn.run() (no deja forma de pararlo desde
    adentro), sino Config+Server con la instancia guardada.
    """
    global _server
    import uvicorn
    # logger, no print(): empaquetado con PyInstaller la consola de Windows
    # puede no ser UTF-8 (o no existir siquiera, en un build sin consola) --
    # confirmado con un build real que un print() con emoji tira
    # UnicodeEncodeError y mata el proceso. loguru ya maneja esto con más
    # tolerancia (degrada, no se cae) y es el mecanismo de logging que ya
    # usa el resto del proyecto.
    logger.info(f"🔧 Wizard de configuración en http://{host}:{port} — abrilo en tu navegador.")
    cfg = uvicorn.Config(app, host=host, port=port, log_level="warning")
    _server = uvicorn.Server(cfg)
    _server.run()
    _server = None


if __name__ == "__main__":
    run()
