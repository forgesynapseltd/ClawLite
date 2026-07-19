"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

tests/test_worker_pool_shutdown.py — Regresión del timeout acotado de
shutdown_pool_with_timeout (clawlite/agents/worker_pool.py). Antes,
AgentWorkerPool.shutdown() esperaba indefinidamente si un worker quedaba
trabado (ProcessPoolExecutor.shutdown() no soporta timeout en la
stdlib) -- este archivo prueba con ProcessPoolExecutor reales (livianos,
sin los agentes pesados de AgentWorkerPool) que ahora la espera queda
acotada, sin depender de atributos internos del ProcessPoolExecutor
(el auditor rechazó explícitamente esa vía por no ser parte de su
contrato público).
"""

import time
from concurrent.futures import ProcessPoolExecutor

from clawlite.agents.worker_pool import shutdown_pool_with_timeout


def _quick_task():
    return "listo"


def _slow_task(seconds):
    import time as _time
    _time.sleep(seconds)
    return "listo (tarde)"


def test_shutdown_returns_promptly_when_pool_is_idle():
    """Camino normal: sin trabajo pendiente, shutdown no debería acercarse
    siquiera al timeout configurado."""
    pool = ProcessPoolExecutor(max_workers=1)
    pool.submit(_quick_task).result()

    start = time.monotonic()
    shutdown_pool_with_timeout("test-quick", pool, timeout=10)
    elapsed = time.monotonic() - start

    assert elapsed < 5, f"shutdown() tardó {elapsed:.1f}s con el pool ocioso -- debería ser casi instantáneo"


def test_shutdown_is_bounded_when_worker_is_stuck():
    """Reproduce el bug real: un worker con una tarea larga en vuelo NO
    debe poder bloquear shutdown() más allá del timeout dado -- antes de
    este fix, esto colgaba indefinidamente."""
    pool = ProcessPoolExecutor(max_workers=1)
    pool.submit(_slow_task, 5)  # tarea de 5s en vuelo, deliberadamente > timeout de abajo

    start = time.monotonic()
    shutdown_pool_with_timeout("test-stuck", pool, timeout=1)
    elapsed = time.monotonic() - start

    assert elapsed < 3, (
        f"shutdown() tardó {elapsed:.1f}s con timeout=1s -- la espera debía quedar "
        "acotada en vez de esperar a que la tarea de 5s termine."
    )
