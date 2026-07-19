"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agent/tools/research/engine.py — Motor de investigación profunda
Orquesta: Tavily → Scraper → FactChecker → Síntesis con confianza verificada.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from loguru import logger
from tavily import TavilyClient
from clawlite.agent.tools.research.scraper import DeepScraper
from clawlite.agent.tools.research.factchecker import FactChecker, Confidence, FactCheckResult
from clawlite.llm.client import llm
from clawlite.llm.json_parser import extract_json
from clawlite.security.injection_detector import verify_scraped_page
from clawlite.personality.voice import ClawPersonality
from clawlite.personality.catalog import msg as catalog_msg
from clawlite.config import config
from clawlite.governance import action_guard, Mandate, MandateOrigin
import asyncio


MAX_URLS_TO_SCRAPE = 5

# Comparaciones (query_type="comparison"): máximo de entidades independientes
# que se descomponen en búsquedas paralelas. Por encima de este número el pool
# por entidad sería demasiado delgado para ser útil; se mantiene el pool
# compartido (con el filtro OR ya existente) en vez de fragmentar más.
COMPARISON_MAX_ENTITIES = 4
# Techo TOTAL de URLs a scrapear, repartido entre entidades — el scraper no
# debe recibir más páginas de las que puede digerir en una pasada.
COMPARISON_URL_BUDGET = 12

# Confirmación iterativa de research. Si tras cruzar fuentes el nº de afirmaciones
# verificadas (corroboradas por 2+ fuentes) no llega al piso, se scrapea otro lote y
# se vuelve a cruzar sobre TODAS las fuentes acumuladas, hasta el piso, agotar URLs o
# el tope de pasadas. NUNCA infla la verificación: solo añade fuentes reales; si no
# corroboran (típico en noticias inconexas), el número no sube y se reporta honesto.
# Acotado por código (sin bucles ni coste libre). La síntesis corre UNA vez al final.
RESEARCH_VERIFIED_FLOOR = 3      # afirmaciones verificadas que se consideran "suficiente"
RESEARCH_MAX_PASSES = 2          # 1 inicial + 1 extra (cada pasada extra ≈ +10-15s síncronos)
# URLs que Tavily trae de una vez, suficientes para alimentar las pasadas sin re-consultar.
URL_POOL_SIZE = MAX_URLS_TO_SCRAPE * RESEARCH_MAX_PASSES + 2

# Mensajes de borde de research (sin fuentes / sin acceso / sin corroboración /
# síntesis caída): deterministas, esquivan la síntesis por diseño. Viven en el
# catálogo multilingüe (personality/catalog.py, victoria #6 fase 1); el idioma
# lo fija la red determinista del turno (ContextVar), nunca el modelo. La deuda
# "hardcodeado en español" de estos early-returns queda cerrada ahí. El de
# síntesis caída sigue garantizando que NUNCA se filtra contenido crudo de
# páginas al usuario (guardia permanente en tests).

# Cabeceras de sección de la entrada de síntesis (victoria D2-5). Son andamiaje
# INTERNO NUESTRO: constantes exactas para que la red anti-fuga pueda borrarlas
# deterministamente si el modelo alguna vez las reprodujera literales. La
# confianza viaja como ESTRUCTURA (posición en el grupo), no como tag inline
# dentro de la frase — la raíz de la fuga de etiquetas copiadas/traducidas.
_HDR_VERIFIED = "FINDINGS CORROBORATED BY MULTIPLE INDEPENDENT SOURCES (state these as confirmed facts):"
_HDR_SINGLE = "FINDINGS REPORTED BY ONLY ONE SOURCE (attribute them; never present as established facts):"
_HDR_CONFLICT = "FINDINGS WHERE SOURCES DISAGREE (present the differing views):"
_HDR_UNCONFIRMED = "UNCONFIRMED FINDINGS (no verification; use with maximum caution or omit):"

SYNTHESIS_PROMPT = """You are a strict research synthesizer. Answer the user's query DIRECTLY and ONLY using the Findings provided below.

LANGUAGE RULE:
Respond in the exact same language as the user's query. Ignore the language of the sources.

User query: {query}

CRITICAL GROUNDING RULES:
1. You MUST NOT use your pre-trained knowledge. If a detail is not explicitly written in the Findings below, DO NOT include it.
2. If the Findings do not contain the answer to the user's query, state that the sources do not provide the information. DO NOT invent an answer.
3. If the user's query names specific items to compare (e.g. "compare A, B, C and D"), answer ONLY about those named items — even if a source also discusses other, unrequested items alongside them. Do not introduce items the user did not ask about.

The findings below are GROUPED by how well independent sources support them. Each
group's header tells you how to treat its findings. The headers are INTERNAL
machinery — never reproduce or translate them in your answer. Convey each finding's
level of support in natural prose, in the user's language, in your own words
(whether several sources back it, only one reports it, or sources disagree).

Findings:
{key_information}

Sources consulted: {sources}

Overall confidence guidance for this answer:
{calibration}

Instructions:
- Lead with the answer, built on the corroborated findings first.
- Make clear IN THE PROSE which parts are well-supported and which rest on a single source or are disputed — do not present single-source or conflicting claims as confirmed facts.
- Weave this in naturally; be direct and concise, and do not bury the answer under repeated disclaimers.
- {language_rule}
"""

