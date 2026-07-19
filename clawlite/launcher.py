"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

launcher.py — Punto de entrada único del instalador empaquetado (PyInstaller).
Decide solo qué hacer: si falta configuración, abre el wizard web; si no,
arranca el bot directo. El usuario nunca ve una terminal ni escribe un
comando.

Instancia única: un mutex nombrado de Windows (CreateMutex) impide que dos
ejecuciones corran a la vez -- Windows lo libera automáticamente cuando el
proceso termina, incluso por crash, sin el riesgo de "lock huérfano" de un
lockfile manual. El nombre del mutex es un CONTRATO ESTABLE DE PRODUCTO:
debe permanecer igual entre versiones para que todas las instancias de
ClawLite se coordinen correctamente. Si en el futuro coexistieran
ediciones distintas (estable/desarrollo), esa diferenciación debe ser
deliberada (nombres de mutex distintos a propósito), no un accidente.
"""

import os
import sys
from pathlib import Path

MUTEX_NAME = "Global\\FORGESYNAPSE.ClawLite.Launcher"
WIZARD_PORT = 8710


def _acquire_single_instance_lock():
    """Devuelve (mutex_handle, already_running: bool). El handle debe
    mantenerse vivo (referenciado) durante toda la vida del proceso -- si
    se lo deja recolectar por el GC, Windows libera el mutex antes de
    tiempo."""
    import win32event
    import win32api
    import winerror

    mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
    already_running = win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS
    return mutex, already_running


def _handle_already_running():
    """
    Ya hay una instancia corriendo. Se distingue de forma determinista,
    no adivinando, si el wizard está activo (responde en su puerto fijo)
    o si ya terminó (el bot real ya está corriendo en segundo plano):
    - Wizard activo -> reabrir esa pestaña del navegador, nunca levantar
      un segundo servidor compitiendo por el mismo puerto.
    - Wizard ya cerrado -> avisar con un mensaje nativo de Windows y salir,
      sin tocar .env/DB/Telegram (eso lo maneja solo la instancia real).
    """
    import ctypes
    import httpx

    try:
        httpx.get(f"http://127.0.0.1:{WIZARD_PORT}/", timeout=1.0)
        wizard_active = True
    except Exception:
        wizard_active = False

    if wizard_active:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{WIZARD_PORT}/")
    else:
        ctypes.windll.user32.MessageBoxW(
            0, "ClawLite ya está corriendo en segundo plano.", "ClawLite", 0x40,
        )


def _set_data_home():
    """
    Working directory: %LOCALAPPDATA%\\ClawLite -- por usuario, nunca
    necesita permisos de administrador, siempre escribible. Separado de
    dónde se instala el programa (Program Files o similar): convención
    estándar de Windows, programa de un lado, datos del usuario del otro.
    Las rutas relativas del proyecto (./.env, ./data/clawlite.db) quedan
    ancladas acá sin tocar su código.
    """
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    data_home = Path(base) / "ClawLite"
    data_home.mkdir(parents=True, exist_ok=True)
    os.chdir(data_home)


def main():
    mutex, already_running = _acquire_single_instance_lock()
    if already_running:
        _handle_already_running()
        return

    _set_data_home()

    from clawlite.config import BOOTSTRAP_REQUIRED_KEYS
    from clawlite.setup import ENV_PATH, _read_env_values

    current = _read_env_values(ENV_PATH)
    missing = [k for k in BOOTSTRAP_REQUIRED_KEYS if not current.get(k)]

    if missing:
        import threading
        import webbrowser

        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{WIZARD_PORT}/")).start()

        from clawlite.setup_web import run as run_wizard
        run_wizard(port=WIZARD_PORT)  # bloqueante -- retorna cuando /done pide el apagado

    from clawlite.main import main as run_bot
    run_bot()

    # 'mutex' se mantiene referenciado hasta acá -- Windows lo libera al
    # terminar el proceso, sin importar cómo termine.
    del mutex


if __name__ == "__main__":
    sys.exit(main() or 0)
