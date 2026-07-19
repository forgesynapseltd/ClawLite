"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

sandbox/docker_manager.py — Ejecutor de código en contenedores Docker aislados
Cada ejecución crea un contenedor efímero que se destruye al terminar.
"""

import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from loguru import logger
from clawlite.sandbox.sandbox_levels import SandboxLevel, get_level


IMAGE_NAME = "clawlite-sandbox-python"
DOCKERFILE_PATH = Path(__file__).parent / "images" / "Dockerfile.python"


def _compute_image_tag() -> str:
    """
    Calcula el tag de la imagen como hash del Dockerfile.
    Si el Dockerfile cambia, el tag cambia y Docker rebuildea automáticamente.
    Esto garantiza que cambios en la imagen base se propaguen sin pasos manuales.
    """
    import hashlib
    try:
        content = DOCKERFILE_PATH.read_bytes()
        digest = hashlib.sha256(content).hexdigest()[:12]
        return f"v{digest}"
    except Exception:
        # Si no podemos leer el Dockerfile, caer a 'latest' (comportamiento legacy)
        return "latest"


IMAGE_TAG = _compute_image_tag()


class SandboxResult:
    """Resultado de una ejecución en sandbox."""

    def __init__(
        self,
        success: bool,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = -1,
        duration: float = 0.0,
        timed_out: bool = False,
        error: str = "",
    ):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.duration = duration
        self.timed_out = timed_out
        self.error = error

    def __repr__(self):
        return (
            f"SandboxResult(success={self.success}, "
            f"exit_code={self.exit_code}, "
            f"duration={self.duration:.2f}s, "
            f"timed_out={self.timed_out})"
        )


class DockerSandbox:
    """
    Ejecuta código Python en contenedores Docker aislados.
    Construye la imagen base la primera vez y la reutiliza.
    """

    def __init__(self):
        self._image_built = False

    def is_docker_available(self) -> bool:
        """Verifica que Docker esté instalado y corriendo."""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def ensure_image(self) -> bool:
        """Construye la imagen sandbox si no existe."""
        if self._image_built:
            return True

        try:
            # Verificar si la imagen ya existe localmente
            check = subprocess.run(
                ["docker", "image", "inspect", f"{IMAGE_NAME}:{IMAGE_TAG}"],
                capture_output=True,
                timeout=10,
            )
            if check.returncode == 0:
                self._image_built = True
                logger.debug(f"🐳 Sandbox image already built: {IMAGE_NAME}:{IMAGE_TAG}")
                return True

            # Construir
            logger.info(f"🐳 Building sandbox image {IMAGE_NAME}:{IMAGE_TAG} (first time, this may take a minute)...")
            build_dir = DOCKERFILE_PATH.parent
            result = subprocess.run(
                [
                    "docker", "build",
                    "-t", f"{IMAGE_NAME}:{IMAGE_TAG}",
                    "-f", str(DOCKERFILE_PATH),
                    str(build_dir),
                ],
                capture_output=True,
                timeout=300,
            )

            if result.returncode == 0:
                self._image_built = True
                logger.info(f"✅ Sandbox image built")
                return True
            else:
                logger.error(f"❌ Error building image: {result.stderr.decode()[:500]}")
                return False

        except Exception as e:
            logger.error(f"❌ Error ensuring image: {e}")
            return False

    def execute_python(
        self,
        code: str,
        level: str = "isolated",
    ) -> SandboxResult:
        """
        Ejecuta código Python en un contenedor aislado.
        - code: código Python a ejecutar
        - level: 'isolated' | 'networked' | 'filesystem'
        """
        if not self.is_docker_available():
            return SandboxResult(
                success=False,
                error="Docker no disponible. Asegúrate de que Docker Desktop esté corriendo.",
            )

        if not self.ensure_image():
            return SandboxResult(
                success=False,
                error="No se pudo construir la imagen sandbox.",
            )

        try:
            sandbox_level = get_level(level)
        except ValueError as e:
            return SandboxResult(success=False, error=str(e))

        return self._run_in_container(code, sandbox_level)

    def _run_in_container(
        self,
        code: str,
        level: SandboxLevel,
    ) -> SandboxResult:
        """Crea el contenedor, ejecuta el código, captura output y destruye."""
        import time

        # Guardar el código en un archivo temporal
        tmp_file = None
        container_name = f"clawlite-sandbox-{uuid.uuid4().hex[:12]}"

        try:
            # Crear archivo temporal para el código
            tmp_dir = tempfile.mkdtemp(prefix="clawlite_sandbox_")
            tmp_file = os.path.join(tmp_dir, "script.py")
            with open(tmp_file, "w", encoding="utf-8") as f:
                f.write(code)

            # Construir comando docker
            docker_args = [
                "docker", "run",
                "--name", container_name,
            ] + level.to_docker_args() + [
                # Montar el script como read-only
                "-v", f"{tmp_file}:/workspace/script.py:ro",
                # Imagen
                f"{IMAGE_NAME}:{IMAGE_TAG}",
                # Argumento al ENTRYPOINT
                "/workspace/script.py",
            ]

            logger.debug(f"🐳 Running sandbox [{level.name}]: {container_name}")

            start = time.time()
            try:
                result = subprocess.run(
                    docker_args,
                    capture_output=True,
                    timeout=level.timeout_seconds,
                    text=True,
                )
                duration = time.time() - start

                success = result.returncode == 0
                if success:
                    logger.info(f"✅ Sandbox [{level.name}] completed in {duration:.2f}s")
                else:
                    logger.warning(f"⚠️  Sandbox [{level.name}] exit code {result.returncode}")

                return SandboxResult(
                    success=success,
                    stdout=result.stdout[:50000],
                    stderr=result.stderr[:10000],
                    exit_code=result.returncode,
                    duration=duration,
                )

            except subprocess.TimeoutExpired:
                duration = time.time() - start
                logger.warning(f"⏱  Sandbox [{level.name}] timed out after {level.timeout_seconds}s")
                # Forzar matar el contenedor
                self._force_kill(container_name)
                return SandboxResult(
                    success=False,
                    duration=duration,
                    timed_out=True,
                    error=f"Timeout after {level.timeout_seconds}s",
                )

        except Exception as e:
            logger.error(f"❌ Sandbox execution failed: {e}")
            return SandboxResult(success=False, error=str(e))

        finally:
            # Limpieza
            if tmp_file:
                try:
                    os.remove(tmp_file)
                    os.rmdir(os.path.dirname(tmp_file))
                except Exception:
                    pass
            # Por si acaso, matar el contenedor si quedó vivo
            self._force_kill(container_name, silent=True)

    def _force_kill(self, container_name: str, silent: bool = False):
        """Mata un contenedor por nombre."""
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                timeout=5,
            )
        except Exception as e:
            if not silent:
                logger.debug(f"Could not kill container {container_name}: {e}")


# Instancia singleton
docker_sandbox = DockerSandbox()
