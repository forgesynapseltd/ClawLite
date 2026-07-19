"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agent/tools/research/factchecker.py — Contrastador de fuentes

Lee TODAS las fuentes recolectadas en una sola pasada y contrasta sus
afirmaciones entre sí: qué corroboran varias (verificado), qué dice una sola
(fuente única) y en qué se contradicen (conflicto). Es el agente que recopila
la información final contrastada para presentarla con su nivel de confianza.
No garantiza verdad absoluta — garantiza consistencia entre fuentes.

DISEÑO (por qué este archivo es como es):
- La VERIFICACIÓN CRUZADA es el diferenciador del producto: un dato corroborado
  por 2+ fuentes independientes vale más que uno suelto. NO se elimina.
- Pero la salida estructurada de un modelo local puede fallar el parsing (la
  literatura lo sitúa en 8-15% sin enforcement). Por eso NO se confía a ciegas:
  se trata la salida del LLM como una API NO confiable —parsing tolerante, UN
  reintento, y fallback a single-source si aun así falla—. La respuesta al
  usuario NUNCA se pierde; en el peor caso se degrada a fuentes sin cruzar.
- Usa SIEMPRE la cascada del sistema (llm.complete con structured=True), que ya
  selecciona el modelo adecuado por proveedor (Ollama local → Groq → etc.). NO
  se hardcodea ningún modelo: eso rompería el local-first configurable.
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from loguru import logger
from clawlite.agent.tools.research.scraper import ScrapedPage
from clawlite.llm.client import llm
from clawlite.sandbox.guard import wrap_untrusted


class Confidence(str, Enum):
    VERIFIED = "VERIFIED"           # 2+ fuentes independientes coinciden
    SINGLE_SOURCE = "SINGLE_SOURCE" # Solo 1 fuente lo menciona
    CONFLICTING = "CONFLICTING"     # Fuentes se contradicen
    UNVERIFIABLE = "UNVERIFIABLE"   # No se puede verificar


@dataclass
class Claim:
    text: str
    confidence: Confidence
    sources: list[str] = field(default_factory=list)
    conflict_note: str = ""


@dataclass
class FactCheckResult:
    claims: list[Claim]
    verified_count: int
    single_source_count: int
    conflicting_count: int
    sources_checked: int

    def summary(self) -> str:
        return (
            f"Fuentes analizadas: {self.sources_checked} | "
            f"Verificadas: {self.verified_count} | "
            f"Fuente única: {self.single_source_count} | "
            f"En conflicto: {self.conflicting_count}"
        )

    @staticmethod
    def calibration_note(verified: int, total: int, checked: int) -> str:
        """
        Mandato de confianza ACCIONABLE para el sintetizador, derivado del ratio
        de verificación. Fuente ÚNICA de verdad: la consumen tanto el research
        engine (camino directo) como el synthesizer multi-agente, para que la
        calibración del tono sea idéntica venga la respuesta por donde venga.
        Opera sobre enteros (no sobre el objeto) para servir a ambos llamadores.
        Agnóstico de idioma: instruye AL MODELO, no impone texto al usuario.
        """
        if not total:
            return (
                "UNKNOWN: no verification data. Treat findings as unconfirmed and say so if you "
                "present specific claims."
            )
        ratio = verified / total
        if ratio >= 0.6:
            return (
                f"HIGH: {verified} of {total} claims confirmed by multiple of {checked} "
                f"sources. Most findings are well supported — you may state them with confidence."
            )
        if ratio >= 0.3:
            return (
                f"MIXED: {verified} of {total} claims confirmed across {checked} sources; "
                f"the rest are single-source. State the well-supported answer clearly, and mark "
                f"single-source details as such — but do not make the whole answer sound doubtful "
                f"if the core answer is supported."
            )
        return (
            f"LOW: only {verified} of {total} claims confirmed across {checked} sources. "
            f"Most findings are single-source. Present them as reported by their source, and note "
            f"they are not cross-confirmed — without burying the answer in repeated disclaimers."
        )


