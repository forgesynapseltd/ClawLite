"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

tests/test_ollama_install.py — Regresión de la instalación automática de
Ollama (clawlite/setup_checks.py:download_and_install_ollama y el wizard
en setup_web.py). Solo se mockea la frontera no determinista/pesada real
(la descarga HTTP de ~1.36GB y la ejecución del instalador silencioso) --
el resto (escritura real a archivo, callback de progreso, idempotencia
del wizard) se ejercita real, mismo criterio que counting_check en
test_setup_web.py.
"""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import clawlite.setup_checks as setup_checks
from clawlite.setup_checks import CheckResult, CheckStatus, download_and_install_ollama


class _FakeStreamResponse:
    """Imita el objeto que devuelve httpx.stream(...) como context manager --
    reparte un contenido falso en 3 chunks, con un content-length real."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.headers = {"content-length": str(sum(len(c) for c in chunks))}

    def raise_for_status(self):
        pass

    def iter_bytes(self, chunk_size):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_and_install_ollama_reports_real_progress(tmp_path, monkeypatch):
    """El callback de progreso debe recibir el acumulado real de bytes
    descargados, y el archivo temporal debe contener el contenido completo
    antes de intentar correr el instalador."""
    chunks = [b"A" * 1000, b"B" * 2000, b"C" * 500]
    written_content = {}

    def fake_stream(method, url, **kwargs):
        return _FakeStreamResponse(chunks)

    def fake_run(args, timeout, check):
        # En este punto el archivo ya debe existir con el contenido completo.
        with open(args[0], "rb") as f:
            written_content["data"] = f.read()
        assert args[1] == "/VERYSILENT"

    monkeypatch.setattr(setup_checks.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(setup_checks.httpx, "stream", fake_stream)
    monkeypatch.setattr(setup_checks.subprocess, "run", fake_run)
    monkeypatch.setattr(setup_checks, "check_ollama", lambda model: CheckResult(CheckStatus.OK))

    progress_calls = []
    result = download_and_install_ollama(progress_cb=lambda d, t: progress_calls.append((d, t)))

    assert result.status == CheckStatus.OK
    assert written_content["data"] == b"".join(chunks)
    total = sum(len(c) for c in chunks)
    assert progress_calls[-1] == (total, total)
    # el progreso debe ser acumulativo, no el tamaño de cada chunk suelto
    assert progress_calls[0] == (1000, total)
    assert progress_calls[1] == (3000, total)


def test_download_and_install_ollama_download_failure_returns_clear_error(monkeypatch):
    def fake_stream(method, url, **kwargs):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(setup_checks.httpx, "stream", fake_stream)

    result = download_and_install_ollama()

    assert result.status == CheckStatus.NOT_INSTALLED
    assert "no se pudo descargar" in result.detail.lower()


def test_download_and_install_ollama_installer_failure_returns_clear_error(tmp_path, monkeypatch):
    def fake_stream(method, url, **kwargs):
        return _FakeStreamResponse([b"fake installer bytes"])

    def fake_run(args, timeout, check):
        raise RuntimeError("el instalador silencioso fallo (simulado)")

    monkeypatch.setattr(setup_checks.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(setup_checks.httpx, "stream", fake_stream)
    monkeypatch.setattr(setup_checks.subprocess, "run", fake_run)

    result = download_and_install_ollama()

    assert result.status == CheckStatus.NOT_INSTALLED
    assert "no pudo completarse" in result.detail.lower()


# ── Wizard: idempotencia y progreso end-to-end ──────────────────────────

@pytest.fixture
def fake_install(monkeypatch):
    """Reemplaza download_and_install_ollama por una versión falsa e
    instrumentada -- cuenta invocaciones reales y reporta progreso
    determinista, sin descargar nada real ni depender de la red."""
    import clawlite.setup_web as setup_web

    calls = {"count": 0}

    def fake(progress_cb=None):
        calls["count"] += 1
        if progress_cb:
            progress_cb(50, 100)
        return CheckResult(CheckStatus.OK)

    monkeypatch.setattr(setup_web, "download_and_install_ollama", fake)
    monkeypatch.setattr(setup_web, "_tasks", {
        "docker": {"state": setup_web.TaskState.IDLE, "result": None},
        "ollama": {"state": setup_web.TaskState.IDLE, "result": None},
        "ollama_install": {"state": setup_web.TaskState.IDLE, "result": None},
    })
    monkeypatch.setattr(setup_web, "_ollama_install_progress", {"downloaded": 0, "total": 0})
    yield calls


def _wait_ollama_install_done(client, timeout=5):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get("/install-ollama/status").json()
        if r["state"] == "done":
            return r
        time.sleep(0.05)
    raise TimeoutError("install-ollama no llegó a 'done' a tiempo")


def test_install_ollama_is_idempotent(fake_install):
    import clawlite.setup_web as setup_web
    with TestClient(setup_web.app) as client:
        client.post("/install-ollama/start")
        client.post("/install-ollama/start")  # segundo click rápido -- no debe relanzar
        _wait_ollama_install_done(client)
        assert fake_install["count"] == 1


def test_install_ollama_status_reports_progress(fake_install):
    import clawlite.setup_web as setup_web
    with TestClient(setup_web.app) as client:
        client.post("/install-ollama/start")
        r = _wait_ollama_install_done(client)
        assert r["status"] == "ok"
        assert r["downloaded"] == 50
        assert r["total"] == 100
