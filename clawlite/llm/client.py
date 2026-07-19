"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

llm/client.py — Abstracción del modelo de lenguaje
Ollama primero (local). Groq como fallback. Visión soportada.
"""

import ollama as ollama_lib
from contextvars import ContextVar
from groq import Groq
from loguru import logger
from clawlite.config import config

# Override de UN SOLO USO: fuerza que la próxima cascada de este turno prefiera
# la nube por encima de Ollama, sin tocar la preferencia guardada del usuario en
# DB. Mismo patrón que el contextvar de idioma en personality/language.py —
# estado scoped a la tarea asyncio actual, no global ni persistente. Se activa
# cuando el usuario acepta el disclaimer de soberanía (ver Agent.
# resolve_comparison_disclaimer en core.py) y se limpia en un finally.
_force_cloud_once: ContextVar[bool] = ContextVar("_force_cloud_once", default=False)


def set_force_cloud_once(value: bool = True) -> None:
    _force_cloud_once.set(value)

try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


class NoLLMAvailable(Exception):
    pass


class LLMProviderChosenFailed(Exception):
    """
    El proveedor que el usuario eligió explícitamente (sin fallback) falló. Se
    distingue de NoLLMAvailable para que la capa de arriba notifique al usuario
    ("tu modelo elegido no respondió, ¿esperas o cambias con /modelo?") en vez de
    caer a otro proveedor a escondidas. `provider` y `is_rate_limit` permiten un
    mensaje preciso.
    """
    def __init__(self, provider: str, is_rate_limit: bool, detail: str = ""):
        self.provider = provider
        self.is_rate_limit = is_rate_limit
        self.detail = detail
        super().__init__(f"Proveedor elegido '{provider}' falló (rate_limit={is_rate_limit}): {detail}")


class LLMClient:
    def __init__(self):
        self._groq_client = None
        self._openai_client = None
        self._anthropic_client = None

        if config.GROQ_API_KEY:
            self._groq_client = Groq(api_key=config.GROQ_API_KEY)
        if config.OPENAI_API_KEY and OPENAI_AVAILABLE:
            self._openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
        if config.ANTHROPIC_API_KEY and ANTHROPIC_AVAILABLE:
            self._anthropic_client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

    async def complete(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 1024,
        structured: bool = False,
        task_type: str = "conversational",
        enforce_language: bool = False,
        temperature: float | None = None,
    ) -> tuple[str, str]:
        """
        Devuelve (respuesta, fuente).

        task_type elige la cascada de proveedores definida en config (.env):
        - "conversational" (default): planner, profile, small talk, etc.
        - "coding": CodingAgent — requiere razonamiento robusto sobre código.

        El cliente recorre la cascada del task_type intentando cada proveedor.
        Si todos fallan, lanza NoLLMAvailable.

        enforce_language: SOLO para texto de cara al usuario. Si el turno tiene un
        idioma objetivo confirmado (personality/language.py) y la salida no está en
        ese idioma, regenera UNA vez con mandato endurecido. La garantía es por
        código, no por confianza en el modelo. No aplica a llamadas `structured`
        (planner/factchecker/etc.) ni a turnos sin idioma objetivo.
        """
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        content, source = await self._run_cascade(
            full_messages, max_tokens, structured, task_type, temperature
        )

        if enforce_language and not structured:
            content, source = await self._enforce_output_language(
                content, source, system, messages, max_tokens, task_type
            )
        return content, source

    async def _run_cascade(
        self,
        full_messages: list[dict],
        max_tokens: int,
        structured: bool,
        task_type: str,
        temperature: float | None = None,
    ) -> tuple[str, str]:
        """Recorre la cascada de proveedores del task_type. Devuelve (content, source)
        o lanza NoLLMAvailable / LLMProviderChosenFailed."""
        cascade = config.get_task_cascade(task_type)
        if _force_cloud_once.get() and "ollama" in cascade:
            # Un solo uso: Ollama pasa al final de ESTA cascada (no se elimina,
            # sigue siendo el último recurso si ningún proveedor de nube está
            # configurado). No toca config.get_user_llm_preference() ni la DB.
            cascade = [p for p in cascade if p != "ollama"] + ["ollama"]
        last_error: Exception | None = None

        for provider in cascade:
            try:
                content = None
                source = None

                if provider == "ollama":
                    # Un solo intento con el modelo que la tarea necesita. Si falla,
                    # CUENTA como fallo del proveedor "ollama" — el cascade externo
                    # decide el siguiente paso (Groq u otra nube, si está configurada).
                    # Nunca se sustituye en silencio por un modelo más débil: eso fue
                    # la causa exacta de la degradación de ayer (investigación y
                    # código servidos por un modelo de 3B sin que nadie lo supiera).
                    model = config.ollama_model_for(task_type, structured)
                    _options = {"num_predict": max_tokens}
                    if temperature is not None:
                        _options["temperature"] = temperature
                    response = ollama_lib.chat(
                        model=model,
                        messages=full_messages,
                        options=_options,
                    )
                    content, source = response["message"]["content"], "local"

                elif provider == "anthropic" and self._anthropic_client:
                    system_content = next(
                        (m["content"] for m in full_messages if m["role"] == "system"), ""
                    )
                    user_messages = [m for m in full_messages if m["role"] != "system"]
                    response = await self._anthropic_client.messages.create(
                        model=config.ANTHROPIC_MODEL,
                        max_tokens=max_tokens,
                        system=system_content,
                        messages=user_messages,
                    )
                    logger.info(f"☁️  [{task_type}] Anthropic [{config.ANTHROPIC_MODEL}]")
                    content, source = response.content[0].text, "cloud"

                elif provider == "openai" and self._openai_client:
                    response = await self._openai_client.chat.completions.create(
                        model=config.OPENAI_MODEL,
                        messages=full_messages,
                        max_tokens=max_tokens,
                    )
                    logger.info(f"☁️  [{task_type}] OpenAI [{config.OPENAI_MODEL}]")
                    content, source = response.choices[0].message.content, "cloud"

                elif provider == "groq" and self._groq_client:
                    response = self._groq_client.chat.completions.create(
                        model=config.GROQ_MODEL,
                        messages=full_messages,
                        max_tokens=max_tokens,
                    )
                    logger.info(f"☁️  [{task_type}] Groq [{config.GROQ_MODEL}]")
                    content, source = response.choices[0].message.content, "cloud"

                else:
                    # Proveedor en la cascada pero sin cliente configurado: saltar silencioso
                    logger.debug(f"Provider '{provider}' en cascada de {task_type} pero sin cliente — saltando")
                    continue

                # Una respuesta VACÍA cuenta como fallo del proveedor, no como
                # respuesta válida. Los modelos locales pequeños a veces devuelven
                # cadena vacía ante prompts triviales con un system largo; sin esta
                # comprobación, la cascada aceptaba el vacío y nunca probaba el
                # siguiente proveedor. Ahora cae al siguiente, igual que con un error.
                if content is None or not content.strip():
                    raise ValueError(f"{provider} devolvió respuesta vacía")

                return content, source

            except Exception as e:
                last_error = e
                logger.warning(f"⚠️  [{task_type}] {provider} falló: {e}")
                continue

        # Toda la cascada falló. Si la cascada tenía UN SOLO proveedor, fue porque
        # el usuario lo eligió explícitamente sin fallback — su elección manda y
        # debe notificarse de forma específica, no como un fallo genérico.
        if len(cascade) == 1:
            provider = cascade[0]
            detail = str(last_error) if last_error else ""
            # Detección best-effort de límite de cuota (429 / rate limit). No
            # siempre viene etiquetado limpio; si no se reconoce, se trata como
            # caída genérica del proveedor.
            low = detail.lower()
            is_rate_limit = "429" in low or "rate limit" in low or "rate_limit" in low or "quota" in low
            raise LLMProviderChosenFailed(provider=provider, is_rate_limit=is_rate_limit, detail=detail)

        # Cascada normal con varios proveedores, todos fallaron.
        raise NoLLMAvailable(
            f"Ningún proveedor de la cascada [{task_type}] respondió. "
            f"Cascada intentada: {cascade}. Último error: {last_error}"
        )

    async def _enforce_output_language(
        self,
        content: str,
        source: str,
        system: str,
        messages: list[dict],
        max_tokens: int,
        task_type: str,
    ) -> tuple[str, str]:
        """
        Red determinista de idioma (Nivel B). Si el turno tiene idioma objetivo y la
        salida NO está en él, regenera UNA sola vez con un mandato endurecido. El
        idioma equivocado no llega al usuario. Un único reintento (nunca bucle): si
        tras regenerar sigue sin coincidir, se entrega lo mejor disponible y se
        registra — fallar honesto antes que colgar.
        """
        from clawlite.personality.language import (
            get_target_language, detect_language, language_name,
        )
        target = get_target_language()
        if not target:
            return content, source
        # Solo se regenera ante un desajuste SEGURO. Si el detector lee el idioma
        # objetivo, o si NO está seguro (None: salida corta o escritura que py3langid
        # no lee), se entrega tal cual — nunca se degrada una respuesta posiblemente
        # correcta por una duda del detector.
        det = detect_language(content)
        if det is None or det == target:
            return content, source

        name = language_name(target)
        logger.warning(f"🌐 Salida no está en {target}; regenerando en {name}")
        hardened = (f"{system}\n\n" if system else "") + (
            f"ABSOLUTE REQUIREMENT: Write your ENTIRE response in {name}. "
            f"Every sentence must be in {name}. Do not use any other language."
        )
        retry_messages = [{"role": "system", "content": hardened}] + messages
        try:
            content2, source2 = await self._run_cascade(
                retry_messages, max_tokens, False, task_type
            )
            if content2 and content2.strip():
                det2 = detect_language(content2)
                if det2 is not None and det2 != target:
                    logger.warning(
                        f"🌐 Tras regenerar sigue sin ser {target}; se entrega lo mejor disponible"
                    )
                return content2, source2
        except Exception as e:
            logger.warning(f"🌐 Regeneración de idioma falló ({e}); se entrega la original")
        return content, source

    async def complete_vision(
        self,
        image_b64: str,
        question: str,
        system: str = "",
        max_tokens: int = 1024,
    ) -> tuple[str, str]:
        """Procesa una imagen. Usa Groq vision si Ollama no tiene soporte."""

        # Intentar Ollama con visión (llama3.2-vision si está disponible)
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({
                "role": "user",
                "content": question,
                "images": [image_b64],
            })
            response = ollama_lib.chat(
                model="llama3.2-vision",
                messages=messages,
                options={"num_predict": max_tokens},
            )
            return response["message"]["content"], "local"
        except Exception:
            pass

        # Groq vision fallback
        if self._groq_client:
            try:
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                        },
                        {"type": "text", "text": question},
                    ],
                })
                response = self._groq_client.chat.completions.create(
                    model="meta-llama/llama-4-scout-17b-16e-instruct",
                    messages=messages,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content, "cloud"
            except Exception as e:
                logger.error(f"❌ Vision falló: {e}")

        raise NoLLMAvailable("No hay modelo de visión disponible.")


llm = LLMClient()
