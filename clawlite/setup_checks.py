"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

setup_checks.py — Detección y smoke-test funcional de Docker y Ollama,
para el wizard del instalador. No compara números de versión (no hay un
mínimo oficial documentado, sobre todo para Ollama) -- prueba en vivo,
con las flags/llamadas EXACTAS que ClawLite usa en producción. Si el
smoke test pasa, la instalación es apta sin importar su versión.
"""

import os
import subprocess
import tempfile
import uuid
from enum import Enum

import httpx
from loguru import logger


# ── Timeouts: cada uno responde a un tipo de bloqueo distinto -- no       ──
# ── "optimizar" estos números sin entender por qué son distintos.        ──
DOCKER_VERSION_CHECK_TIMEOUT = 5     # "docker --version": debe responder casi instantáneo; si no, el binario está roto o el PATH es incorrecto.
DOCKER_INFO_TIMEOUT = 5              # "docker info": consulta al daemon local; 5s alcanza de sobra si el servicio está corriendo, y falla rápido si no.
DOCKER_PULL_TIMEOUT = 120            # "docker pull alpine": puede ser la primera vez que se descarga la imagen -- techo generoso para una descarga real de red.
DOCKER_SMOKETEST_TIMEOUT = 30        # "docker run" del smoke test: la imagen YA está local (paso anterior) -- esto solo mide arranque + ejecución del contenedor.

OLLAMA_CONNECT_TIMEOUT = 10.0        # Conexión al daemon local de Ollama -- si no conecta en 10s, no está corriendo.
OLLAMA_READ_TIMEOUT = 60.0           # Timeout de INACTIVIDAD, no de tiempo total -- se reinicia con cada chunk recibido. Necesario para no cortar una descarga de modelo grande que progresa lento pero real, mientras SÍ corta algo genuinamente colgado (sin datos por 60s).
OLLAMA_WRITE_TIMEOUT = 60.0          # Mismo criterio que read, para el lado de escritura de la request.
OLLAMA_POOL_TIMEOUT = 10.0           # Tiempo de espera para obtener una conexión del pool de httpx.

# Instalador oficial de Ollama para Windows -- verificado: usa Inno Setup
# (igual que ClawLite, soporta /VERYSILENT de forma estándar), licencia MIT
# (permite automatizar/redistribuir sin problema legal), ~1.36GB -- pesa
# demasiado para empaquetarlo DENTRO del instalador de ClawLite, se
# descarga en el momento desde la URL oficial de GitHub Releases.
OLLAMA_INSTALLER_URL = "https://github.com/ollama/ollama/releases/latest/download/OllamaSetup.exe"
OLLAMA_INSTALLER_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)
OLLAMA_INSTALLER_RUN_TIMEOUT = 300   # el instalador silencioso de Ollama no debería tardar más que esto


class CheckStatus(Enum):
    NOT_INSTALLED = "not_installed"
    NOT_RUNNING = "not_running"
    FUNCTIONAL_CHECK_FAILED = "functional_check_failed"
    OK = "ok"


class CheckResult:
    def __init__(self, status: CheckStatus, detail: str = ""):
        self.status = status
        self.detail = detail


def check_docker() -> CheckResult:
    """
    Detecta Docker en pasos: binario presente -> daemon responde -> imagen
    de prueba disponible -> smoke test funcional con las MISMAS flags que
    usa AgentSandbox en producción (--read-only, --tmpfs con uid/gid,
    --cap-drop ALL, --security-opt no-new-privileges). El contenedor de
    prueba se borra solo (--rm) -- no deja rastro en el sistema del usuario.
    """
    try:
        subprocess.run(
            ["docker", "--version"], capture_output=True,
            timeout=DOCKER_VERSION_CHECK_TIMEOUT, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return CheckResult(CheckStatus.NOT_INSTALLED, "Docker no está instalado o no está en el PATH.")

    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=DOCKER_INFO_TIMEOUT)
        if result.returncode != 0:
            return CheckResult(CheckStatus.NOT_RUNNING, "Docker está instalado pero el servicio no está corriendo.")
    except subprocess.TimeoutExpired:
        return CheckResult(CheckStatus.NOT_RUNNING, "Docker no respondió a tiempo -- el servicio puede no estar corriendo.")

    # Pull separado de la ejecución: la primera vez puede tardar, con su propio timeout.
    try:
        pull_result = subprocess.run(
            ["docker", "pull", "alpine:latest"],
            capture_output=True, timeout=DOCKER_PULL_TIMEOUT, text=True,
        )
        if pull_result.returncode != 0:
            return CheckResult(
                CheckStatus.FUNCTIONAL_CHECK_FAILED,
                f"No se pudo descargar la imagen de prueba: {pull_result.stderr[:300]}",
            )
    except subprocess.TimeoutExpired:
        return CheckResult(
            CheckStatus.FUNCTIONAL_CHECK_FAILED,
            f"La descarga de la imagen de prueba no respondió a tiempo ({DOCKER_PULL_TIMEOUT}s).",
        )

    # El objetivo de este smoke test NO es "¿Docker puede correr un
    # contenedor?" -- es reproducir EXACTAMENTE las condiciones reales de
    # AgentSandbox (mismo uid, mismo --cap-drop, mismo --read-only/tmpfs) para
    # detectar incompatibilidades de permisos del entorno del usuario ANTES
    # de que aparezcan en producción. Si se "simplifica" este test más
    # adelante quitando --user 1000:1000, vuelve el falso negativo real que
    # se encontró y corrigió acá: alpine corre como root por defecto, pero
    # --cap-drop ALL le quita CAP_DAC_OVERRIDE (lo que normalmente deja a
    # root ignorar permisos), y root ya no puede escribir en un tmpfs
    # declarado uid=1000 -- exactamente el escenario real de AgentSandbox
    # (imagen con USER sandboxuser, uid 1000, no root).
    container_name = f"clawlite-smoketest-{uuid.uuid4().hex[:8]}"
    try:
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--name", container_name,
                "--read-only",
                "--tmpfs", "/tmp:rw,exec,size=16m,mode=1777",
                "--tmpfs", "/test:rw,exec,size=16m,mode=0755,uid=1000,gid=1000",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--user", "1000:1000",
                "alpine:latest",
                "sh", "-c", "echo ok > /test/probe && cat /test/probe",
            ],
            capture_output=True, timeout=DOCKER_SMOKETEST_TIMEOUT, text=True,
        )
        if result.returncode != 0 or "ok" not in result.stdout:
            return CheckResult(
                CheckStatus.FUNCTIONAL_CHECK_FAILED,
                f"El contenedor de prueba no funcionó como se esperaba: {result.stderr[:300]}",
            )
    except subprocess.TimeoutExpired:
        return CheckResult(
            CheckStatus.FUNCTIONAL_CHECK_FAILED,
            f"El contenedor de prueba no respondió a tiempo ({DOCKER_SMOKETEST_TIMEOUT}s).",
        )
    except Exception as e:
        return CheckResult(CheckStatus.FUNCTIONAL_CHECK_FAILED, f"Error inesperado probando Docker: {e}")

    return CheckResult(CheckStatus.OK)


def check_ollama(default_model: str) -> CheckResult:
    """
    Detecta Ollama en pasos: daemon responde -> modelo configurado ya
    presente (sin descargar de más) -> si NO está presente, descarga UNA
    vez como parte de la instalación inicial (se informa al usuario que
    esto es la instalación real, no una verificación) y confirma con una
    petición de completion trivial que el modelo responde de verdad.

    Usa un ollama.Client propio con timeout explícito -- las funciones de
    conveniencia del módulo (ollama.list()/pull()/chat()) usan un cliente
    global SIN timeout, que puede colgarse indefinidamente.
    """
    try:
        import ollama as ollama_lib
    except ImportError:
        return CheckResult(CheckStatus.NOT_INSTALLED, "El paquete cliente de Ollama no está disponible.")

    client = ollama_lib.Client(
        timeout=httpx.Timeout(
            connect=OLLAMA_CONNECT_TIMEOUT,
            read=OLLAMA_READ_TIMEOUT,
            write=OLLAMA_WRITE_TIMEOUT,
            pool=OLLAMA_POOL_TIMEOUT,
        )
    )

    try:
        local_models = {m.model for m in client.list().models}
    except Exception as e:
        return CheckResult(CheckStatus.NOT_RUNNING, f"Ollama no respondió -- el servicio puede no estar corriendo: {e}")

    if default_model not in local_models:
        logger.info(
            f"⬇️ Instalación inicial: descargando el modelo '{default_model}' "
            f"(esto no es una verificación, es parte de la instalación)."
        )
        try:
            client.pull(default_model)
        except Exception as e:
            return CheckResult(CheckStatus.FUNCTIONAL_CHECK_FAILED, f"No se pudo descargar el modelo '{default_model}': {e}")
    else:
        logger.info(f"✅ El modelo '{default_model}' ya está disponible -- no se vuelve a descargar.")

    try:
        response = client.chat(
            model=default_model,
            messages=[{"role": "user", "content": "responde solo con la palabra: ok"}],
        )
        if not response or not response.get("message", {}).get("content"):
            return CheckResult(CheckStatus.FUNCTIONAL_CHECK_FAILED, "El modelo no devolvió una respuesta válida.")
    except Exception as e:
        return CheckResult(CheckStatus.FUNCTIONAL_CHECK_FAILED, f"Error real ejecutando el modelo: {e}")

    return CheckResult(CheckStatus.OK)


def download_and_install_ollama(progress_cb=None) -> CheckResult:
    """
    Descarga el instalador oficial de Ollama y lo corre en modo silencioso
    -- automatiza la única pieza que check_ollama() no resuelve sola (el
    modelo en sí ya se descarga automático dentro de check_ollama/
    ensure_ollama_models_available, esto es solo para cuando falta la
    aplicación Ollama misma).

    progress_cb(downloaded_bytes, total_bytes) se llama periódicamente
    durante la descarga -- sin esto, la UI quedaría sin ninguna señal de
    progreso durante varios minutos en una conexión normal (el instalador
    pesa ~1.36GB).
    """
    installer_path = os.path.join(tempfile.gettempdir(), "ClawLite-OllamaSetup.exe")

    try:
        with httpx.stream(
            "GET", OLLAMA_INSTALLER_URL, follow_redirects=True,
            timeout=OLLAMA_INSTALLER_DOWNLOAD_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(installer_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total)
    except Exception as e:
        return CheckResult(CheckStatus.NOT_INSTALLED, f"No se pudo descargar el instalador de Ollama: {e}")

    try:
        subprocess.run([installer_path, "/VERYSILENT"], timeout=OLLAMA_INSTALLER_RUN_TIMEOUT, check=True)
    except Exception as e:
        return CheckResult(CheckStatus.NOT_INSTALLED, f"El instalador de Ollama no pudo completarse: {e}")
    finally:
        try:
            os.remove(installer_path)
        except OSError:
            pass

    from clawlite.config import config
    return check_ollama(config.OLLAMA_MODEL)
