"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

sandbox/sandbox_levels.py — Definición de niveles de aislamiento
Cada nivel especifica recursos, red y filesystem permitidos.
"""

from dataclasses import dataclass, field


@dataclass
class SandboxLevel:
    """
    Configuración de un nivel de sandbox.
    Define exactamente qué recursos puede usar el contenedor.
    """
    name: str
    description: str
    network_enabled: bool
    allowed_domains: list[str] = field(default_factory=list)
    timeout_seconds: int = 30
    memory_limit: str = "256m"
    cpu_limit: float = 0.5
    read_only_paths: list[str] = field(default_factory=list)
    write_paths: list[str] = field(default_factory=list)

    def to_docker_args(self) -> list[str]:
        """Convierte la config en argumentos para docker run."""
        args = [
            "--rm",                                    # Auto-eliminar al terminar
            "--memory", self.memory_limit,
            "--memory-swap", self.memory_limit,        # No swap
            "--cpus", str(self.cpu_limit),
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",                       # Sin capabilities
            "--pids-limit", "100",                     # Max procesos
        ]

        # Red
        if not self.network_enabled:
            args.extend(["--network", "none"])
        else:
            # DNS explícito para evitar fallos de resolución en Windows/WSL
            args.extend([
                "--dns", "8.8.8.8",
                "--dns", "1.1.1.1",
            ])

        # Read-only filesystem por defecto
        args.append("--read-only")

        # tmpfs para que el contenedor pueda escribir en /tmp y /workspace
        args.extend([
            "--tmpfs", "/tmp:rw,size=64m,mode=1777",
            "--tmpfs", "/workspace:rw,size=64m,mode=1777",
        ])

        # Mounts opcionales
        for path in self.read_only_paths:
            args.extend(["-v", f"{path}:/sandbox_input:ro"])
        for path in self.write_paths:
            args.extend(["-v", f"{path}:/sandbox_output:rw"])

        return args


# Tres niveles predefinidos
LEVEL_ISOLATED = SandboxLevel(
    name="isolated",
    description="Aislamiento total — sin red, sin filesystem",
    network_enabled=False,
    timeout_seconds=30,
    memory_limit="256m",
    cpu_limit=0.5,
)

LEVEL_NETWORKED = SandboxLevel(
    name="networked",
    description="Con red para scraping/HTTPS — sin filesystem del host",
    network_enabled=True,
    timeout_seconds=60,
    memory_limit="512m",
    cpu_limit=1.0,
)

LEVEL_FILESYSTEM = SandboxLevel(
    name="filesystem",
    description="Con filesystem read-only de entrada + escritura limitada",
    network_enabled=False,
    timeout_seconds=120,
    memory_limit="1g",
    cpu_limit=1.0,
)


def get_level(name: str) -> SandboxLevel:
    """Obtiene un nivel por nombre."""
    levels = {
        "isolated": LEVEL_ISOLATED,
        "networked": LEVEL_NETWORKED,
        "filesystem": LEVEL_FILESYSTEM,
    }
    if name not in levels:
        raise ValueError(f"Nivel desconocido: {name}. Disponibles: {list(levels.keys())}")
    return levels[name]