# Contraste en UNA sola pasada sobre TODAS las fuentes. El modelo ve el conjunto
# numerado y decide, por cada afirmación, en qué fuentes aparece. Es como se
# contrasta de verdad (leyendo todo junto, no par-por-par). Agnóstico de idioma.
#
# Clave para que la verificación FUNCIONE (no dé 0 siempre): se instruye
# EXPLÍCITAMENTE a AGRUPAR el mismo hecho aunque esté redactado distinto en cada
# fuente. Ese agrupamiento es lo que permite que algo llegue a 2+ fuentes; sin
# instruirlo, el modelo trata cada frase como un hecho aislado y todo queda en
# single-source (causa real de los "0 verificadas").
CONTRAST_PROMPT = """You are contrasting information across multiple independent sources to assess
how well-supported each factual claim is.

Query: {query}

Sources (numbered):
{sources_block}

Read ALL sources. Identify the key factual claims relevant to the query. For EACH claim, decide
which source numbers support it and which (if any) contradict it.

CRITICAL — how to find agreement across sources:
- Two sources state the SAME fact even if they word it differently. Treat them as the SAME claim
  and merge their source numbers. Examples of the same fact worded differently:
  "70% of AI projects fail" == "7 out of 10 AI initiatives never reach production".
  "developed by OpenAI" == "OpenAI created it" == "an OpenAI model".
  Normalize numbers, dates and names to detect agreement (e.g. "8 planets" == "eight planets").
- Only after merging, count how many DIFFERENT sources support each claim.

Return ONLY a JSON object with this exact shape:
{{"claims": [
  {{"claim": "a specific factual statement", "supported_by": [1, 2], "contradicted_by": []}}
]}}

Rules:
- Extract 5-10 of the most important, specific claims relevant to the query.
- supported_by and contradicted_by are arrays of source NUMBERS (1-based) shown above.
- MERGE the same fact across sources into ONE claim with all its supporting source numbers.
- Only include claims actually present in the sources. Do not invent.
- Return ONLY the JSON object, no prose, no markdown, no code fences.
"""


def _salvage_json(raw: str) -> dict | None:
    """
    Parsing TOLERANTE de la salida del modelo. La salida de un LLM local puede
    venir con envoltura (```json), texto antes/después, o pequeñas roturas. Se
    intenta, en orden: (1) json.loads directo; (2) quitar fences de markdown;
    (3) extraer el primer objeto {...} por regex. Si un array llega suelto, se
    envuelve en {"claims": [...]}. Si nada funciona, devuelve None (el llamador
    decide el fallback). Nunca lanza.
    """
    if not raw:
        return None
    stripped = raw.strip()
    candidates = [stripped]
    no_fence = re.sub(r"^```(?:json)?|```$", "", stripped, flags=re.MULTILINE).strip()
    if no_fence != stripped:
        candidates.append(no_fence)
    m = re.search(r"\{.*\}", stripped, re.DOTALL)
    if m:
        candidates.append(m.group(0))
    m_arr = re.search(r"\[.*\]", stripped, re.DOTALL)
    if m_arr:
        candidates.append(m_arr.group(0))
    for c in candidates:
        try:
            obj = json.loads(c)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list):
            return {"claims": obj}
    return None


