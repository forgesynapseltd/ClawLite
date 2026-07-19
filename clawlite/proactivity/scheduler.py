"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

proactivity/scheduler.py — Loop de background con APScheduler
Ejecuta el ProactivityEngine cada 15 minutos de forma silenciosa.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from clawlite.proactivity.engine import ProactivityEngine


class ProactivityScheduler:
    """
    Wrapper sobre APScheduler que ejecuta el motor de proactividad
    en background cada 15 minutos.
    """

    INTERVAL_MINUTES = 15

    def __init__(self, engine: ProactivityEngine):
        self.engine = engine
        self.scheduler = AsyncIOScheduler()

    def start(self):
        self.scheduler.add_job(
            self.engine.run_cycle,
            trigger="interval",
            minutes=self.INTERVAL_MINUTES,
            id="proactivity_cycle",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info(f"⏱️  ProactivityScheduler iniciado — ciclo cada {self.INTERVAL_MINUTES} min")

    def stop(self):
        self.scheduler.shutdown(wait=False)
        logger.info("⏱️  ProactivityScheduler detenido")
