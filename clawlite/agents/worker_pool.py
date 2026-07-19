"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agents/worker_pool.py — Aislamiento de proceso para ResearchAgent,
ContextAgent y BrandAgent. CodingAgent queda fuera (ya aislado vía
Docker/AgentSandbox -- mecanismo distinto, apropiado para ejecución de
código real).

CONTRATO DE CONSTRUCCIÓN (invariante de arquitectura, no tocar sin
actualizar este contrato explícitamente):
- Cada agente vive en su PROPIO ProcessPoolExecutor(max_workers=1) --
  nunca comparten pool. Es lo único que garantiza afinidad de proceso
  (un pool con N>1 workers no promete qué worker ejecuta cada tarea).
- Los agentes se construyen EXCLUSIVAMENTE a partir de config persistente
  y serializable (db_path: str) -- nunca a partir de un objeto ya vivo
  del proceso principal.
- PROHIBIDO cruzar el límite de proceso: conexiones SQLite abiertas,
  modelos ya cargados en memoria, clientes HTTP con sesión activa,
  cualquier caché mutable compartida.
- EmbeddingEngine() no toma configuración hoy (verificado: se instancia
  sin argumentos en main.py, ver embeddings.py). Si en el futuro acepta
  config (modelo, backend), esa config debe pasarse al worker igual que
  db_path -- mismo principio, sin excepción.
- Toda dependencia NUEVA que se agregue a estos agentes debe poder
  construirse desde config persistente dentro del worker, o el agente
  deja de ser apto para aislamiento hasta actualizar este contrato.

COSTO EN PLATAFORMAS 'spawn' (Windows, macOS):
- En 'fork' (Linux/Docker, target de producción) el costo de los 3
  procesos residentes es bajo (COW de memoria al fork). En 'spawn' cada
  uno de los 3 pools levanta un intérprete Python completo e
  independiente -- 3 intérpretes residentes todo el tiempo que ClawLite
  esté corriendo, no un pico transitorio. Aceptado porque el target de
  producción es Linux/Docker; quien corra ClawLite nativo en
  Windows/macOS debe saber que este aislamiento tiene ese precio fijo.
- WORKER_POOL_LAZY_INIT=true (variable de entorno, default false) atenúa
  ese costo en máquinas modestas: cada pool se crea recién en su primer
  uso real, no los 3 de entrada. No es auto-detección de hardware (el
  proyecto prefiere config explícita a magia oculta) -- es el usuario
  quien decide si su máquina lo necesita. Sigue sin violar el invariante
  de arriba (1 worker dedicado por agente): lo único que cambia es
  CUÁNDO se crea cada pool, nunca que compartan uno.

CONTRATO DE CICLO DE VIDA (shutdown):
- shutdown() intenta esperar (wait=True) cualquier llamada en vuelo,
  incluida una _learn_safely pendiente de ContextAgent -- nunca cancela
  trabajo a medias por decisión propia. Mismo principio que
  JobRunner.stop(): dejar terminar el ciclo en curso en vez de matarlo a
  la fuerza.
- La espera está ACOTADA por SHUTDOWN_TIMEOUT_SECONDS (default 30s) por
  pool. ProcessPoolExecutor.shutdown() no soporta timeout en la stdlib,
  así que se corre en un thread daemon aparte y se hace
  thread.join(timeout=...) -- NUNCA se accede a atributos internos ni
  PIDs del ProcessPoolExecutor para forzar nada (rechazado explícitamente
  por el auditor: no son parte de su contrato público, y manipularlos
  podría dejarlo en un estado inconsistente si algo más tarde intentara
  usarlo). Si el timeout se cumple, se loguea una alerta clara y
  shutdown() simplemente retorna -- el thread daemon en segundo plano
  sigue intentando terminar por su cuenta, pero deja de bloquear al
  llamador.