# Filtro de relevancia temática (código, no juicio del modelo — ver
# _extract_subject_terms / _filter_relevant_pages más abajo). El modelo SOLO
# extrae términos que ya están en la query (tarea puramente extractiva); la
# decisión de qué página se descarta la toma el código por substring match.
SUBJECT_TERMS_PROMPT = """Identify the 1-5 DISTINCT named subjects of this query — the specific
place(s), organization(s), person(s), product(s), or topic(s) it is about. These are things
already present in the query itself; do not invent or add anything new.
{previous_context}
Group them: each DISTINCT subject is its own group. Within a group, list alternate phrasings of
THAT SAME subject — ALWAYS include the exact phrasing as it appears in the query itself, and if
the query is not in English, ALSO add the standard international/English form as a separate
alternate (sources found online are frequently in English regardless of the query's language, and
a place, concept or name is often written differently there). Never drop or replace the original
phrasing — a group must contain BOTH forms when the query is not in English, not just one. Do NOT
merge two DIFFERENT subjects into the same group, and do not include generic words about the
search itself (news, latest, search, investigate, information) as if they were a subject.

Also classify the query itself (not the subjects) as exactly one of:
- "single_topic": the subjects together describe ONE specific thing (e.g. a place within a city,
  an event tied to an organization). A relevant source should cover them TOGETHER.
- "comparison": the subjects are independent, parallel options being compared, listed, or chosen
  among (e.g. several companies, products, teams). A relevant source may cover just ONE of them.

Query: {query}

Return ONLY JSON: {{"terms": [["subject1", "subject1 alt form"], ["subject2"]], "query_type": "single_topic"|"comparison"}}
If the query has no single clear named subject, return {{"terms": [], "query_type": "single_topic"}}.
"""


@dataclass
class ResearchResult:
    answer: str
    sources: list[str]
    verified_claims: int
    total_claims: int
    sources_checked: int
    synthesis_failed: bool = False
    # True cuando answer es un MENSAJE DE BORDE (sin fuentes / sin acceso /
    # rechazo sin corroboración): la síntesis nunca corrió, así que el sello
    # "Verified research" (footer) y el merge del synthesizer no aplican.
    # Bandera estructural emitida en el origen — mismo patrón que
    # synthesis_failed, nunca comparación de texto (Regla 12).
    edge_message: bool = False


_CONFIDENCE_TAG_RE = re.compile(
    r"\[?\s*(?:VERIFIED(?:\s*[·\-]\s*\d+\s*sources?)?|SINGLE[\s\-]?SOURCE|CONFLICTING|UNVERIFIED)\s*\]?"
)

# Firma ESTRUCTURAL de la etiqueta TRADUCIDA: palabra que no empieza en minúscula
# + punto medio (·) + número + palabra opcional. El "· N" es la firma de NUESTRO
# formato interno ("[VERIFIED · 2 sources]") — el modelo débil a veces TRADUCE la
# etiqueta completa ("VERIFICADO · 2 fuentes", "已验证 · 2 来源") y la regex
# inglesa no la ve. Anclada en la ESTRUCTURA, no en el idioma: casa la forma
# traducida a cualquier lengua sin lista de palabras. El punto medio seguido de
# dígito no existe en prosa natural (el catalán "l·l" no lleva dígito; nuestro
# footer usa "•", otro carácter). Formas traducidas SIN esta firma ("FUENTE
# ÚNICA", "CONFLICTO entre las fuentes") NO son atrapables por ninguna red
# determinista sin listas de palabras — su eliminación de raíz es el rediseño
# de la entrada de síntesis (victoria #5: sin tags inline, nada que traducir).
_TRANSLATED_TAG_RE = re.compile(
    r"\[?\s*[^\W\da-z_]\w*(?:\s+\w+){0,2}\s*·\s*\d+\s*\w*\s*\]?", re.UNICODE
)


