"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

_version.py — Única fuente de verdad de la versión de ClawLite. El
archivo VERSION en la raíz del repo es lo único que se edita a mano en
cada release; tanto este módulo como installer/ClawLite.iss lo leen
directo, para que el paquete Python y el instalador nunca quedan
desincronizados entre sí.

Reusa _bundle_root() de clawlite.setup (misma necesidad: localizar un
archivo que vive en la raíz del bundle, no relativo al cwd -- ver el
bug real de .env.example que motivó esa función).
"""

from clawlite.setup import _bundle_root


def _read_version() -> str:
    return (_bundle_root() / "VERSION").read_text(encoding="utf-8").strip()


__version__ = _read_version()
