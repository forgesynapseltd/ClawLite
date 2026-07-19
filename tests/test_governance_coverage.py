"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

tests/test_governance_coverage.py — Red de seguridad de gobernanza (Vellum).

Verifica ESTRUCTURALMENTE, por análisis estático del código, la consistencia entre
el registro de políticas del ActionGuard (_POLICIES) y los puntos donde realmente se
llama a authorize(). No ejecuta el agente: parsea el árbol fuente.

Atrapa dos modos de fallo que hoy dependen de la disciplina del desarrollador:
  1. Un action_type con typo / no registrado en un call site → el kernel lo denegaría
     siempre en silencio (default-deny). El test lo caza antes de producción.
  2. Una acción de ALTO IMPACTO registrada pero sin NINGÚN authorize() en su camino →
     protección muerta. El test obliga a que toda política HIGH esté realmente enforced.

Lo que este test NO cubre (y por eso viene el decorador @guarded como paso A): un sink
nuevo que se añada SIN registrar política ni llamar a authorize en absoluto. Eso se
ataca acoplando enforcement al borde del método; aquí cubrimos la consistencia del
registro con los call sites existentes.
"""

import ast
from pathlib import Path

from clawlite.governance.action_guard import _POLICIES, RiskTier

_CLAWLITE_SRC = Path(__file__).resolve().parent.parent / "clawlite"


def _collect_authorized_action_types() -> set[str]:
    """Recorre el árbol fuente de clawlite/ y recoge el primer argumento string-literal
    de cada llamada *.authorize(...) o *.enforce(...) (ambos son puntos de enforcement
    del kernel). Determinista: AST, no regex; ignora llamadas cuyo action_type no sea un
    literal (no las hay hoy, pero no rompería el test)."""
    used: set[str] = set()
    for py_file in _CLAWLITE_SRC.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("authorize", "enforce") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    used.add(first.value)
    return used


def test_every_call_site_uses_a_registered_action_type():
    """Ningún call site llama a authorize() con un action_type fuera de _POLICIES.
    Un typo aquí provocaría DENY silencioso en producción (default-deny)."""
    used = _collect_authorized_action_types()
    registered = set(_POLICIES.keys())
    unregistered = used - registered
    assert not unregistered, (
        f"authorize() llamado con action_type(s) NO registrados en _POLICIES "
        f"(se denegarían siempre, en silencio): {sorted(unregistered)}"
    )


def test_every_high_impact_action_is_enforced():
    """Toda política de riesgo HIGH debe tener al menos un authorize() en su camino.
    Una HIGH registrada pero nunca invocada = protección muerta (bypass silencioso)."""
    used = _collect_authorized_action_types()
    high_actions = {name for name, p in _POLICIES.items() if p.risk == RiskTier.HIGH}
    unenforced = high_actions - used
    assert not unenforced, (
        f"Acción(es) de ALTO IMPACTO registradas en _POLICIES pero sin ningún "
        f"authorize() en el código (protección muerta): {sorted(unenforced)}"
    )


def test_collector_finds_the_known_call_sites():
    """Sanity del propio colector: si dejara de encontrar los action_types conocidos,
    los dos tests anteriores pasarían en falso (vacío ⊆ todo). Ancla el colector."""
    used = _collect_authorized_action_types()
    for expected in ("send_email", "create_calendar_event", "clear_memory", "execute_code"):
        assert expected in used, (
            f"El colector AST no encontró '{expected}' — los tests de cobertura podrían "
            f"estar pasando en falso. Revisar _collect_authorized_action_types()."
        )
