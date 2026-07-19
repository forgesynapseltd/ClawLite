"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

tests/test_worker_pool_lazy_init.py — Regresión de WORKER_POOL_LAZY_INIT
(clawlite/agents/worker_pool.py). Deuda técnica real: los 3 pools
(ResearchAgent, ContextAgent, BrandAgent) se creaban siempre los 3 de
entrada, sin ninguna opción para máquinas modestas que no usan los 3
tipos de agente. No se sometió a submit() ningún task real en estos
tests -- eso dispararía la construcción pesada de los agentes reales
dentro del worker (EmbeddingEngine, SkillStore, etc.), que es ortogonal
a lo que este fix cambia: CUÁNDO se crea el ProcessPoolExecutor, no qué
hace el worker una vez que arranca.
"""

import clawlite.agents.worker_pool as worker_pool
from clawlite.agents.worker_pool import AgentWorkerPool


def test_eager_by_default_creates_all_three_pools(monkeypatch):
    monkeypatch.setattr(worker_pool, "WORKER_POOL_LAZY_INIT", False)
    pool = AgentWorkerPool(db_path="unused.db")
    try:
        assert pool._research_pool is not None
        assert pool._context_pool is not None
        assert pool._brand_pool is not None
    finally:
        pool.shutdown(timeout=5)


def test_lazy_init_creates_no_pools_until_first_use(monkeypatch):
    monkeypatch.setattr(worker_pool, "WORKER_POOL_LAZY_INIT", True)
    pool = AgentWorkerPool(db_path="unused.db")
    try:
        assert pool._research_pool is None
        assert pool._context_pool is None
        assert pool._brand_pool is None

        pool._get_research_pool()  # simula el primer uso real de ResearchAgent

        assert pool._research_pool is not None
        assert pool._context_pool is None, "ContextAgent nunca se usó -- su pool no debería existir"
        assert pool._brand_pool is None, "BrandAgent nunca se usó -- su pool no debería existir"
    finally:
        pool.shutdown(timeout=5)


def test_shutdown_skips_pools_never_created(monkeypatch):
    """Con WORKER_POOL_LAZY_INIT=true y ningún agente usado, shutdown() no
    debe fallar ni intentar apagar pools inexistentes (None)."""
    monkeypatch.setattr(worker_pool, "WORKER_POOL_LAZY_INIT", True)
    pool = AgentWorkerPool(db_path="unused.db")

    pool.shutdown(timeout=5)  # no debe lanzar excepción