def _strip_confidence_tags(text: str) -> str:
    """Red de seguridad determinista: el modelo local a veces COPIA las etiquetas
    internas ([VERIFIED · N sources], [SINGLE SOURCE], [CONFLICTING]) literalmente en
    la respuesta en vez de expresarlas en prosa. Esas etiquetas son maquinaria interna
    y NO deben llegar al usuario. El prompt ya pide no copiarlas; esto garantiza que,
    si se cuelan igual, se eliminen. Limpia los espacios/puntuación que deja el borrado."""
    if not text:
        return text
    # Cabeceras de sección (andamiaje nuestro, constantes exactas): si el modelo
    # las reprodujera literales, se borran antes que nada. Match determinista.
    for hdr in (_HDR_VERIFIED, _HDR_SINGLE, _HDR_CONFLICT, _HDR_UNCONFIRMED):
        text = text.replace(hdr, "")
    cleaned = _CONFIDENCE_TAG_RE.sub("", text)
    cleaned = _TRANSLATED_TAG_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    return cleaned.strip()


def _fold_accents(text: str) -> str:
    """Quita acentos/diacríticos (México -> Mexico, café -> cafe, São -> Sao) para
    que el filtro de relevancia no trate la misma palabra como distinta solo
    porque una fuente la escribe sin tilde. Normalización Unicode universal —
    aplica por igual a cualquier idioma con diacríticos, no es una lista de
    palabras. No afecta idiomas sin alfabeto latino (chino, árabe): esos ya se
    resuelven pidiendo la forma internacional/inglesa del término (ver
    SUBJECT_TERMS_PROMPT), no por esta normalización."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


class ResearchEngine:
    """
    Pipeline completo de investigación profunda:
    1. Tavily encuentra las URLs más relevantes
    2. Scraper visita cada URL en paralelo y extrae contenido completo
    3. FactChecker cruza información entre fuentes
    4. LLM sintetiza con nivel de confianza explícito por afirmación
    """

    def __init__(self):
        self.tavily = TavilyClient(api_key=config.TAVILY_API_KEY)
        self.scraper = DeepScraper()
        self.fact_checker = FactChecker()

    async def research(
        self,
        query: str,
        is_news: bool = False,
        term_groups: list[list[str]] | None = None,
        query_type: str | None = None,
        user_id: str | None = None,
    ) -> ResearchResult:
        logger.info(f"🔬 Research started: {query[:80]}")

        # Si el llamador (core.py, con memoria conversacional para resolver
        # pronombres) ya calculó term_groups/query_type, se reutilizan tal
        # cual — evita una segunda llamada al modelo que puede discrepar de
        # la primera (causa real de la inestabilidad observada: 1 grupo vs 2
        # grupos para la misma query en el mismo turno). Sin llamador que los
        # provea (MCP, jobs asíncronos), se calculan aquí igual que siempre —
        # cero cambio de comportamiento para esos caminos.
        if term_groups is None or query_type is None:
            subject_terms, query_type = await self._extract_subject_terms(query)
        else:
            subject_terms = term_groups

        # 1. Tavily — pool de URLs. Noticias: cascada de ventana temporal
        # (7 → 30 días → general) decidida por páginas RELEVANTES tras
        # scrape+filtro, no por el conteo crudo de Tavily (ver
        # _search_news_with_cascade — causa raíz real de que la cascada nunca
        # se disparara). No-noticias: comparaciones con 2-4 entidades usan una
        # búsqueda por entidad en paralelo; todo lo demás, una sola búsqueda
        # compartida (comportamiento de siempre, sin cambios).
        is_comparison = query_type == "comparison" and 2 <= len(subject_terms) <= COMPARISON_MAX_ENTITIES
        use_news_mode = is_news and not self._mentions_past_year(query)

        if use_news_mode:
            urls, pages = await self._search_news_with_cascade(query, subject_terms, query_type, is_comparison, user_id=user_id)
        else:
            # use_news_mode (no is_news crudo): el guard _mentions_past_year ya
            # decidió que esta consulta NO es de actualidad -- pasar el is_news
            # sin gatear aquí reactivaba topic="news" en Tavily por otra vía,
            # anulando en la práctica el propio guard (causa raíz real del bug
            # "mundial 2022" devolviendo resultados de actualidad irrelevantes).
            if is_comparison:
                urls = await self._search_urls_decomposed(query, subject_terms, use_news_mode, user_id=user_id)
            else:
                urls = await self._search_urls(query, is_news=use_news_mode, user_id=user_id)
            pages = []

        if not urls:
            return ResearchResult(
                answer=catalog_msg("research_no_sources"),
                sources=[],
                verified_claims=0,
                total_claims=0,
                sources_checked=0,
                edge_message=True,
            )

        # 2-3. Confirmación iterativa: scrape por LOTES + factcheck sobre TODAS las
        # fuentes acumuladas, hasta alcanzar el piso de verificación, agotar URLs o
        # llegar al tope de pasadas. En noticias NO se itera (fuentes inconexas no
        # corroboran) — el scrape+filtro de la ventana ganadora YA se hizo dentro
        # de _search_news_with_cascade, así que aquí solo se factchequea. El
        # criterio de parada es un número (verified_count), no un juicio del
        # modelo. Nunca infla: solo añade fuentes reales.
        fact_result = None
        if use_news_mode:
            if pages:
                fact_result = await self.fact_checker.check(pages, query)
        else:
            used = 0
            for pass_num in range(1, RESEARCH_MAX_PASSES + 1):
                batch = urls[used:used + MAX_URLS_TO_SCRAPE]
                if not batch:
                    break  # no quedan URLs nuevas que probar
                used += len(batch)

                new_pages = await self.scraper.scrape_many(batch)
                # Filtro determinista de relevancia temática: descarta ANTES de que
                # lleguen al FactChecker las páginas que no tienen relación con el tema
                # (el código decide por substring, no el modelo por corroboración).
                new_pages = self._filter_relevant_pages(new_pages, subject_terms, query_type)

                # Filtro de seguridad: prompt-injection. Fase propia, después de
                # relevancia y antes del fact-check — descarta SOLO la página
                # afectada, nunca bloquea toda la investigación.
                new_pages = await self._filter_injection_safe_pages(new_pages)

                pages.extend(new_pages)
                if not pages:
                    continue  # este lote no bajó contenido relevante; probar el siguiente

                fact_result = await self.fact_checker.check(pages, query)
                if fact_result.verified_count >= RESEARCH_VERIFIED_FLOOR:
                    logger.info(
                        f"🔬 Piso de verificación alcanzado "
                        f"({fact_result.verified_count}≥{RESEARCH_VERIFIED_FLOOR}) "
                        f"en pasada {pass_num}/{RESEARCH_MAX_PASSES}"
                    )
                    break
                if pass_num < RESEARCH_MAX_PASSES and used < len(urls):
                    logger.info(
                        f"🔬 Verificadas {fact_result.verified_count}<{RESEARCH_VERIFIED_FLOOR}; "
                        f"buscando más fuentes (pasada {pass_num + 1})"
                    )

        if not pages:
            return ResearchResult(
                answer=catalog_msg("research_sources_inaccessible"),
                sources=urls,
                verified_claims=0,
                total_claims=0,
                sources_checked=0,
                edge_message=True,
            )

        # Garantía defensiva: si por el orden de lotes vacíos el factcheck no llegó a
        # correr pero sí hay páginas, se cruza ahora (no debería ocurrir, pero el
        # contrato exige fact_result no nulo antes de sintetizar).
        if fact_result is None:
            fact_result = await self.fact_checker.check(pages, query)

        # Guarda anti-fabricación: NO sintetizar cuando no hubo NINGUNA afirmación
        # corroborada (verified_count == 0) Y el pool de fuentes útiles fue demasiado
        # delgado para que el cruce fuera siquiera posible (menos fuentes que el piso
        # de verificación). Sintetizar sobre 0 verificadas + fuentes escasas/off-topic
        # es lo que deja al modelo hilar un relato coherente pero inventado.
        if fact_result.verified_count == 0 and fact_result.sources_checked < RESEARCH_VERIFIED_FLOOR:
            return ResearchResult(
                answer=catalog_msg("research_insufficient_verification"),
                sources=[p.url for p in pages],
                verified_claims=0,
                total_claims=len(fact_result.claims),
                sources_checked=fact_result.sources_checked,
                edge_message=True,
            )

        # 4. Síntesis con LLM — UNA sola vez, sobre el resultado final
        answer, synthesis_failed = await self._synthesize(query, fact_result, pages)

        return ResearchResult(
            answer=answer,
            sources=[p.url for p in pages],
            verified_claims=fact_result.verified_count,
            total_claims=len(fact_result.claims),
            sources_checked=fact_result.sources_checked,
            synthesis_failed=synthesis_failed,
        )

    # Un año de 4 dígitos anterior al actual es señal universal (no depende del
    # idioma) de que la consulta pregunta por un HECHO PASADO, no por algo
    # ocurriendo ahora. topic="news" + days=7 NUNCA encontraría la respuesta
    # correcta a un hecho de hace meses/años, sin importar qué tan bueno sea el
    # resto del pipeline — causa raíz real, no del filtro de relevancia ni de la
    # síntesis. Código puro, sin LLM.
    _YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

    @classmethod
    def _mentions_past_year(cls, query: str) -> bool:
        current_year = datetime.now().year
        years = [int(y) for y in cls._YEAR_RE.findall(query)]
        return any(y < current_year for y in years)

    async def _search_urls(
        self, query: str, is_news: bool = False, max_results: int | None = None,
        days: int | None = None, user_id: str | None = None,
    ) -> list[str]:
        """
        Usa Tavily para encontrar las mejores URLs. is_news lo decide el planner
        SEMÁNTICAMENTE (agnóstico de idioma), no una lista de palabras. Cuando es
        una consulta de actualidad, se usa topic='news' (motor de noticias de
        Tavily: artículos recientes con fecha) en vez de la búsqueda general, que
        para noticias devolvía páginas de tags.

        days: ventana temporal en días para topic="news" (7 o 30), o None para
        dejar que Tavily use su default / para búsqueda general sin fecha. La
        cascada 7→30→general YA NO vive aquí — la decide _search_news_with_cascade
        en research(), que puede medir si las URLs devueltas producen páginas
        RELEVANTES tras scrape+filtro. Un conteo de URLs crudo (lo único que este
        método podía ver) casi nunca es cero aunque el tema no encaje — causa raíz
        real de que days=30 nunca se llegara a intentar.

        max_results: techo de URLs para ESTA llamada. Por defecto URL_POOL_SIZE
        (comportamiento normal); _search_urls_decomposed lo reduce por entidad en
        comparaciones, para no exceder COMPARISON_URL_BUDGET páginas totales.
        """
        # Mediación por el kernel (auditoría + contrato): el research saca la query a
        # Tavily (egress). Se audita como web_search. user_id real cuando el llamador
        # lo provee (hilvanado desde research() → ResearchAgent.run()/core.py);
        # "local" solo como fallback si ninguno lo pasa.
        decision = action_guard.authorize(
            "web_search",
            Mandate(origin=MandateOrigin.USER_DIRECT, user_id=user_id or "local", summary=(query or "")[:120]),
        )
        if not decision.allowed:
            logger.warning(f"🛡️ Búsqueda web (research) DENEGADA por el kernel: {decision.reason}")
            return []

        pool_size = max_results or URL_POOL_SIZE

        try:
            if is_news:
                kwargs = {"topic": "news"}
                if days is not None:
                    kwargs["days"] = days
                results = self.tavily.search(
                    query=query, max_results=pool_size, search_depth="advanced", **kwargs
                )
            else:
                results = self.tavily.search(
                    query=query, max_results=pool_size, search_depth="advanced",
                )
            urls = [r["url"] for r in results.get("results", [])]
            logger.info(
                f"🔍 Tavily found {len(urls)} URLs"
                + (f" (news, days={days})" if is_news else "")
            )
            return urls
        except Exception as e:
            logger.error(f"❌ Tavily search failed: {e}")
            return []

    async def _search_urls_decomposed(
        self, query: str, subject_terms: list[list[str]], is_news: bool, days: int | None = None,
        user_id: str | None = None,
    ) -> list[str]:
        """
        Multi-hop retrieval para comparaciones: en vez de UNA búsqueda compartida
        entre todas las entidades (causa real de cobertura pobre por entidad — un
        club de 5 con 74 palabras de una sola fuente), se lanza UNA búsqueda POR
        ENTIDAD, en paralelo (asyncio.gather, sin penalizar latencia), acotando
        cuántas URLs pide cada una para no exceder COMPARISON_URL_BUDGET páginas
        totales. La sub-query es concatenación de código (query original + término
        primario de la entidad) — cero llamadas nuevas al modelo.
        """
        per_entity_cap = max(2, COMPARISON_URL_BUDGET // len(subject_terms))
        sub_queries = [f"{query} {group[0]}" for group in subject_terms]
        results = await asyncio.gather(
            *(self._search_urls(sq, is_news=is_news, max_results=per_entity_cap, days=days, user_id=user_id) for sq in sub_queries),
            return_exceptions=True,
        )
        urls: list[str] = []
        seen = set()
        for entity_urls in results:
            if isinstance(entity_urls, Exception):
                logger.warning(f"⚠️ Búsqueda por entidad falló (se ignora, sigue el resto): {entity_urls}")
                continue
            for u in entity_urls:
                if u not in seen:
                    seen.add(u)
                    urls.append(u)
        logger.info(f"🔍 Búsqueda descompuesta: {len(subject_terms)} entidades → {len(urls)} URLs únicas")
        return urls

    async def _search_news_with_cascade(
        self, query: str, subject_terms: list[list[str]], query_type: str, is_comparison: bool,
        user_id: str | None = None,
    ) -> tuple[list[str], list]:
        """
        Cascada de ventana temporal para noticias (7 → 30 días → general),
        decidida por páginas RELEVANTES tras scrape+filtro, no por el conteo
        crudo de URLs de Tavily — ese conteo casi nunca es cero aunque el tema
        no encaje, así que con el criterio anterior days=30 nunca se llegaba a
        intentar (causa raíz real, confirmada en log: days=7 con 12 URLs fuera
        de tema). Devuelve (urls, pages) de la primera ventana que produjo al
        menos una página relevante; si ninguna lo hizo, el último intento es
        búsqueda general sin acotar fecha, devuelto tal cual para que
        research() aplique su propio mensaje de "sin resultados".
        """
        urls: list[str] = []
        pages = []
        for days in (7, 30):
            candidate_urls = (
                await self._search_urls_decomposed(query, subject_terms, is_news=True, days=days, user_id=user_id)
                if is_comparison
                else await self._search_urls(query, is_news=True, days=days, user_id=user_id)
            )
            urls = candidate_urls
            if not candidate_urls:
                logger.info(f"🔍 Tavily news vacío con days={days}")
                continue
            pages = self._filter_relevant_pages(
                await self.scraper.scrape_many(candidate_urls[:MAX_URLS_TO_SCRAPE]),
                subject_terms, query_type,
            )
            # Filtro de seguridad: prompt-injection. Misma fase que el camino
            # no-noticias — ver _filter_injection_safe_pages.
            pages = await self._filter_injection_safe_pages(pages)
            if pages:
                logger.info(f"🔍 Tavily (news, days={days}): {len(pages)} páginas relevantes")
                return urls, pages
            logger.info(f"🔍 days={days}: {len(candidate_urls)} URLs pero 0 relevantes tras filtrar")

        logger.info("🔍 Tavily news sin páginas relevantes en 7 ni 30 días — cayendo a búsqueda general")
        urls = (
            await self._search_urls_decomposed(query, subject_terms, is_news=False, user_id=user_id)
            if is_comparison
            else await self._search_urls(query, is_news=False, user_id=user_id)
        )
        if urls:
            pages = self._filter_relevant_pages(
                await self.scraper.scrape_many(urls[:MAX_URLS_TO_SCRAPE]), subject_terms, query_type,
            )
            pages = await self._filter_injection_safe_pages(pages)
        return urls, pages

    async def _extract_subject_terms(
        self, query: str, previous_query: str = ""
    ) -> tuple[list[list[str]], str]:
        """
        Extrae los GRUPOS de sujetos del TEMA de la query, más una clasificación de
        la query ("single_topic" vs "comparison") que decide si el filtro exige
        TODOS los sujetos en una página (AND) o le basta con UNO (OR). El modelo NO
        decide la operación booleana — solo clasifica la naturaleza de la query,
        extractivo igual que el resto de esta función; el CÓDIGO en
        _filter_relevant_pages traduce esa clasificación a AND/OR. Sin esta
        distinción, un AND fijo rompe comparaciones legítimas (N clubes de fútbol,
        "OpenClaw vs tus capacidades") porque ninguna fuente real menciona a la vez
        a todas las alternativas — causa raíz confirmada en pantalla el 4-5 jul.
        Fail-safe: ante fallo, lista vacía, o campo ausente/inválido →
        "single_topic" (el comportamiento AND ya validado, cero regresión en los
        casos que ya funcionan — Ecuador, Arthur's Seat).

        previous_query: turno anterior de la conversación (ej. desde last_research
        en core.py), si lo hay. Se usa SOLO para que el modelo resuelva pronombres
        ("estos clubes", "esa compañía") sobre la query actual — sigue siendo
        extracción del contenido de ESTA query, no un juicio nuevo sobre otra cosa.
        Causa raíz real: sin esto, una pregunta de seguimiento sin nombrar las
        entidades otra vez producía grupos de conceptos genéricos en vez de las
        entidades reales. Fail-safe: cadena vacía → mismo comportamiento de hoy.
        """
        previous_context = (
            "\nNote: the query may use a pronoun (\"these\", \"that one\") referring "
            "back to the previous question below. Use it ONLY to resolve what the "
            "pronoun refers to — extract subjects from the CURRENT query, not the "
            f"previous one.\nPrevious question: {previous_query}\n"
            if previous_query else ""
        )
        try:
            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": SUBJECT_TERMS_PROMPT.format(
                    query=query, previous_context=previous_context
                )}],
                max_tokens=150,
                structured=True,
                task_type="factcheck",
            )
            data = extract_json(raw, expect="object")
            logger.debug(f"🔍 subject_terms extraction raw: {data}")
            if not data:
                return [], "single_topic"
            query_type = data.get("query_type")
            if query_type not in ("single_topic", "comparison"):
                query_type = "single_topic"
            raw_terms = data.get("terms", []) or []
            if not raw_terms:
                return [], query_type
            if all(isinstance(t, str) for t in raw_terms):
                cleaned = [str(t).strip() for t in raw_terms if str(t).strip()]
                return ([cleaned] if cleaned else []), query_type
            groups = []
            for group in raw_terms:
                if isinstance(group, str):
                    group = [group]
                cleaned = [str(t).strip() for t in group if str(t).strip()]
                if cleaned:
                    groups.append(cleaned)
            return groups, query_type
        except Exception as e:
            logger.debug(f"Extracción de términos de tema falló (sin filtro, fail-safe): {e}")
            return [], "single_topic"

    # Longitud mínima de un token para contar como "palabra significativa". Es un
    # umbral universal (no una lista de palabras por idioma): descarta conectores
    # cortos (de, el, in, on, la...) en cualquier idioma latino sin necesidad de
    # nombrarlos uno por uno; números y nombres propios cortos (ej. "2022") siguen
    # contando porque ya tienen 4 caracteres.
    _MIN_TOKEN_LEN = 3

    @staticmethod
    def _significant_tokens(text: str) -> set:
        """Tokeniza en palabras significativas: minúsculas, sin acentos/diacríticos,
        mínimo _MIN_TOKEN_LEN caracteres. \\w+ ya es agnóstico de idioma en Python 3
        (incluye caracteres con tilde, cirílico, árabe, etc.) — no hace falta
        segmentar por idioma."""
        folded = _fold_accents(text.lower())
        return {t for t in re.findall(r"\w+", folded) if len(t) >= ResearchEngine._MIN_TOKEN_LEN}

    # Longitud del "stem" universal: comparamos por PREFIJO del token en vez de
    # string exacto, para absorber variación morfológica (plural/singular,
    # sufijos de género/conjugación) sin listas de palabras por idioma — mismo
    # principio que _fold_accents (normalización universal, no reglas por
    # idioma). Causa raíz real que resuelve: "regulations"(en) vs "regulation"
    # en una fuente real, "europeas"(es) vs "europea"/"Europe" en otra — el
    # match por string COMPLETO era frágil ante variación léxica normal, aunque
    # el sujeto extraído fuera correcto. No es perfecto (no absorbe sinónimos
    # de raíz distinta, ni divergencias tipo "Europa" vs "europeas" que caen a
    # ambos lados de los 6 caracteres) — sustancialmente más tolerante que la
    # comparación exacta, sin tocar el AND/OR entre sujetos.
    _STEM_LEN = 6

    @staticmethod
    def _stem(token: str) -> str:
        return token[:ResearchEngine._STEM_LEN]

    @staticmethod
    def _group_matches_page(page_tokens: set, group: list[str]) -> bool:
        """Un grupo es UN sujeto con posibles formas alternas (sinónimos/
        traducciones). Coincide si la página contiene TODAS las palabras
        significativas de AL MENOS UNA forma completa — no basta que aparezca
        una sola palabra suelta de una frase de varias palabras. Causa raíz real
        que resuelve: "Edinburgh Airport" se partía en {"edinburgh","airport"} y
        cualquier página con la palabra genérica "airport" (aeropuerto de JFK,
        de Israel, etc.) pasaba el filtro sin ser sobre Edimburgo. AND dentro de
        una forma, OR entre formas alternas del mismo grupo."""
        page_stems = {ResearchEngine._stem(t) for t in page_tokens}
        for phrasing in group:
            phrasing_tokens = ResearchEngine._significant_tokens(phrasing)
            phrasing_stems = {ResearchEngine._stem(t) for t in phrasing_tokens}
            if phrasing_stems and phrasing_stems.issubset(page_stems):
                return True
        return False

    @staticmethod
    def _filter_relevant_pages(
        pages: list, term_groups: list[list[str]], query_type: str = "single_topic"
    ) -> list:
        """
        Filtro DETERMINISTA: cada grupo es UN sujeto distinto de la query. Una
        página coincide con un grupo si contiene TODAS las palabras de al menos
        UNA forma alterna completa de ese grupo (ver _group_matches_page) — no
        basta una palabra suelta de una frase de varias palabras.

        Entre sujetos DISTINTOS, la operación depende de query_type:
        - "single_topic" (default, fail-safe): AND — la página debe tocar TODOS los
          sujetos. Causa raíz real que resuelve: una consulta compuesta (ej. un
          lugar específico dentro de una ciudad) pasaba con que la página solo
          tocara la ciudad, aunque no fuera sobre el lugar preguntado.
        - "comparison": OR — basta con que la página toque AL MENOS UN sujeto.
          Causa raíz real que resuelve: en "compara estos 5 equipos" o "OpenClaw
          vs tus capacidades" ninguna fuente real habla de todas las alternativas
          a la vez; exigir AND ahí garantizaba 0 fuentes siempre.

        Sigue siendo substring/set determinista, sin embeddings ni llamadas al
        modelo por página. Sin grupos (query sin sujeto claro) → no filtra.
        """
        if not term_groups:
            logger.debug(f"🔍 filter bypass: term_groups vacío de origen, {len(pages)} páginas sin filtrar")
            return pages
        term_groups = [
            g for g in term_groups
            if any(ResearchEngine._significant_tokens(t) for t in g)
        ]
        if not term_groups:
            logger.debug(f"🔍 filter bypass: grupos sin tokens significativos tras poda, {len(pages)} páginas sin filtrar")
            return pages
        combine = any if query_type == "comparison" else all
        result = [
            p for p in pages
            if combine(
                ResearchEngine._group_matches_page(
                    ResearchEngine._significant_tokens(f"{p.title} {p.content}"), group
                )
                for group in term_groups
            )
        ]
        logger.debug(
            f"🔍 filter: grupos={term_groups!r} modo={'OR' if combine is any else 'AND'} "
            f"→ {len(result)}/{len(pages)} páginas sobreviven"
        )
        return result

    async def _filter_injection_safe_pages(self, pages: list) -> list:
        """Fase de seguridad del pipeline: excluye páginas cuyo contenido
        scrapeado falla el detector de prompt-injection (verify_scraped_page,
        security/injection_detector.py — mismo motor de decisión que el
        flujo de email, con su propio template). Fail-closed POR PÁGINA:
        cualquier duda descarta esa fuente puntual, nunca bloquea toda la
        investigación."""
        safe_pages = []
        for p in pages:
            if await verify_scraped_page(p.content):
                safe_pages.append(p)
            else:
                logger.warning(f"🛡️ Página descartada por posible prompt-injection: {p.url}")
        return safe_pages

    async def _synthesize(self, query: str, fact_result, pages) -> tuple[str, bool]:
        """Genera la respuesta final marcando el nivel de confianza POR afirmación,
        para que el CUERPO —no solo el footer— distinga verificado de fuente única.
        Antes se pasaba solo f.text y el prompt pedía una distinción imposible: el
        dato de confianza nunca llegaba al modelo."""
        facts_list = fact_result.claims if hasattr(fact_result, "claims") else []

        if facts_list:
            # Claims agrupados por SECCIÓN de confianza — sin tags inline. La raíz
            # de la fuga de etiquetas (incl. TRADUCIDAS, imposibles de atrapar sin
            # listas de palabras): el modelo reescribía cada línea y el tag viajaba
            # DENTRO de la frase que parafraseaba, así que lo copiaba o traducía.
            # Una cabecera de grupo es estructura posicional: no está pegada al
            # texto que el modelo transforma. Secciones vacías se OMITEN (mismo
            # principio que la estructura emoji del brief: sin texto de estado
            # vacío). UNVERIFIABLE y cualquier valor futuro caen a 'unconfirmed'.
            buckets = {
                Confidence.VERIFIED: [], Confidence.SINGLE_SOURCE: [],
                Confidence.CONFLICTING: [],
            }
            unconfirmed = []
            for f in facts_list:
                buckets.get(f.confidence, unconfirmed).append(f.text)

            sections = []
            if buckets[Confidence.VERIFIED]:
                sections.append(_HDR_VERIFIED + "\n" +
                                "\n".join(f"• {t}" for t in buckets[Confidence.VERIFIED]))
            if buckets[Confidence.SINGLE_SOURCE]:
                sections.append(_HDR_SINGLE + "\n" +
                                "\n".join(f"• {t}" for t in buckets[Confidence.SINGLE_SOURCE]))
            if buckets[Confidence.CONFLICTING]:
                sections.append(_HDR_CONFLICT + "\n" +
                                "\n".join(f"• {t}" for t in buckets[Confidence.CONFLICTING]))
            if unconfirmed:
                sections.append(_HDR_UNCONFIRMED + "\n" +
                                "\n".join(f"• {t}" for t in unconfirmed))
            key_information = "\n\n".join(sections)
            calibration = FactCheckResult.calibration_note(
                fact_result.verified_count, len(facts_list), fact_result.sources_checked
            )
        else:
            # Fallback: contenido de páginas cuando el FactChecker no produjo claims.
            key_information = "\n\n".join(
                f"[Fuente {i+1}] {p.url}\n{p.content[:1200]}"
                for i, p in enumerate(pages[:3])
            )
            calibration = FactCheckResult.calibration_note(0, 0, len(pages))

        sources_text = "\n".join(f"• {p.url}" for p in pages[:5])

        prompt = SYNTHESIS_PROMPT.format(
            query=query,
            key_information=key_information,
            sources=sources_text,
            calibration=calibration,
            language_rule=ClawPersonality.get_language_rule(),
        )

        try:
            response, _ = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500,
                enforce_language=True,
            )
            return _strip_confidence_tags(response), False
        except Exception as e:
            logger.error(f"❌ Synthesis failed: {e}")
            return catalog_msg("research_synthesis_failed"), True


research_engine = ResearchEngine()