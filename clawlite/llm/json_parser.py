"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

llm/json_parser.py — Extractor robusto de JSON de respuestas LLM
Los LLMs a veces devuelven JSON dentro de prosa, code fences, o con texto
extra antes/después. Este parser balancea llaves y extrae el primer JSON válido.
"""

import json
from typing import Any


def extract_json(text: str, expect: str | None = None) -> dict | list | None:
    """
    Extrae el primer JSON válido (objeto o array) de un texto.

    expect: opcional, indica qué tipo se espera:
      - "object": devuelve solo si el JSON es un dict. Si encuentra un array que
        contiene exactamente un dict, lo desempaqueta (caso común: LLM envuelve en array por error).
      - "array": devuelve solo si el JSON es una lista.
      - None: devuelve cualquier JSON válido (compatibilidad hacia atrás).

    Tolerante a code fences, prosa antes/después, múltiples JSONs, texto extra al final.
    Retorna None si no encuentra JSON del tipo esperado.
    """
    if not text:
        return None

    # Quitar code fences si las hay
    cleaned = text.strip()
    candidates = []
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            stripped = part.strip()
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()
            if stripped:
                candidates.append(stripped)
    candidates.append(cleaned)

    # Recorrer candidatos y devolver el primero que matchee el tipo esperado
    for cand in candidates:
        for parsed in _iter_balanced_extracts(cand):
            normalized = _coerce_to_expected(parsed, expect)
            if normalized is not None:
                return normalized
    return None


def _coerce_to_expected(value, expect: str | None):
    """
    Normaliza el valor extraído al tipo esperado.
    - expect=None: cualquier valor sirve
    - expect="object": dict directamente, o array de 1 dict (lo desempaqueta)
    - expect="array": lista directamente
    Retorna None si no se puede coercionar.
    """
    if expect is None:
        return value
    if expect == "object":
        if isinstance(value, dict):
            return value
        # Caso común: el LLM envolvió un dict en array
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
            return value[0]
        return None
    if expect == "array":
        if isinstance(value, list):
            return value
        return None
    return value


def _iter_balanced_extracts(text: str):
    """
    Generador que produce TODOS los JSONs balanceados encontrados en el texto,
    en orden. Permite descartar uno y probar el siguiente cuando el tipo no coincide.
    """
    i = 0
    while i < len(text):
        char = text[i]
        if char in '{[':
            opener = char
            closer = '}' if opener == '{' else ']'
            result, end_idx = _extract_balanced_with_end(text, i, opener, closer)
            if result is not None:
                yield result
                i = end_idx + 1
                continue
        i += 1


def _extract_balanced_with_end(text: str, start: int, opener: str, closer: str):
    """
    Como _extract_balanced pero devuelve también el índice final del JSON encontrado.
    Retorna (valor_parseado, índice_de_cierre) o (None, start).
    """
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        char = text[i]

        if escape_next:
            escape_next = False
            continue

        if char == '\\' and in_string:
            escape_next = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate), i
                except json.JSONDecodeError:
                    continue

    return None, start


def _extract_balanced(text: str, start: int, opener: str, closer: str) -> dict | list | None:
    """
    Desde la posición start, encuentra el cierre balanceado y parsea.
    Respeta strings entre comillas (no cuenta llaves dentro de strings).
    """
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        char = text[i]

        if escape_next:
            escape_next = False
            continue

        if char == '\\' and in_string:
            escape_next = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    # Intentar el siguiente posible cierre
                    continue

    return None
