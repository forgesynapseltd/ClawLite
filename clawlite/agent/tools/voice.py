"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agent/tools/voice.py — Entrada y salida de voz
Whisper local + Groq Whisper fallback para transcripción.
edge-tts para síntesis de voz (gratis, sin API key), multilingüe.
"""

import os
import re
from pathlib import Path
from loguru import logger
from clawlite.config import config

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False


class VoiceTool:
    """
    Manejo completo de voz:
    - Transcripción: Whisper local primero, Groq como fallback transparente
    - Síntesis: edge-tts (gratis, sin API key, multilingüe)
    """

    def __init__(self):
        self._groq_client = None
        if config.GROQ_API_KEY and GROQ_AVAILABLE:
            self._groq_client = Groq(api_key=config.GROQ_API_KEY)

    async def transcribe(self, audio_path: str) -> tuple[str, str]:
        """
        Transcribe un archivo de audio a texto.
        Devuelve (texto, fuente) donde fuente es 'local' o 'cloud'.
        """
        try:
            import whisper
            model = whisper.load_model("base")
            result = model.transcribe(audio_path)
            text = result["text"].strip()
            logger.info(f"🎙 Whisper local transcribió: {text[:80]}")
            return text, "local"
        except ImportError:
            logger.debug("Whisper local no instalado, usando Groq")
        except Exception as e:
            logger.warning(f"⚠️  Whisper local falló: {e}")

        if self._groq_client:
            try:
                with open(audio_path, "rb") as f:
                    transcription = self._groq_client.audio.transcriptions.create(
                        file=(Path(audio_path).name, f.read()),
                        model="whisper-large-v3",
                    )
                text = transcription.text.strip()
                logger.info(f"🎙 Groq Whisper transcribió: {text[:80]}")
                return text, "cloud"
            except Exception as e:
                logger.error(f"❌ Groq Whisper falló: {e}")

        raise Exception("No hay servicio de transcripción disponible.")

    async def synthesize(self, text: str, output_path: str, lang: str = "es") -> bool:
        """Convierte texto a audio usando edge-tts (gratis, sin API key). Guarda MP3."""
        if not EDGE_TTS_AVAILABLE:
            logger.error("❌ edge-tts no instalado. Instala con: pip install edge-tts")
            return False

        try:
            clean_text = self._clean_for_speech(text)
            if not clean_text:
                return False

            # Voz natural y gratuita según idioma. edge-tts usa voces con nombre.
            # Cobertura multilingüe real (no solo es/en) para que la voz acompañe la
            # garantía de idioma del resto del sistema. El idioma lo decide el detector
            # determinista canónico (ver detect_lang), no una lista de palabras.
            voice = {
                "es": "es-ES-AlvaroNeural",
                "en": "en-US-AriaNeural",
                "de": "de-DE-KillianNeural",
                "fr": "fr-FR-HenriNeural",
                "it": "it-IT-DiegoNeural",
                "pt": "pt-BR-AntonioNeural",
                "nl": "nl-NL-MaartenNeural",
                "ru": "ru-RU-DmitryNeural",
                "zh": "zh-CN-YunxiNeural",
                "ja": "ja-JP-KeitaNeural",
                "ko": "ko-KR-InJoonNeural",
                "ar": "ar-SA-HamedNeural",
                "hi": "hi-IN-MadhurNeural",
                "tr": "tr-TR-AhmetNeural",
                "pl": "pl-PL-MarekNeural",
            }.get(lang, "es-ES-AlvaroNeural")

            communicate = edge_tts.Communicate(clean_text, voice)
            await communicate.save(output_path)
            logger.info(f"🔊 Audio sintetizado ({voice}): {output_path}")
            return True
        except Exception as e:
            logger.error(f"❌ TTS falló: {e}")
            return False

    def _clean_for_speech(self, text: str) -> str:
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'_(.+?)_', r'\1', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        text = re.sub(r'[⏰📧📅📄🔍🎙🔊✅❌⚠️🌍💡🎨🤖🚀]', '', text)
        text = re.sub(r'\n+', '. ', text)
        text = text.strip()
        if len(text) > 1500:
            text = text[:1500] + "..."
        return text

    def detect_lang(self, text: str) -> str:
        """Idioma (código ISO) para elegir la voz TTS. Usa el detector DETERMINISTA
        canónico (personality/language.py, ~97 idiomas por n-gramas de caracteres),
        NO listas de palabras. Triple fallback: detección del texto → idioma objetivo
        del turno → 'es'. Así una respuesta en alemán suena con voz alemana, etc."""
        from clawlite.personality.language import detect_language, get_target_language
        return detect_language(text) or get_target_language() or "es"


voice_tool = VoiceTool()
