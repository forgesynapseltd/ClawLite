"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

llm/bootstrap.py — Garantiza que los modelos Ollama configurados existen
localmente ANTES de arrancar el bot. Un instalador de un clic no puede
depender de que el usuario corra `ollama pull` a mano.
"""

import ollama as ollama_lib
from loguru import logger
from clawlite.config import config


def _configured_ollama_models() -> set[str]:
    """Todos los modelos Ollama que la configuración actual puede necesitar.
    Con la unificación, en el caso normal es solo OLLAMA_MODEL."""
    models = {config.OLLAMA_MODEL}
    for name in (
        "OLLAMA_MODEL_STRUCTURED", "OLLAMA_MODEL_CODING", "OLLAMA_MODEL_MEMORY",
        "OLLAMA_MODEL_PLANNER", "OLLAMA_MODEL_FACTCHECK", "OLLAMA_MODEL_CONVERSATIONAL",
    ):
        value = getattr(config, name, "")
        if value:
            models.add(value)
    return models


def ensure_ollama_models_available() -> None:
    """Descarga cualquier modelo Ollama configurado que no exista localmente.
    Si Ollama no está corriendo (usuario en modo 100% nube, ej. Groq), se registra
    con claridad pero NO interrumpe el arranque — puede haber otros proveedores
    en la cascada perfectamente válidos sin Ollama."""
    try:
        local_models = {m.model for m in ollama_lib.list().models}
    except Exception as e:
        logger.warning(
            f"⚠️ No se pudo consultar Ollama al arrancar ({e}) — se omite la verificación "
            f"de modelos locales. Si tu configuración depende de Ollama, esa tarea fallará "
            f"hasta que el servicio esté disponible."
        )
        return

    for model in _configured_ollama_models():
        if model in local_models:
            continue
        logger.info(f"⬇️ Modelo '{model}' no está descargado — descargando ahora (puede tardar varios minutos)...")
        try:
            ollama_lib.pull(model)
            logger.info(f"✅ Modelo '{model}' descargado")
        except Exception as e:
            logger.error(
                f"❌ No se pudo descargar '{model}': {e}. "
                f"El bot arrancará igual, pero esa tarea fallará hasta que se descargue a mano."
            )
