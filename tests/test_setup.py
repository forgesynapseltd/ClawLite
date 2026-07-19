"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

tests/test_setup.py — Regresión de _bundle_root(): ENV_EXAMPLE_PATH es una
plantilla de solo lectura que viaja con el programa, y NO debe depender del
cwd -- un .exe empaquetado hace chdir a la carpeta de datos del usuario
(ver launcher._set_data_home) antes de resolver esta ruta, y esa carpeta
nunca tiene un .env.example real.
"""

import sys

from clawlite import setup


def test_bundle_root_dev_mode_ignores_cwd(monkeypatch, tmp_path):
    """En modo dev (sys.frozen no seteado), _bundle_root() debe resolver a
    la raíz del repo por posición del archivo fuente, sin importar el cwd
    del proceso -- no debe pisarlo un chdir hecho por otro módulo."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.chdir(tmp_path)  # cwd deliberadamente vacío y ajeno al repo

    root = setup._bundle_root()

    assert (root / "clawlite" / "setup.py").exists(), (
        "_bundle_root() en modo dev debe apuntar a la raíz real del repo, "
        f"no a {root}"
    )


def test_bundle_root_frozen_mode_uses_meipass(monkeypatch, tmp_path):
    """Empaquetado (sys.frozen=True), _bundle_root() debe usar sys._MEIPASS
    -- la carpeta real del bundle de PyInstaller -- y no el cwd."""
    fake_bundle = tmp_path / "bundle"
    fake_bundle.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_bundle), raising=False)
    monkeypatch.chdir(tmp_path)  # cwd distinto del bundle -- no debe usarse

    root = setup._bundle_root()

    assert root == fake_bundle


def test_env_example_path_survives_chdir_away_from_repo(monkeypatch, tmp_path):
    """Reproduce el bug real: si algo (como launcher._set_data_home) hace
    chdir a una carpeta de datos de usuario ANTES de leer ENV_EXAMPLE_PATH,
    la ruta calculada en import-time ya no debe depender de ese cwd."""
    data_home = tmp_path / "ClawLite"
    data_home.mkdir()
    monkeypatch.chdir(data_home)

    assert setup.ENV_EXAMPLE_PATH.exists(), (
        "ENV_EXAMPLE_PATH no debe volverse inválido por un chdir posterior "
        "a la carpeta de datos del usuario -- ese fue el bug real: HTTP 500 "
        "en /form del wizard empaquetado."
    )