- Consecuencia de agotar el timeout: los procesos worker pueden quedar
  vivos hasta que el SO los limpie -- EXACTAMENTE el mismo límite que ya
  estaba aceptado para el camino de señal dura/excepción no capturada
  (ver debajo), ahora también aplicable si el camino ordenado tarda
  demasiado. No es un riesgo nuevo, es extender el mismo riesgo ya
  aceptado a un caso más.
- Una señal dura o una excepción no capturada que tumbe el proceso
  principal deja los procesos hijo vivos hasta que el SO los limpie --
  mismo límite que ya aplica a cualquier subproceso de este codebase
  (p.ej. Docker vía AgentSandbox), no una debilidad nueva.
"""

import asyncio
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from loguru import logger

SHUTDOWN_TIMEOUT_SECONDS = 30
WORKER_POOL_LAZY_INIT = os.getenv("WORKER_POOL_LAZY_INIT", "false").lower() == "true"

# Estado del worker -- SOLO existe dentro del proceso hijo correspondiente,
# nunca se comparte con el proceso principal ni entre workers.
_worker_research_agent = None
_worker_context_agent = None
_worker_brand_agent = None


def _init_research_worker(db_path: str):
    """Corre UNA VEZ en el proceso dedicado a ResearchAgent. No atrapa
    excepciones: si algo falla, se propaga y el pool queda con su único
    worker no inicializado -- nunca un worker a medio construir."""
    global _worker_research_agent
    import os
    from clawlite.agents.skill_store import SkillStore
    from clawlite.memory.embeddings import EmbeddingEngine
    from clawlite.agents.specialized.research_agent import ResearchAgent

    skill_store = SkillStore(db_path, embedding_engine=EmbeddingEngine())
    _worker_research_agent = ResearchAgent(skill_store)
    logger.info(f"🔒 Worker ResearchAgent inicializado (PID {os.getpid()})")


def _init_context_worker(db_path: str):
    """Corre UNA VEZ en el proceso dedicado a ContextAgent."""
    global _worker_context_agent
    import os
    from clawlite.memory.profile import DeepMemory, UserProfile
    from clawlite.agents.memory.hierarchical import HierarchicalMemory
    from clawlite.agents.specialized.context_agent import ContextAgent

    deep_memory = DeepMemory(db_path)
    _worker_context_agent = ContextAgent(UserProfile(deep_memory), HierarchicalMemory(db_path))
    logger.info(f"🔒 Worker ContextAgent inicializado (PID {os.getpid()})")


def _init_brand_worker(db_path: str):
    """Corre UNA VEZ en el proceso dedicado a BrandAgent."""
    global _worker_brand_agent
    import os
    from clawlite.agents.skill_store import SkillStore
    from clawlite.memory.embeddings import EmbeddingEngine
    from clawlite.memory.profile import DeepMemory
    from clawlite.agent.tools.brand import BrandManager
    from clawlite.agents.specialized.brand_agent import BrandAgent

    skill_store = SkillStore(db_path, embedding_engine=EmbeddingEngine())
    _worker_brand_agent = BrandAgent(BrandManager(DeepMemory(db_path)), skill_store)
    logger.info(f"🔒 Worker BrandAgent inicializado (PID {os.getpid()})")


def _run_research_in_worker(user_id, query, is_news, term_groups, query_type) -> dict:
    """Función top-level picklable -- se ejecuta DENTRO del proceso worker."""
    return asyncio.run(
        _worker_research_agent.run(
            user_id, query, is_news=is_news, term_groups=term_groups, query_type=query_type
        )
    )


async def _context_run_and_wait(user_id: str, query: str) -> dict:
    """Espera explícitamente SOLO la tarea de aprendizaje que ContextAgent
    creó (vía await_pending_learning) -- no todas las tareas del loop.
    Necesario porque asyncio.run() cancela cualquier tarea pendiente en
    cuanto la corrutina principal termina; sin esto, el aprendizaje en
    background nunca llegaría a persistir dentro de un worker aislado."""
    result = await _worker_context_agent.run(user_id, query)
    await _worker_context_agent.await_pending_learning()
    return result


def _run_context_in_worker(user_id, query) -> dict:
    """Función top-level picklable -- se ejecuta DENTRO del proceso worker."""
    return asyncio.run(_context_run_and_wait(user_id, query))


def _run_brand_in_worker(user_id, query) -> dict:
    """Función top-level picklable -- se ejecuta DENTRO del proceso worker."""
    return asyncio.run(_worker_brand_agent.run(user_id, query))


def shutdown_pool_with_timeout(name: str, pool: ProcessPoolExecutor, timeout: float) -> None:
    """Espera a que `pool.shutdown(wait=True)` termine, acotado por
    `timeout` segundos -- ver CONTRATO DE CICLO DE VIDA en el docstring del
    módulo para el razonamiento completo (por qué NO se accede a atributos
    internos/PIDs del ProcessPoolExecutor para forzar nada). Función de
    módulo (no método) para poder probarla con ProcessPoolExecutor reales y
    livianos en tests, sin necesitar construir los 3 agentes pesados de
    AgentWorkerPool."""
    t = threading.Thread(target=pool.shutdown, kwargs={"wait": True}, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        logger.warning(
            f"⚠️ Pool '{name}' no terminó de apagarse en {timeout}s -- "
            "siguiendo sin esperar más. El proceso worker puede quedar "
            "vivo hasta que el SO lo limpie (mismo límite ya aceptado "
            "para el camino de señal dura, ver worker_pool.py)."
        )


class AgentWorkerPool:
    """Tres pools de un solo worker cada uno -- ResearchAgent, ContextAgent
    y BrandAgent corren cada uno en su propio proceso OS dedicado,
    garantizado (no solo probable) por la afinidad 1-worker-por-pool."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._research_pool = None
        self._context_pool = None
        self._brand_pool = None
        if not WORKER_POOL_LAZY_INIT:
            self._research_pool = self._new_pool(_init_research_worker)
            self._context_pool = self._new_pool(_init_context_worker)
            self._brand_pool = self._new_pool(_init_brand_worker)

    def _new_pool(self, initializer) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(max_workers=1, initializer=initializer, initargs=(self._db_path,))

    def _get_research_pool(self) -> ProcessPoolExecutor:
        if self._research_pool is None:
            self._research_pool = self._new_pool(_init_research_worker)
        return self._research_pool

    def _get_context_pool(self) -> ProcessPoolExecutor:
        if self._context_pool is None:
            self._context_pool = self._new_pool(_init_context_worker)
        return self._context_pool

    def _get_brand_pool(self) -> ProcessPoolExecutor:
        if self._brand_pool is None:
            self._brand_pool = self._new_pool(_init_brand_worker)
        return self._brand_pool

    async def run_research(self, user_id, query, is_news=False, term_groups=None, query_type=None) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._get_research_pool(), _run_research_in_worker, user_id, query, is_news, term_groups, query_type
        )

    async def run_context(self, user_id, query) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._get_context_pool(), _run_context_in_worker, user_id, query)

    async def run_brand(self, user_id, query) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._get_brand_pool(), _run_brand_in_worker, user_id, query)

    def shutdown(self, timeout: float = SHUTDOWN_TIMEOUT_SECONDS):
        """Espera a que cada pool termine su trabajo en vuelo, acotado por
        `timeout` segundos -- ver CONTRATO DE CICLO DE VIDA en el docstring
        del módulo. `timeout` es parámetro (no solo constante global) para
        que la batería de regresión pueda probar el camino de timeout real
        sin esperar SHUTDOWN_TIMEOUT_SECONDS completos. Pools nunca creados
        (WORKER_POOL_LAZY_INIT=true y ese agente nunca se usó) se saltan --
        no hay nada que apagar."""
        for name, pool in (
            ("research", self._research_pool),
            ("context", self._context_pool),
            ("brand", self._brand_pool),
        ):
            if pool is not None:
                shutdown_pool_with_timeout(name, pool, timeout)
