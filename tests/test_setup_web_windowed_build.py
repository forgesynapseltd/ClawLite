"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

tests/test_setup_web_windowed_build.py — Regresión de un bug real
encontrado por un usuario en el instalador empaquetado: en un build de
PyInstaller windowed (console=False) lanzado SIN redirección de
stdout/stderr (el escenario real de un usuario haciendo doble clic --
las validaciones previas de esta sesión siempre redirigían stdout/stderr
a un archivo para poder leer el log, lo que enmascaró este bug),
sys.stdout y sys.stderr son None. uvicorn.Config() configura por
defecto el logging estándar de Python con un formatter que llama a
sys.stdout.isatty(), lo que tira AttributeError -> ValueError: Unable
to configure formatter 'default', matando el proceso antes de loguear
nada. Fix: log_config=None en la construcción de uvicorn.Config().
"""

import sys

import uvicorn

from clawlite.setup_web import app


def test_uvicorn_config_survives_none_stdout_stderr(monkeypatch):
    """Reproduce exactamente el escenario real: sys.stdout/stderr en None
    (build windowed sin consola, sin redirección) no debe romper la
    construcción de uvicorn.Config() -- antes de este fix, tiraba
    ValueError: Unable to configure formatter 'default'."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    # No debe lanzar excepción -- log_config=None evita que uvicorn intente
    # configurar el logging estándar de Python (que revienta con stdout=None).
    uvicorn.Config(app, host="127.0.0.1", port=8710, log_level="warning", log_config=None)


def test_uvicorn_config_without_log_config_none_reproduces_the_original_bug(monkeypatch):
    """Control negativo: confirma que el bug es real y que log_config=None
    es lo que lo evita -- sin este parámetro, la misma construcción SÍ
    debe fallar con el error real reportado por el usuario."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    try:
        uvicorn.Config(app, host="127.0.0.1", port=8710, log_level="warning")
    except ValueError as e:
        assert "Unable to configure formatter" in str(e)
    else:
        raise AssertionError(
            "Se esperaba ValueError sin log_config=None -- si esto no falla más, "
            "significa que uvicorn cambió su comportamiento y este test de control "
            "quedó obsoleto (revisar antes de asumir que el bug real desapareció solo)."
        )