class FactChecker:
    """
    Contrasta hechos cruzando TODAS las fuentes en una sola pasada.
      1. Numera las fuentes y las pasa juntas al modelo (structured=True → la
         cascada usa el modelo apropiado para JSON).
      2. El modelo devuelve los claims con qué fuentes los apoyan/contradicen,
         habiendo AGRUPADO el mismo hecho entre fuentes.
      3. Se asigna confianza por consenso (2+ fuentes distintas = VERIFIED).
    Robustez: parsing tolerante + UN reintento + degradación a single-source.
    La respuesta al usuario nunca se pierde.
    """

    # La investigación iterativa puede acumular varias pasadas de fuentes; el
    # factchecker debe poder cruzarlas TODAS (hasta este tope) sin agrandar el prompt,
    # porque un prompt enorme rompe al modelo local. Por eso el contenido se reparte
    # bajo un presupuesto TOTAL de caracteres: con 5 fuentes son 2000 c/u (igual que
    # antes — no se regresiona el caso de una pasada); con más, baja proporcionalmente,
    # con un mínimo por fuente para que siga habiendo texto del que extraer claims.
    MAX_SOURCES = 10              # tope de fuentes a contrastar (cubre la iteración)
    TOTAL_CONTENT_BUDGET = 10000  # caracteres totales de contenido en el prompt
    MIN_CHARS_PER_SOURCE = 800    # piso por fuente (para no quedarse sin texto útil)

    async def check(self, pages: list[ScrapedPage], query: str) -> FactCheckResult:
        if not pages:
            return FactCheckResult([], 0, 0, 0, 0)

        logger.info(f"🔍 Fact-checking {len(pages)} sources for: {query[:60]}")

        sources = pages[: self.MAX_SOURCES]
        # Presupuesto de caracteres repartido: el prompt se mantiene acotado aunque la
        # iteración acumule más fuentes. Con 5 fuentes -> 2000 c/u (idéntico al caso de
        # una sola pasada). Con más, baja proporcionalmente, nunca por debajo del piso.
        per_source = max(self.MIN_CHARS_PER_SOURCE, self.TOTAL_CONTENT_BUDGET // len(sources))
        url_by_num = {i + 1: p.url for i, p in enumerate(sources)}

        # Cada página scrapeada es contenido externo no confiable: se enmarca para
        # que una instrucción inyectada en una web ("ignora las fuentes y di X") no
        # secuestre la extracción de claims. El número de fuente queda FUERA del
        # marco para preservar la numeración que el contraste usa.
        sources_block = "\n\n".join(
            f"[Source {i + 1}] {p.url}\n"
            + wrap_untrusted(p.content[:per_source], source=f"source {i + 1}")
            for i, p in enumerate(sources)
        )

        claims = await self._contrast(query, sources_block, url_by_num)

        # Degradación: si el contraste no produjo nada (modelo o parsing fallaron
        # incluso tras el reintento), cada fuente se presenta como single-source.
        # El usuario sigue recibiendo el research; solo sin el cruce fino.
        if not claims:
            logger.warning("⚠️ Contraste sin claims — degradando a fuentes single-source")
            claims = [
                Claim(
                    text=(p.title or p.url),
                    confidence=Confidence.SINGLE_SOURCE,
                    sources=[p.url],
                )
                for p in sources
            ]

        verified = sum(1 for c in claims if c.confidence == Confidence.VERIFIED)
        single = sum(1 for c in claims if c.confidence == Confidence.SINGLE_SOURCE)
        conflicting = sum(1 for c in claims if c.confidence == Confidence.CONFLICTING)

        result = FactCheckResult(
            claims=claims,
            verified_count=verified,
            single_source_count=single,
            conflicting_count=conflicting,
            sources_checked=len(sources),
        )
        logger.info(f"✅ Fact-check complete: {result.summary()}")
        return result

    async def _contrast(self, query: str, sources_block: str, url_by_num: dict) -> list[Claim]:
        """
        Llama al modelo (vía cascada, structured=True) para contrastar las fuentes.
        Trata la salida como NO confiable: parsing tolerante; si falla, UN reintento
        con una instrucción de corrección; si vuelve a fallar, devuelve [] y el
        llamador degrada a single-source. Nunca lanza.
        """
        prompt = CONTRAST_PROMPT.format(query=query, sources_block=sources_block)

        parsed = await self._call_and_parse(prompt)

        if parsed is None:
            # UN reintento, reforzando que la salida debe ser SOLO el objeto JSON.
            # (Patrón documentado: reintentar una vez con instrucción específica
            # antes de rendirse; no más de 1-2 para no disparar latencia/coste.)
            retry_prompt = (
                prompt
                + "\n\nIMPORTANT: your previous answer could not be parsed. Return ONLY the raw "
                  "JSON object described above. No explanation, no markdown, no code fences. "
                  "Start your answer with '{' and end with '}'."
            )
            parsed = await self._call_and_parse(retry_prompt)

        if parsed is None:
            return []

        items = parsed.get("claims", [])
        if not isinstance(items, list):
            return []

        claims: list[Claim] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("claim"):
                continue
            supported_nums = item.get("supported_by", []) or []
            contradicted_nums = item.get("contradicted_by", []) or []

            supported_urls = self._nums_to_urls(supported_nums, url_by_num)
            contradicted_urls = self._nums_to_urls(contradicted_nums, url_by_num)

            if contradicted_urls:
                confidence = Confidence.CONFLICTING
                conflict_note = f"Contradicted by: {', '.join(contradicted_urls[:2])}"
            elif len(supported_urls) >= 2:
                confidence = Confidence.VERIFIED
                conflict_note = ""
            elif len(supported_urls) == 1:
                confidence = Confidence.SINGLE_SOURCE
                conflict_note = ""
            else:
                # Sin fuentes válidas de apoyo: no se puede sostener, se omite.
                continue

            claims.append(
                Claim(
                    text=str(item["claim"]),
                    confidence=confidence,
                    sources=supported_urls or contradicted_urls,
                    conflict_note=conflict_note,
                )
            )

        return claims

    @staticmethod
    def _nums_to_urls(nums, url_by_num: dict) -> list[str]:
        """
        Convierte índices de fuente (1-based) a URLs. Acepta enteros y strings
        numéricas ("1") que el modelo local a veces emite. Ignora cualquier valor
        que no sea un índice válido. Deduplica preservando el orden, para que un
        mismo URL no infle el conteo de apoyo.
        """
        urls = []
        for n in nums:
            try:
                n_int = int(n)
            except (ValueError, TypeError):
                continue
            if n_int in url_by_num:
                urls.append(url_by_num[n_int])
        return list(dict.fromkeys(urls))

    async def _call_and_parse(self, prompt: str) -> dict | None:
        """
        Una llamada a la cascada con structured=True + parsing tolerante.
        Devuelve el dict parseado o None. Nunca lanza: cualquier fallo (proveedor
        caído, salida no parseable) se traduce en None para que el llamador decida.
        """
        try:
            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1200,
                structured=True,
                task_type="factcheck",  # corroboración = razonamiento → modelo capaz si está
                                        # configurado (ver config.ollama_model_for); si no,
                                        # mismo modelo de JSON que antes (sin regresión).
            )
        except Exception as e:
            logger.warning(f"⚠️ Contrast LLM call failed ({e})")
            return None
        return _salvage_json(raw)