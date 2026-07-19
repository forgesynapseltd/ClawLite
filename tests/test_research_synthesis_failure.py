"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

tests/test_research_synthesis_failure.py — Camino de fallo de la síntesis.

Guarda la garantía del fix del 4-5 jul (crisis de VRAM): si la síntesis LLM
falla, el usuario recibe el mensaje limpio del catálogo multilingüe
("research_synthesis_failed", #6 fase 1) — NUNCA contenido crudo de
páginas scrapeadas — y ResearchResult.synthesis_failed=True (la bandera
estructural que core.py consume para suprimir el footer "Verified research").
Este camino nunca se había ejercitado: Ollama no volvió a fallar tras la
unificación de modelo. Este test lo ejercita de forma determinista, para
siempre. Pipeline real (research() completo); solo se stubbean los bordes
de red (Tavily/scraper/factchecker) y el LLM de síntesis.
"""

import asyncio
from unittest.mock import patch

import pytest

from clawlite.agent.tools.research.engine import ResearchEngine
from clawlite.personality.catalog import msg as catalog_msg
from clawlite.agent.tools.research.scraper import ScrapedPage
from clawlite.agent.tools.research.factchecker import FactCheckResult

RAW_MARKER = "CONTENIDO_CRUDO_DE_PAGINA_QUE_JAMAS_DEBE_LLEGAR_AL_USUARIO"


@pytest.fixture
def engine():
    """Engine fresco (no el singleton) con los bordes de red stubbeados:
    Tavily y scraper devuelven material canned; el factchecker devuelve un
    resultado que alcanza el piso (corta la iteración en la pasada 1)."""
    eng = ResearchEngine()

    async def fake_search_urls(*args, **kwargs):
        return ["https://example.com/a"]

    async def fake_scrape_many(urls, *args, **kwargs):
        return [ScrapedPage(
            url="https://example.com/a", title="prueba",
            content=f"prueba de contenido {RAW_MARKER} " * 30, success=True,
        )]

    async def fake_check(*args, **kwargs):
        return FactCheckResult(claims=[], verified_count=3,
                               single_source_count=0, conflicting_count=0,
                               sources_checked=1)

    eng._search_urls = fake_search_urls
    eng.scraper.scrape_many = fake_scrape_many
    eng.fact_checker.check = fake_check
    return eng


def _run(engine):
    return asyncio.run(engine.research(
        "prueba de fallo", term_groups=[["prueba"]], query_type="single_topic",
    ))


def test_synthesis_failure_never_leaks_raw_content(engine):
    """LLM de síntesis caído → mensaje de fallo limpio + bandera activa.
    NI UN BYTE del contenido scrapeado llega a la respuesta."""
    with patch("clawlite.agent.tools.research.engine.llm") as mock_llm:
        async def broken_complete(**kwargs):
            raise RuntimeError("ollama sin respuesta (simulado)")
        mock_llm.complete = broken_complete
        result = _run(engine)

    assert result.synthesis_failed is True
    # Misma resolución de idioma que hizo el engine (test sin turno → reserva EN):
    # lo que se garantiza es "mensaje del catálogo, jamás contenido crudo".
    assert result.answer == catalog_msg("research_synthesis_failed")
    assert RAW_MARKER not in result.answer  # la regresión exacta que esto guarda
    # Los metadatos sobreviven al fallo (core decide qué mostrar con la bandera).
    assert result.sources == ["https://example.com/a"]


def test_synthesis_success_keeps_flag_off(engine):
    """Control positivo: con síntesis sana, la bandera queda apagada y la
    respuesta es la síntesis (el footer de core depende de este contrato)."""
    with patch("clawlite.agent.tools.research.engine.llm") as mock_llm:
        async def healthy_complete(**kwargs):
            return "Respuesta sintetizada de prueba.", "local"
        mock_llm.complete = healthy_complete
        result = _run(engine)

    assert result.synthesis_failed is False
    assert result.answer == "Respuesta sintetizada de prueba."
