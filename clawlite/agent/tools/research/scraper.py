"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agent/tools/research/scraper.py — Scraper profundo en paralelo
Visita múltiples URLs simultáneamente y extrae contenido limpio y completo.
"""

import asyncio
import httpx
from bs4 import BeautifulSoup
from dataclasses import dataclass
from loguru import logger


MAX_CHARS_PER_PAGE = 8000
MAX_CONCURRENT = 4  # máximo de páginas visitadas en paralelo
REQUEST_TIMEOUT = 12


@dataclass
class ScrapedPage:
    url: str
    title: str
    content: str
    success: bool
    error: str = ""

    @property
    def word_count(self) -> int:
        return len(self.content.split())

    @property
    def is_useful(self) -> bool:
        """Una página es útil si tiene suficiente contenido real."""
        return self.success and self.word_count >= 50


class DeepScraper:
    """
    Scraper paralelo que visita múltiples URLs simultáneamente.
    Extrae contenido limpio — sin scripts, anuncios ni navegación.
    Filtra páginas de baja calidad automáticamente.
    """

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    }

    # Tags que no aportan contenido útil
    NOISE_TAGS = [
        "script", "style", "nav", "footer", "header", "aside",
        "advertisement", "ads", "cookie", "popup", "modal",
        "sidebar", "menu", "breadcrumb", "pagination",
    ]

    async def scrape_url(self, url: str) -> ScrapedPage:
        """Visita una URL y extrae su contenido limpio."""
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
                headers=self.HEADERS,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()

                # Solo procesar HTML
                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type:
                    return ScrapedPage(url=url, title="", content="", success=False,
                                       error=f"Non-HTML content: {content_type}")

            soup = BeautifulSoup(response.text, "html.parser")

            # Eliminar ruido
            for tag in soup(self.NOISE_TAGS):
                tag.decompose()

            # Extraer título
            title = ""
            if soup.title:
                title = soup.title.string.strip() if soup.title.string else ""
            if not title and soup.find("h1"):
                title = soup.find("h1").get_text(strip=True)

            # Intentar extraer el contenido principal
            content = self._extract_main_content(soup)

            # Limpiar y truncar
            lines = [l.strip() for l in content.splitlines() if l.strip()]
            clean = "\n".join(lines)

            if len(clean) > MAX_CHARS_PER_PAGE:
                clean = clean[:MAX_CHARS_PER_PAGE] + "\n[contenido truncado]"

            logger.debug(f"✅ Scraped: {url[:60]} — {len(clean.split())} words")
            return ScrapedPage(url=url, title=title, content=clean, success=True)

        except httpx.TimeoutException:
            return ScrapedPage(url=url, title="", content="", success=False, error="Timeout")
        except httpx.HTTPStatusError as e:
            return ScrapedPage(url=url, title="", content="", success=False,
                               error=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ScrapedPage(url=url, title="", content="", success=False, error=str(e))

    def _extract_main_content(self, soup: BeautifulSoup) -> str:
        """
        Intenta extraer el contenido principal de la página.
        Prioriza article > main > body con heurísticas de densidad de texto.
        """
        # Prioridad 1: etiquetas semánticas de contenido
        for selector in ["article", "main", "[role='main']", ".content", ".post", ".article"]:
            element = soup.select_one(selector)
            if element:
                text = element.get_text(separator="\n")
                if len(text.split()) > 100:
                    return text

        # Prioridad 2: el div con más texto
        divs = soup.find_all("div")
        if divs:
            best = max(divs, key=lambda d: len(d.get_text()))
            text = best.get_text(separator="\n")
            if len(text.split()) > 50:
                return text

        # Fallback: todo el body
        body = soup.find("body")
        return body.get_text(separator="\n") if body else soup.get_text(separator="\n")

    async def scrape_many(self, urls: list[str]) -> list[ScrapedPage]:
        """
        Scraping paralelo con límite de concurrencia.
        Devuelve solo las páginas con contenido útil.
        """
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def scrape_with_limit(url: str) -> ScrapedPage:
            async with semaphore:
                return await self.scrape_url(url)

        tasks = [scrape_with_limit(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        useful = [r for r in results if r.is_useful]
        logger.info(f"🌐 Scraped {len(useful)}/{len(urls)} useful pages")

        return useful
