"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agent/tools/reader.py — Lector universal de contenido
PDF, URLs, imágenes y texto largo. Todo lo que el usuario manda.
"""

import httpx
import pypdf
from pathlib import Path
from bs4 import BeautifulSoup
from loguru import logger


MAX_CHARS = 12000  # Límite de contexto seguro para cualquier LLM


class ContentReader:

    async def read_pdf(self, file_path: str) -> str:
        """
        Extrae texto de un PDF. 100% local, sin cloud.

        Usa pypdf (BSD) en vez de PyMuPDF (dual-licenciado AGPLv3/comercial
        -- incompatible con distribuir ClawLite como Apache 2.0, confirmado
        con la propia página de licenciamiento de Artifex). El único uso
        real acá es extracción de texto plano página por página, sin
        imágenes, anotaciones, OCR ni edición -- pypdf cubre esa capacidad
        exacta, así que el reemplazo no pierde funcionalidad.
        """
        try:
            pdf_reader = pypdf.PdfReader(file_path)
            pages = []
            for i, page in enumerate(pdf_reader.pages):
                text = page.extract_text().strip()
                if text:
                    pages.append(f"[Página {i+1}]\n{text}")

            full_text = "\n\n".join(pages)
            logger.info(f"📄 PDF leído: {len(full_text)} chars, {len(pages)} páginas")

            if len(full_text) > MAX_CHARS:
                full_text = full_text[:MAX_CHARS] + f"\n\n[Documento truncado — {len(pages)} páginas totales]"

            return full_text if full_text else "El PDF no contiene texto extraíble."

        except Exception as e:
            logger.error(f"❌ Error leyendo PDF: {e}")
            return f"No pude leer el PDF: {str(e)}"

    async def read_url(self, url: str) -> str:
        """Visita una URL y extrae el contenido limpio."""
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; ClawLite/1.0)"}
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Eliminar scripts, estilos y navegación
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()

            # Extraer título y texto principal
            title = soup.title.string.strip() if soup.title else ""
            text = soup.get_text(separator="\n", strip=True)

            # Limpiar líneas vacías múltiples
            lines = [l for l in text.splitlines() if l.strip()]
            clean = "\n".join(lines)

            result = f"**{title}**\n\n{clean}" if title else clean
            logger.info(f"🌐 URL leída: {url[:60]} — {len(result)} chars")

            if len(result) > MAX_CHARS:
                result = result[:MAX_CHARS] + "\n\n[Contenido truncado]"

            return result

        except Exception as e:
            logger.error(f"❌ Error leyendo URL {url}: {e}")
            return f"No pude acceder a esa URL: {str(e)}"

    async def read_text_file(self, file_path: str) -> str:
        """Lee archivos de texto plano (.txt, .md, .csv, .py, etc.)"""
        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            logger.info(f"📝 Archivo de texto leído: {len(content)} chars")

            if len(content) > MAX_CHARS:
                content = content[:MAX_CHARS] + "\n\n[Archivo truncado]"

            return content
        except Exception as e:
            return f"No pude leer el archivo: {str(e)}"

    def detect_url(self, text: str) -> str | None:
        """Detecta si el mensaje contiene una URL."""
        import re
        pattern = r'https?://[^\s]+'
        match = re.search(pattern, text)
        return match.group(0) if match else None


reader = ContentReader()
