"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

tests/test_agent_sandbox_readonly.py — Integración real del endurecimiento
--read-only de AgentSandbox (Expediente 4 del barrido de seguridad).
Requiere Docker; se salta automáticamente si no está disponible.
"""

import pytest
from clawlite.sandbox.agent_sandbox import AgentSandbox
from clawlite.sandbox.docker_manager import docker_sandbox

pytestmark = pytest.mark.skipif(
    not docker_sandbox.is_docker_available(),
    reason="Docker no disponible en este entorno",
)


def test_workspace_write_read_and_pytest_cache_still_work():
    with AgentSandbox(networked=False) as sandbox:
        assert sandbox._started
        assert sandbox.write_file("utils.py", "def add(a, b):\n    return a + b\n")
        assert sandbox.write_file("test_utils.py", "from utils import add\ndef test_add():\n    assert add(1, 2) == 3\n")
        assert sandbox.read_file("utils.py") is not None

        result = sandbox.exec("pytest -v", timeout=60)
        assert result.success, result.output


def test_native_extension_package_installs_and_imports():
    """
    Confirma que el tmpfs de ~/.local con 'exec' permite cargar extensiones
    compiladas (.so) -- sin 'exec' esto falla con
    ImportError: failed to map segment from shared object (confirmado
    empíricamente durante el diseño de este expediente).
    """
    with AgentSandbox(networked=True) as sandbox:
        assert sandbox._started
        install = sandbox.exec("pip install --no-cache-dir pandas", timeout=180)
        assert install.success, install.output

        run = sandbox.exec("python -c \"import pandas; print(pandas.__version__)\"", timeout=30)
        assert run.success, run.output


def test_write_outside_allowed_paths_is_blocked():
    """El objetivo central del expediente: --read-only debe bloquear escritura fuera de /workspace, /tmp y ~/.local."""
    with AgentSandbox(networked=False) as sandbox:
        assert sandbox._started
        result = sandbox.exec("touch /usr/local/lib/should_fail.txt", timeout=10)
        assert not result.success
