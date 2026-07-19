"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

Regresion manual (NO forma parte de `pytest -q`): llama al LLM real, no es
determinista y es lenta. Ejecutar tras cualquier cambio de modelo o de
EXTRACT_EVENT_PROMPT para confirmar que el invariante sigue vigente:

    D:\\ClawLite\\.venv\\Scripts\\python.exe tests\\regression_calendar_time_invariant.py

Contexto (14-15 jul 2026): el extractor de calendario fabricaba date/time en
83% (20/24) de mensajes subespecificados ("agendame algo con el dentista")
en vez de is_event=false. Experimento causal A/B/C localizo el HH:MM del
contexto como fuente de contaminacion, pero una bateria de no-regresion
sobre expresiones de hora relativa ("en dos horas") demostro que quitar
HH:MM del contexto rompe resolucion legitima - se descarto esa via. Fix
aplicado: instruccion explicita "time: null si no se menciona, nunca
inferir" en ambos contratos (date/weekday), sin tocar el contexto. Resultado
contra codigo real: 0/24 (era 20/24). Este archivo fija esa bateria como
prueba de regresion permanente.
"""

import sys
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clawlite.agent.core as core_mod
from clawlite.personality.language import set_turn_language, clear_turn_language


class _FakeReminderTool:
    def _next_weekday(self, target_weekday, hour, minute):
        now = datetime.now()
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        days_ahead = (target_weekday - candidate.weekday()) % 7
        if days_ahead == 0 and candidate <= now:
            days_ahead = 7
        return candidate + timedelta(days=days_ahead)


class _StatefulDeepMemory:
    """MagicMock() por defecto es truthy incluso sin configurar — rompería
    los chequeos 'bool(pending)' de la máquina de estados pending_event
    (15 jul). Fake mínimo con semántica real de ausencia/presencia."""
    def __init__(self):
        self.store = {}

    def get_pattern(self, uid, key):
        return self.store.get((uid, key), {})

    def set_pattern(self, uid, key, data):
        self.store[(uid, key)] = data

    def clear_pattern(self, uid, key):
        self.store.pop((uid, key), None)


class _FakeAgent:
    _wants_event_creation = core_mod.Agent._wants_event_creation
    _resolve_calendar_day_kind = core_mod.Agent._resolve_calendar_day_kind
    _extract_event_fields = core_mod.Agent._extract_event_fields
    _event_missing = staticmethod(core_mod.Agent._event_missing)
    _merge_event_fields = staticmethod(core_mod.Agent._merge_event_fields)
    _resolve_event_start = staticmethod(core_mod.Agent._resolve_event_start)
    _fallback_event_title = staticmethod(core_mod.Agent._fallback_event_title)
    _build_event_draft = core_mod.Agent._build_event_draft
    _is_recent = staticmethod(core_mod.Agent._is_recent)

    def __init__(self):
        self.reminder_tool = _FakeReminderTool()
        self.deep_memory = _StatefulDeepMemory()
        self.memory = MagicMock()

    def set_session(self, *a, **k):
        pass


# Mensajes subespecificados (sin dia ni hora explicitos) que reproducian la
# fabricacion antes del fix del 15 jul.
MENSAJES_SUBESPECIFICADOS = [
    "agéndame algo con el dentista",
    "necesito una cita con el dentista",
    "reserva hora con el dentista",
    "quiero agendar al dentista",
    "programa una cita médica",
    "agenda una reunión con Carlos",
    "necesito ver al dentista pronto",
    "arma una cita con el dentista",
]
REPS = 3


async def main():
    clear_turn_language()
    set_turn_language("es")
    total = 0
    fabrica = 0
    with patch("clawlite.agent.core.gmail_tool.is_authenticated", return_value=True):
        for mensaje in MENSAJES_SUBESPECIFICADOS:
            for i in range(REPS):
                total += 1
                fake = _FakeAgent()
                res = await core_mod.Agent._handle_calendar_event(fake, "u1", mensaje)
                if res is not None and "📅" in res[0]:
                    fabrica += 1
                    print(f"[FALLO] {mensaje!r} #{i + 1} -> tarjeta fabricada: {res[0][:60]!r}")

    print(f"\n{total - fabrica}/{total} respetaron el invariante (objetivo: {total}/{total})")
    if fabrica:
        print(f"REGRESION: {fabrica} fabricaciones detectadas.")
        sys.exit(1)
    print("OK — invariante 'sin evidencia textual -> null' se mantiene.")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
