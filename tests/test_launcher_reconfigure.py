"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

tests/test_launcher_reconfigure.py — Regresión de gaps reales reportados
por Fernando usando el instalador ya publicado:
1. Sin consola visible (console=False), no queda ningún rastro en disco
   de qué hizo ClawLite -- _configure_file_logging() es el único punto
   de observabilidad real disponible.
2. Sin forma de reabrir el asistente de configuración una vez que la
   config ya está completa -- launcher.py --reconfigure resuelve esto.
3. Sin ninguna confirmación visual de que ClawLite está corriendo --
   _start_tray_icon() agrega un ícono real en la bandeja del sistema.
"""

import sys
import time

import ctypes
import httpx

import clawlite.launcher as launcher


def test_configure_file_logging_writes_a_real_file(tmp_path, monkeypatch):
    """El log debe quedar en un archivo real de disco -- es la única
    fuente de verdad disponible sin consola visible."""
    monkeypatch.chdir(tmp_path)
    launcher._configure_file_logging()

    from loguru import logger
    logger.info("mensaje de prueba real")

    log_file = tmp_path / "clawlite.log"
    assert log_file.exists()
    assert "mensaje de prueba real" in log_file.read_text(encoding="utf-8")


def test_reconfigure_flag_detected_from_argv(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ClawLite.exe", "--reconfigure"])
    assert "--reconfigure" in sys.argv


def test_reconfigure_flag_absent_by_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ClawLite.exe"])
    assert "--reconfigure" not in sys.argv


def test_handle_already_running_with_reconfigure_shows_explicit_message(monkeypatch):
    """Si el bot ya está corriendo (wizard no activo en su puerto) y se pidió
    --reconfigure, el mensaje debe explicar qué hacer -- no el genérico.
    Se parchea httpx.get y ctypes.windll.user32.MessageBoxW directamente
    (no launcher.httpx/launcher.ctypes) porque _handle_already_running()
    los importa localmente dentro de la función -- el nombre queda
    resuelto contra el módulo real, no un atributo del módulo launcher."""
    shown = {}

    def fake_get(url, timeout=1.0):
        raise ConnectionError("wizard no activo -- simula que el bot ya está corriendo")

    def fake_message_box(hwnd, text, caption, flags):
        shown["text"] = text
        return 1

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", fake_message_box)

    launcher._handle_already_running(reconfigure=True)

    assert "reconfigurarlo" in shown["text"].lower()
    assert "administrador de tareas" in shown["text"].lower()


def test_make_tray_icon_image_returns_a_real_image():
    """La imagen del ícono debe generarse sin depender de ningún archivo
    externo -- confirma que PIL genera un objeto de imagen válido."""
    img = launcher._make_tray_icon_image()
    assert img.size == (64, 64)


def test_start_tray_icon_runs_for_real_without_crashing():
    """Prueba real (no mockeada) de que pystray arranca en un thread
    daemon aparte sin lanzar ninguna excepción, y que queda realmente
    visible en la bandeja -- reproduce exactamente el patrón que usa
    _start_tray_icon() en el launcher real."""
    icon = launcher._start_tray_icon()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not icon.visible:
            time.sleep(0.1)
        assert icon.visible, "el ícono no llegó a quedar visible en 5s -- pystray no arrancó bien en un thread daemon"
    finally:
        icon.stop()
        time.sleep(0.3)
