"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

sandbox/agent_sandbox.py — Sandbox persistente para agentes que desarrollan proyectos
Mantiene un contenedor Docker vivo durante toda la sesión del agente,
permitiendo múltiples comandos que preservan el estado del workspace.
"""

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from loguru import logger
from clawlite.sandbox.docker_manager import IMAGE_NAME, IMAGE_TAG, docker_sandbox


class AgentExecResult:
    def __init__(
        self,
        success: bool,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = -1,
        duration: float = 0.0,
        timed_out: bool = False,
    ):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.duration = duration
        self.timed_out = timed_out

    @property
    def output(self) -> str:
        if self.stderr and not self.stdout:
            return self.stderr
        if self.stderr:
            return f"{self.stdout}\n[stderr]\n{self.stderr}"
        return self.stdout


class AgentSandbox:
    """
    Contenedor Docker persistente para una sesión de agente.
    El workspace se mantiene durante toda la sesión, permitiendo
    crear archivos, ejecutar comandos, e iterar con preservación de estado.
    """

    DEFAULT_TIMEOUT = 90
    MAX_LIFETIME_SECONDS = 600  # 10 min máximo por sesión

    def __init__(self, session_id: str | None = None, networked: bool = True):
        self.session_id = session_id or f"agent-{uuid.uuid4().hex[:12]}"
        self.networked = networked
        self.container_name = f"clawlite-agent-{self.session_id}"
        self.host_workspace: str | None = None
        self._started = False
        self._start_time: float = 0.0

    def start(self) -> bool:
        """Crea el contenedor persistente con el workspace montado."""
        if self._started:
            return True

        if not docker_sandbox.is_docker_available():
            logger.error("❌ Docker no disponible")
            return False

        if not docker_sandbox.ensure_image():
            logger.error("❌ Imagen sandbox no disponible")
            return False

        # Workspace en el host (será montado en el contenedor)
        self.host_workspace = tempfile.mkdtemp(prefix=f"clawlite_ws_{self.session_id}_")

        try:
            # Argumentos docker run para sandbox de agente
            args = [
                "docker", "run",
                "-d",                                       # Detached
                "--name", self.container_name,
                "--memory", "1g",
                "--memory-swap", "1g",
                "--cpus", "1.0",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "ALL",
                "--pids-limit", "200",
                "--read-only",
                # tmpfs SOLO en los subdirectorios concretos que necesitan
                # escritura -- /home/sandboxuser en sí mismo queda intacto
                # (parte del filesystem read-only, tal cual lo creó la imagen
                # con useradd -m). uid/gid fijados en el propio mount para que
                # el punto de montaje nazca con el dueño correcto -- pip crea
                # la estructura interna sola (validado con Docker real).
                # 'exec' explícito: Docker monta tmpfs con noexec por defecto,
                # lo que rompe la carga de extensiones compiladas (.so) de
                # paquetes como pandas/numpy -- confirmado con Docker real
                # (ImportError: failed to map segment) antes de agregar 'exec'.
                "--tmpfs", "/tmp:rw,exec,size=128m,mode=1777",
                "--tmpfs", "/home/sandboxuser/.local:rw,exec,size=512m,mode=0755,uid=1000,gid=1000",
                "-v", f"{self.host_workspace}:/workspace:rw",
                "-w", "/workspace",
            ]

            # Red — los agentes de desarrollo la necesitan para pip install
            if not self.networked:
                args.extend(["--network", "none"])
            else:
                # DNS explícito para evitar fallos de resolución en Windows/WSL
                args.extend([
                    "--dns", "8.8.8.8",
                    "--dns", "1.1.1.1",
                ])

            # Override entrypoint para mantener el contenedor vivo
            args.extend([
                "--entrypoint", "/bin/sh",
                f"{IMAGE_NAME}:{IMAGE_TAG}",
                "-c", "while sleep 3600; do :; done",
            ])

            result = subprocess.run(
                args,
                capture_output=True,
                timeout=20,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                logger.error(f"❌ Failed to start sandbox: {result.stderr}")
                self._cleanup_workspace()
                return False

            self._started = True
            self._start_time = time.time()
            logger.info(f"🐳 AgentSandbox started: {self.container_name} (networked={self.networked})")
            return True

        except Exception as e:
            logger.error(f"❌ AgentSandbox.start failed: {e}")
            self._cleanup_workspace()
            return False

    def exec(self, command: str, timeout: int | None = None) -> AgentExecResult:
        """Ejecuta un comando shell dentro del contenedor."""
        if not self._started:
            return AgentExecResult(success=False, stderr="Sandbox no iniciado")

        if time.time() - self._start_time > self.MAX_LIFETIME_SECONDS:
            return AgentExecResult(
                success=False,
                stderr=f"Sesión excedió el límite de {self.MAX_LIFETIME_SECONDS}s",
            )

        timeout = timeout or self.DEFAULT_TIMEOUT
        start = time.time()

        try:
            result = subprocess.run(
                ["docker", "exec", self.container_name, "sh", "-c", command],
                capture_output=True,
                timeout=timeout,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            duration = time.time() - start

            logger.debug(f"  exec [{duration:.1f}s][exit {result.returncode}]: {command[:80]}")

            return AgentExecResult(
                success=result.returncode == 0,
                stdout=result.stdout[:50000],
                stderr=result.stderr[:10000],
                exit_code=result.returncode,
                duration=duration,
            )

        except subprocess.TimeoutExpired:
            duration = time.time() - start
            return AgentExecResult(
                success=False,
                duration=duration,
                timed_out=True,
                stderr=f"Comando timeout después de {timeout}s",
            )
        except Exception as e:
            return AgentExecResult(success=False, stderr=str(e))

    def write_file(self, relative_path: str, content: str) -> bool:
        """Crea o sobrescribe un archivo en el workspace."""
        if not self._started or not self.host_workspace:
            return False

        # Seguridad: no permitir rutas absolutas ni '..'
        if relative_path.startswith("/") or ".." in relative_path:
            logger.warning(f"⚠️  Ruta no permitida: {relative_path}")
            return False

        try:
            full_path = Path(self.host_workspace) / relative_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            return True
        except Exception as e:
            logger.error(f"❌ write_file failed: {e}")
            return False

    def read_file(self, relative_path: str) -> str | None:
        """Lee un archivo del workspace."""
        if not self._started or not self.host_workspace:
            return None

        if relative_path.startswith("/") or ".." in relative_path:
            return None

        try:
            full_path = Path(self.host_workspace) / relative_path
            return full_path.read_text(encoding="utf-8")
        except Exception:
            return None

    def list_files(self) -> list[str]:
        """Lista todos los archivos del workspace."""
        if not self._started or not self.host_workspace:
            return []

        try:
            files = []
            base = Path(self.host_workspace)
            for path in base.rglob("*"):
                if path.is_file() and not any(p.startswith(".") for p in path.parts):
                    files.append(str(path.relative_to(base)).replace("\\", "/"))
            return sorted(files)
        except Exception:
            return []

    def get_workspace_archive(self) -> str | None:
        """Crea un .tar.gz del workspace y devuelve la ruta."""
        if not self._started or not self.host_workspace:
            return None

        try:
            archive_path = f"{self.host_workspace}.tar.gz"
            subprocess.run(
                ["tar", "-czf", archive_path, "-C", self.host_workspace, "."],
                check=True,
                timeout=30,
            )
            return archive_path
        except Exception as e:
            logger.debug(f"Could not create archive: {e}")
            return None

    def stop(self):
        """Destruye el contenedor y limpia el workspace."""
        if not self._started:
            return

        try:
            subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                capture_output=True,
                timeout=10,
            )
            logger.info(f"🐳 AgentSandbox stopped: {self.container_name}")
        except Exception as e:
            logger.debug(f"Error stopping container: {e}")

        self._cleanup_workspace()
        self._started = False

    def _cleanup_workspace(self):
        if self.host_workspace and os.path.exists(self.host_workspace):
            try:
                shutil.rmtree(self.host_workspace, ignore_errors=True)
            except Exception:
                pass
            self.host_workspace = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
