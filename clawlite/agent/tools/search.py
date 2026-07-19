"""
agent/tools/search.py — Búsqueda web real vía Tavily
Tavily es innegociable desde el día 1.
"""

from tavily import TavilyClient
from loguru import logger
from clawlite.config import config
from clawlite.sandbox.guard import SandboxGuard
from clawlite.governance import action_guard, Mandate, MandateOrigin

guard = SandboxGuard(mode=config.SANDBOX_MODE)


class SearchTool:
    def __init__(self):
        self._client = TavilyClient(api_key=config.TAVILY_API_KEY)
        logger.info("🔍 SearchTool (Tavily) iniciado")

    async def search(self, query: str, max_results: int = 5, user_id: str | None = None) -> str:
        """
        Ejecuta una búsqueda web y devuelve los resultados formateados
        como texto plano para inyectar en el prompt del LLM.
        """
        # Mediación por el kernel (auditoría + contrato). La búsqueda saca la query a
        # Tavily (egress); se audita como web_search. user_id real cuando el llamador
        # lo provee (todos los call sites reales del proyecto lo tienen disponible);
        # "local" solo como fallback si ninguno lo pasa.
        decision = action_guard.authorize(
            "web_search",
            Mandate(origin=MandateOrigin.USER_DIRECT, user_id=user_id or "local", summary=(query or "")[:120]),
        )
        if not decision.allowed:
            logger.warning(f"🛡️ Búsqueda web DENEGADA por el kernel: {decision.reason}")
            return "Search blocked by security policy."

        guard.validate_tool_call("search_web")
        guard.validate_content(query)

        logger.info(f"🔍 Buscando: '{query}'")

        try:
            results = self._client.search(
                query=query,
                max_results=max_results,
                search_depth="basic",
                include_answer=True,
            )

            # Si Tavily devuelve una respuesta directa, úsala
            if results.get("answer"):
                return f"Web answer: {results['answer']}"

            # Si no, formatea los resultados
            formatted = []
            for r in results.get("results", []):
                formatted.append(
                    f"Source: {r.get('url', '')}\n"
                    f"Title: {r.get('title', '')}\n"
                    f"Content: {r.get('content', '')[:500]}"
                )

            return "\n\n---\n\n".join(formatted) if formatted else "No results found."

        except Exception as e:
            logger.error(f"❌ Error en búsqueda Tavily: {e}")
            return f"Search failed: {str(e)}"


search_tool = SearchTool()
