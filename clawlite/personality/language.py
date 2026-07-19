"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

personality/language.py — Idioma de respuesta: FUENTE ÚNICA y red determinista.

El idioma de la respuesta NO puede depender de que el modelo "iguale" el idioma
del mensaje: un modelo local pequeño se desvía cuando el contexto (perfil, fuentes
web, historial) está en otro idioma. Diseño (robusto, sin umbrales afinados):

1. IDIOMA OBJETIVO = el que leyó el PLANNER sobre el mensaje del usuario (su campo
   `lang`). El planner ve SOLO el mensaje actual (no el perfil/web/historial), así
   que su lectura no se contamina y es fiable — leer el idioma de un texto limpio es
   tarea fácil incluso para un modelo pequeño. Si el planner no da un idioma
   reconocible, no se fuerza nada (None): comportamiento natural.
2. GARANTÍA DE SALIDA (en llm.complete): si la respuesta sale en un idioma
   CLARAMENTE distinto del objetivo, se regenera UNA vez. Solo se actúa ante un
   desajuste seguro; ante duda no se toca (no se degrada una respuesta posiblemente
   correcta).

El detector estadístico por n-gramas (py3langid, `detect_language`) ya NO decide la
entrada — misfiraba en frases cortas latinas ("cuéntame un chiste" -> 'fr') y enmudecía
en chino/árabe cortos, obligando a una maraña de umbrales y ramas. Se reserva para lo
que sí hace bien: VERIFICAR un texto ya generado (la garantía de salida) y elegir voz
TTS — usos donde "no estoy seguro" (None) es la respuesta segura.

El idioma objetivo se transporta por toda la cadena async (incl. asyncio.gather del
orchestrator) en un ContextVar — sin ensuciar la firma de ninguna función. Lo leen
get_language_rule() (para el prompt) y el gate de llm.complete() (para la garantía).
"""

import contextvars
from loguru import logger

try:
    from py3langid.langid import LanguageIdentifier, MODEL_FILE
    _identifier = LanguageIdentifier.from_pickled_model(MODEL_FILE, norm_probs=True)
    _LANGID_AVAILABLE = True
except Exception:  # dependencia ausente o modelo no cargable: degradar a "sin detección"
    _identifier = None
    _LANGID_AVAILABLE = False

# Código ISO 639-1 -> nombre en inglés. Al modelo se le da el NOMBRE, no el código:
# "reply in German" se obedece mucho mejor que "reply in 'de'".
_LANG_NAMES = {
    "en": "English", "es": "Spanish", "de": "German", "fr": "French",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ru": "Russian",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ar": "Arabic",
    "hi": "Hindi", "tr": "Turkish", "pl": "Polish", "sv": "Swedish",
    "no": "Norwegian", "da": "Danish", "fi": "Finnish", "el": "Greek",
    "he": "Hebrew", "id": "Indonesian", "uk": "Ukrainian", "cs": "Czech",
    "ro": "Romanian", "hu": "Hungarian", "th": "Thai", "vi": "Vietnamese",
    "tl": "Tagalog", "fa": "Persian", "ca": "Catalan",
}
_NAME_TO_CODE = {name.lower(): code for code, name in _LANG_NAMES.items()}

# Algunos modelos devuelven el código ISO 639-2/3 (3 letras, p.ej. "fil" para Filipino,
# "spa", "deu") en vez del 639-1 (2 letras) que se les pide. Esto NO es una lista de
# palabras por idioma: es la tabla estándar de equivalencia de códigos ISO, para los
# idiomas que el sistema ya nombra. Sin esto, un código de 3 letras quedaba sin
# reconocer y el turno se respondía en un idioma equivocado.
_ISO3_TO_ISO1 = {
    "eng": "en", "spa": "es", "deu": "de", "ger": "de", "fra": "fr", "fre": "fr",
    "ita": "it", "por": "pt", "nld": "nl", "dut": "nl", "rus": "ru", "zho": "zh",
    "chi": "zh", "jpn": "ja", "kor": "ko", "ara": "ar", "hin": "hi", "tur": "tr",
    "pol": "pl", "swe": "sv", "nor": "no", "dan": "da", "fin": "fi", "ell": "el",
    "gre": "el", "heb": "he", "ind": "id", "ukr": "uk", "ces": "cs", "cze": "cs",
    "ron": "ro", "rum": "ro", "hun": "hu", "tha": "th", "vie": "vi", "tgl": "tl",
    "fil": "tl", "fas": "fa", "per": "fa", "cat": "ca",
}

# Confianza del DETECTOR (solo para verificar texto ya generado / elegir voz, NO para
# decidir la entrada). Texto corto o baja confianza => detect_language devuelve None =
# "no estoy seguro", que en esos usos es la respuesta segura (no regenerar / fallback).
_MIN_CHARS = 10
_MIN_PROB = 0.85

# Idioma objetivo del turno actual (código ISO) o None si no se debe forzar.
_target_language: contextvars.ContextVar = contextvars.ContextVar(
    "clawlite_target_language", default=None
)


def detect_language(text: str) -> str | None:
    """
    Idioma de `text` como código ISO 639-1 (p.ej. 'es', 'de', 'zh'), o None si no
    es fiable (texto corto, baja confianza, o detector ausente). Determinista:
    mismo texto -> mismo resultado, hoy y mañana.
    """
    if not _LANGID_AVAILABLE:
        return None
    clean = (text or "").strip()
    if len(clean) < _MIN_CHARS:
        return None
    try:
        code, prob = _identifier.classify(clean)
    except Exception:
        return None
    if prob < _MIN_PROB:
        return None
    return code


def language_name(code: str | None) -> str | None:
    """Nombre legible en inglés para el prompt; el propio código si no se conoce."""
    if not code:
        return None
    return _LANG_NAMES.get(code, code)


def _semantic_to_code(value: str | None) -> str | None:
    """Normaliza el `lang` del planner a código ISO. Acepta código de 2 letras
    (formato pedido al planner) o, por robustez, el nombre del idioma en inglés."""
    if not value:
        return None
    v = value.strip().lower()
    if len(v) == 2 and v.isalpha():
        return v
    if len(v) == 3 and v in _ISO3_TO_ISO1:  # código ISO 639-2/3 → 639-1
        return _ISO3_TO_ISO1[v]
    return _NAME_TO_CODE.get(v)


def set_turn_language(semantic_lang: str | None) -> str | None:
    """
    Fija el idioma objetivo del turno = el idioma que leyó el PLANNER sobre el mensaje
    del usuario (`lang`). Señal única, limpia y fiable (el planner solo ve el mensaje,
    no el contexto). Si no es reconocible, devuelve None y no se fuerza nada
    (comportamiento natural). Determinista para una misma entrada. Acepta código ISO de
    2 letras o el nombre del idioma en inglés (robustez ante el formato del planner)."""
    target = _semantic_to_code(semantic_lang)
    _target_language.set(target)
    if target:
        logger.debug(f"🌐 Idioma objetivo del turno: {target} (planner)")
    else:
        logger.debug(f"🌐 Idioma objetivo: ninguno — el planner no dio un idioma reconocible")
    return target


def get_target_language() -> str | None:
    """Código ISO del idioma objetivo del turno, o None si no se debe forzar."""
    return _target_language.get()


def clear_turn_language() -> None:
    _target_language.set(None)
