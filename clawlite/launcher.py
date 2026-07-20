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


def _handle_already_running(reconfigure: bool = False):
    """
    Ya hay una instancia corriendo. Se distingue de forma determinista,
    no adivinando, si el wizard está activo (responde en su puerto fijo)
    o si ya terminó (el bot real ya está corriendo en segundo plano):
    - Wizard activo -> reabrir esa pestaña del navegador, nunca levantar
      un segundo servidor compitiendo por el mismo puerto.
    - Wizard ya cerrado -> avisar con un mensaje nativo de Windows y salir,
      sin tocar .env/DB/Telegram (eso lo maneja solo la instancia real).

    reconfigure=True (se pidió "Reconfigurar ClawLite" con el bot real ya
    corriendo): no hay forma de abrir el wizard sin competir por el mismo
    mutex/puerto que ya usa la instancia real -- se le dice explícito al
    usuario qué hacer, en vez de mostrar el mensaje genérico.
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
    elif reconfigure:
        ctypes.windll.user32.MessageBoxW(
            0,
            "ClawLite ya está corriendo. Para reconfigurarlo, primero cerrá el "
            "proceso ClawLite desde el Administrador de tareas y volvé a intentar.",
            "ClawLite",
            0x30,
        )
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


def _configure_file_logging():
    """
    En un build console=False lanzado sin redirección de salida (el
    escenario real de un usuario haciendo doble clic), sys.stdout y
    sys.stderr son None. loguru no se cae por eso (degrada en silencio),
    pero tampoco queda ningún rastro en ningún lado de qué pasó si algo
    falla -- cero observabilidad real para el usuario o para soporte.
    Este sink de archivo es la única fuente de verdad post-mortem
    disponible sin consola. Rotación para no crecer sin límite; se
    agrega ADEMÁS del handler default de loguru (a stderr), no lo
    reemplaza -- no toca nada de lo que ya depende de él (ver
    run_mcp_only() en main.py, que sí lo reconfigura para su propio
    modo). Debe llamarse DESPUÉS de _set_data_home(): el archivo se
    escribe relativo al cwd (la carpeta de datos), mismo criterio que
    ./.env y ./data/clawlite.db.
    """
    from loguru import logger
    logger.add("clawlite.log", rotation="5 MB", retention=3, level="INFO", encoding="utf-8")


def _make_tray_icon_image():
    """Imagen generada en código (círculo simple) -- evita depender de un
    archivo de ícono externo que haya que empaquetar aparte."""
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, size - 4, size - 4], fill=(88, 101, 242, 255))
    return img


def _start_tray_icon():
    """
    Ícono en la bandeja del sistema -- confirmación visual real de que
    ClawLite está corriendo, sin necesitar abrir ningún log ni consola
    (mismo patrón que Discord/Dropbox/etc.). Corre en un thread daemon
    aparte: pystray funciona bien en un thread secundario en Windows
    (a diferencia de macOS, que exige el thread principal -- este
    instalador apunta solo a Windows).

    Alcance deliberadamente acotado: el único ítem accionable es "Salir"
    (sale de TODO el proceso -- comparte el mismo criterio ya aceptado en
    worker_pool.py de que un cierre abrupto es un riesgo ya asumido, no
    uno nuevo). NO incluye "Reconfigurar" desde la bandeja: hacerlo bien
    requeriría coordinar entre el thread del ícono y el thread principal
    -- que puede estar bloqueado dentro del bot real o del wizard -- para
    parar lo que esté corriendo y reabrir el wizard, una complejidad real
    que no se justifica todavía cuando el acceso directo "Reconfigurar
    ClawLite" del Start Menu ya cubre ese caso (con el proceso actual
    cerrado primero).
    """
    import threading
    import pystray

    def on_exit(icon, item):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(pystray.MenuItem("Salir", on_exit))
    icon = pystray.Icon("ClawLite", _make_tray_icon_image(), "ClawLite", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


def main():
    reconfigure = "--reconfigure" in sys.argv

    mutex, already_running = _acquire_single_instance_lock()
    if already_running:
        _handle_already_running(reconfigure=reconfigure)
        return

    _set_data_home()
    _configure_file_logging()
    tray_icon = _start_tray_icon()

    from clawlite.config import BOOTSTRAP_REQUIRED_KEYS
    from clawlite.setup import ENV_PATH, _read_env_values

    current = _read_env_values(ENV_PATH)
    missing = [k for k in BOOTSTRAP_REQUIRED_KEYS if not current.get(k)]

    if missing or reconfigure:
        import threading
        import webbrowser

        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{WIZARD_PORT}/")).start()

        from clawlite.setup_web import run as run_wizard
        run_wizard(port=WIZARD_PORT, force_reconfigure=reconfigure)  # bloqueante -- retorna cuando /done pide el apagado

    from clawlite.main import main as run_bot
    run_bot()

    # 'mutex' se mantiene referenciado hasta acá -- Windows lo libera al
    # terminar el proceso, sin importar cómo termine.
    del mutex


if __name__ == "__main__":
    sys.exit(main() or 0)
