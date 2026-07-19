"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

tests/test_setup_web.py — Regresión del modelo de tareas del wizard web
(clawlite/setup_web.py). Cubre la propiedad de idempotencia de
POST /check/{name}/start: es un contrato arquitectónico del wizard, no
solo un detalle de implementación -- un refactor futuro no debería poder
romperlo sin que la suite lo detecte.

También cubre la secuencia de dos pasos del formulario de bootstrap:
TELEGRAM_OWNER_ID no puede pedirse junto con TELEGRAM_BOT_TOKEN porque
su captura automática necesita el token YA guardado en disco para
llamar a la API real de Telegram -- bug real encontrado validando el
wizard empaquetado de punta a punta (el formulario original pedía los
3 campos juntos, dejando la auto-captura ya construida inalcanzable).
Solo se mockea la llamada de red a Telegram (_claim_telegram_owner_arm/
_poll) -- el resto (routing real de Starlette, lectura/escritura real
de archivo .env) se ejercita sin mocks, mismo criterio que counting_check.
"""

import threading
import time

import httpx
import pytest
from starlette.testclient import TestClient

from clawlite import setup_web
from clawlite.setup_checks import CheckResult, CheckStatus


@pytest.fixture
def counting_check():
    """Reemplaza el check real por uno falso que cuenta cuántas veces se
    ejecutó de verdad -- así se puede probar la idempotencia sin depender
    de Docker/Ollama reales ni de los timeouts largos de los smoke tests."""
    calls = {"count": 0}

    def fake_check():
        calls["count"] += 1
        time.sleep(0.3)  # simula trabajo real, para que dos /start rápidos puedan solaparse
        return CheckResult(CheckStatus.OK)

    original_funcs = dict(setup_web._CHECK_FUNCS)
    original_tasks = dict(setup_web._tasks)

    setup_web._CHECK_FUNCS["docker"] = fake_check
    setup_web._tasks["docker"] = {"state": setup_web.TaskState.IDLE, "result": None}

    yield calls

    # Restaurar estado global para no contaminar otros tests.
    setup_web._CHECK_FUNCS.clear()
    setup_web._CHECK_FUNCS.update(original_funcs)
    setup_web._tasks.clear()
    setup_web._tasks.update(original_tasks)


def _wait_until_done(client, name, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/check/{name}/status").json()
        if r["state"] == "done":
            return r
        time.sleep(0.05)
    raise TimeoutError(f"'{name}' no llegó a 'done' dentro de {timeout}s")


def test_double_start_is_idempotent_single_execution(counting_check):
    """
    Contrato: dos POST /check/{name}/start consecutivos (mientras el primero
    todavía está corriendo) NO deben lanzar dos ejecuciones independientes
    del check real -- la segunda llamada debe ser un no-op que solo lee el
    estado actual.
    """
    with TestClient(setup_web.app) as client:
        r1 = client.post("/check/docker/start")
        assert r1.json()["state"] == "running"

        r2 = client.post("/check/docker/start")
        assert r2.json()["state"] == "running"  # idempotente: sigue running, no relanzó

        _wait_until_done(client, "docker")

        assert counting_check["count"] == 1, (
            f"Se esperaba exactamente 1 ejecución real del check, pero hubo {counting_check['count']} "
            "-- la idempotencia de /start se rompió."
        )


def test_start_after_done_does_not_rerun_without_force(counting_check):
    """Una vez DONE, un /start sin force=true no debe volver a ejecutar el check."""
    with TestClient(setup_web.app) as client:
        client.post("/check/docker/start")
        _wait_until_done(client, "docker")
        assert counting_check["count"] == 1

        r = client.post("/check/docker/start")
        assert r.json()["state"] == "done"
        assert counting_check["count"] == 1, "No debía volver a ejecutar el check sin force=true."


def test_start_with_force_reruns(counting_check):
    """Con force=true, sí debe volver a ejecutar el check aunque ya esté DONE."""
    with TestClient(setup_web.app) as client:
        client.post("/check/docker/start")
        _wait_until_done(client, "docker")
        assert counting_check["count"] == 1

        client.post("/check/docker/start?force=true")
        _wait_until_done(client, "docker")
        assert counting_check["count"] == 2, "force=true debía relanzar una segunda ejecución real."


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Aísla ENV_PATH/ENV_EXAMPLE_PATH del .env real de la máquina -- estas
    pruebas escriben/leen un archivo de verdad (no mockean el I/O), solo
    que descartable, para ejercitar el guardado secuencial real."""
    env_example = tmp_path / ".env.example"
    env_example.write_text(
        "# Crea tu bot en @BotFather\n"
        "TELEGRAM_BOT_TOKEN=\n"
        "# Plan gratuito en tavily.com\n"
        "TAVILY_API_KEY=\n"
        "# Mandale un mensaje a @userinfobot\n"
        "TELEGRAM_OWNER_ID=\n",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"

    monkeypatch.setattr(setup_web, "ENV_PATH", env_path)
    monkeypatch.setattr(setup_web, "ENV_EXAMPLE_PATH", env_example)
    monkeypatch.setattr(setup_web, "_owner_claim_since", None)
    yield env_path


def test_form_first_step_omits_owner_id(isolated_env):
    """Con todo vacío, el primer /form debe pedir TOKEN y TAVILY, pero
    NUNCA TELEGRAM_OWNER_ID en ese mismo paso -- pedirlo junto rompería
    la captura automática (necesita el token ya en disco)."""
    with TestClient(setup_web.app) as client:
        r = client.get("/form")
        assert 'name="TELEGRAM_BOT_TOKEN"' in r.text
        assert 'name="TAVILY_API_KEY"' in r.text
        assert 'name="TELEGRAM_OWNER_ID"' not in r.text


def test_save_partial_then_form_shows_owner_capture_step(isolated_env):
    """Tras guardar TOKEN+TAVILY, /form debe pasar al paso de captura de
    OWNER_ID (ya con el token persistido en disco), no a /done."""
    with TestClient(setup_web.app) as client:
        client.get("/form")  # crea .env desde .env.example, como en el flujo real
        r = client.post(
            "/save",
            data={"TELEGRAM_BOT_TOKEN": "fake-token", "TAVILY_API_KEY": "fake-key"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/form"

        r = client.get("/form")
        assert "Armar captura automática" in r.text
        assert 'name="TELEGRAM_BOT_TOKEN"' not in r.text  # ya guardado, no se vuelve a pedir


def test_owner_capture_success_writes_env_and_unlocks_done(isolated_env, monkeypatch):
    """Simula un ciclo arm+poll exitoso (solo se mockea la llamada de red
    a Telegram) y confirma que el .env real queda escrito y que /form
    redirige a /done una vez completo."""
    monkeypatch.setattr(setup_web, "_claim_telegram_owner_arm", lambda token: 1234567890)
    monkeypatch.setattr(setup_web, "_claim_telegram_owner_poll", lambda token, since: "999888777")

    with TestClient(setup_web.app) as client:
        client.get("/form")  # crea .env desde .env.example, como en el flujo real
        client.post("/save", data={"TELEGRAM_BOT_TOKEN": "fake-token", "TAVILY_API_KEY": "fake-key"})

        r_arm = client.post("/claim-telegram-owner/arm")
        assert r_arm.json() == {"armed": True}

        r_poll = client.post("/claim-telegram-owner/poll")
        assert r_poll.json() == {"claimed": True, "user_id": "999888777"}

        assert "TELEGRAM_OWNER_ID=999888777" in isolated_env.read_text(encoding="utf-8")

        r = client.get("/form", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/done"


def test_owner_capture_manual_fallback_still_works(isolated_env):
    """Si el usuario prefiere no usar la auto-captura, el formulario manual
    de respaldo (mismo /save genérico) debe seguir funcionando."""
    with TestClient(setup_web.app) as client:
        client.get("/form")  # crea .env desde .env.example, como en el flujo real
        client.post("/save", data={"TELEGRAM_BOT_TOKEN": "fake-token", "TAVILY_API_KEY": "fake-key"})

        r = client.post("/save", data={"TELEGRAM_OWNER_ID": "111222333"}, follow_redirects=False)
        assert r.status_code == 303

        r = client.get("/form", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/done"


def test_done_shuts_down_the_real_server_within_bounded_time():
    """
    Contrato del launcher: run() debe RETORNAR después de que /done dispara
    el apagado (server.should_exit = True) -- si no, launcher.main() nunca
    llegaría a arrancar el bot real después del wizard. Se levanta un
    servidor real (no TestClient, que no ejercita uvicorn.Server de
    verdad) en un puerto de prueba, se pega /done, y se exige que el hilo
    donde corre run() termine dentro de un tiempo acotado.
    """
    port = 8799
    thread = threading.Thread(target=setup_web.run, kwargs={"port": port}, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)
    else:
        pytest.fail("El servidor real no arrancó a tiempo.")

    httpx.get(f"http://127.0.0.1:{port}/done", timeout=5)

    thread.join(timeout=5)
    assert not thread.is_alive(), (
        "run() no retornó dentro de 5s tras /done -- el apagado programado "
        "(should_exit) dejó de funcionar, lo que bloquearía al launcher "
        "indefinidamente después del wizard."
    )
