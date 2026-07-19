"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

governance/ — Capa de gobernanza (motor de confianza fail-closed).
Punto único por el que pasa TODA acción con efecto antes de ejecutarse.
"""

from clawlite.governance.action_guard import (
    action_guard,
    ActionGuard,
    Mandate,
    MandateOrigin,
    Decision,
    Outcome,
    RiskTier,
    Policy,
    GovernanceDenied,
)

__all__ = [
    "action_guard",
    "ActionGuard",
    "Mandate",
    "MandateOrigin",
    "Decision",
    "Outcome",
    "RiskTier",
    "Policy",
    "GovernanceDenied",
]
