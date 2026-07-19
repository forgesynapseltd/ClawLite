"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agent/tools/reminder.py — Herramienta de recordatorios
Extrae fecha/hora del lenguaje natural y guarda el recordatorio real en la DB.
"""

import json
import re
from datetime import datetime
from loguru import logger
from clawlite.llm.client import llm
from clawlite.memory.profile import DeepMemory


REMINDER_EXTRACT_PROMPT = """Extract reminder information from this message and return ONLY a JSON object.

Return format:
{
  "is_reminder": true/false,
  "message": "what to remind about",
  "remind_at": "YYYY-MM-DDTHH:MM:00",
  "recurrence": "" | "daily" | "weekly" | "monthly",
  "weekday": null | "monday".."sunday"
}

Rules:
- Only set is_reminder=true if the user explicitly asks to be reminded
- For relative dates: today is {today}, use it to calculate exact dates
- If the user names a DAY OF THE WEEK (e.g. Monday, "lunes", "el martes", "every Friday"),
  do NOT compute the calendar date yourself — instead set "weekday" to that day's ENGLISH
  NAME, exactly one of: "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
  "sunday". Translate from the user's language before returning the value (e.g. "viernes" ->
  "friday", "vendredi" -> "friday"). Never return localized weekday names or a number — a
  number is ambiguous between calendar conventions and must not be used. The system will
  compute the exact next date for that weekday. Still fill "remind_at" with your best guess
  of the date and the correct time.
- If NO day of week is named, set "weekday" to null and put the full date in "remind_at".
- If no time specified, use 09:00
- recurrence: set "daily", "weekly" or "monthly" ONLY if the user asks for a repeating
  reminder (e.g. every day / every Monday / monthly). For a one-time reminder use "".
- If not a reminder request, return: {"is_reminder": false}
- Return ONLY the JSON, nothing else
"""

# Enum canonico del contrato ("monday".."sunday"), NO numero 0-6: la misma
# causa raiz de B6 en calendario (8 jul) - el 7B alterna entre las dos
# convenciones numericas universales (0=Monday ISO vs 0=Sunday cron/JS)
# segun idioma, "viernes" devolvia un numero que caia en sabado (reproducido
# aqui 14/15, 15 jul). "friday" no admite doble lectura. Mismo fix ya
# validado y cerrado en core.py._handle_calendar_event.
_WEEKDAY_ENUM = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


class ReminderTool:

    def __init__(self, deep_memory: DeepMemory):
        self.memory = deep_memory

    async def try_extract(self, user_id: str, message: str) -> dict | None:
        """
        Intenta extraer un recordatorio del mensaje.
        Devuelve el recordatorio si lo encuentra, None si no.
        """
        today = datetime.now().strftime("%Y-%m-%d %A %H:%M")
        prompt = REMINDER_EXTRACT_PROMPT.replace("{today}", today)

        try:
            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": message}],
                system=prompt,
                max_tokens=100,
                structured=True,
            )

            import re
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return None

            data = json.loads(match.group(0))

            if not data.get("is_reminder"):
                return None

            # El modelo marcó intención de recordatorio pero no resolvió la fecha
            # (devolvió remind_at vacío o ausente). No es un fallo del usuario: es
            # que faltó el dato. No reventamos parseando '' — devolvemos None para
            # que el flujo conversacional pida la hora con naturalidad.
            raw_when = (data.get("remind_at") or "").strip()
            if not raw_when:
                logger.debug("Reminder sin fecha resuelta — se deja al flujo conversacional")
                return None

            try:
                remind_at = datetime.fromisoformat(raw_when)
            except ValueError:
                logger.debug(f"Reminder con fecha inválida: {raw_when!r}")
                return None

            # Si el usuario nombró un día de la semana, NO confiamos en la fecha
            # que calculó el modelo (suele errar el día). El LLM solo identifica
            # QUÉ día es (enum "monday".."sunday"); la fecha exacta la calcula
            # Python, que no se equivoca. Valor fuera del enum (incluido un
            # número legado: aceptarlo reintroduciría la ambigüedad de B6) →
            # fail-safe determinista: se ignora y vale la fecha del modelo.
            weekday = data.get("weekday")
            if weekday is not None:
                target = _WEEKDAY_ENUM.get(str(weekday).strip().lower())
                if target is not None:
                    remind_at = self._next_weekday(target, remind_at.hour, remind_at.minute)
                else:
                    logger.warning(
                        f"⏰ weekday fuera del enum del contrato: {weekday!r} — "
                        f"ignorado (fail-safe: se usa la fecha del modelo)"
                    )

            recurrence = data.get("recurrence", "") or ""

            # Validación de fecha futura: si el modelo devolvió una hora ya pasada
            # para un recordatorio único, dispararía de inmediato — eso es un error
            # de interpretación, no la intención del usuario. Se descarta.
            if not recurrence and remind_at <= datetime.now():
                logger.debug(f"Reminder descartado: fecha en el pasado ({remind_at})")
                return None

            # El LLM a veces incluye un fragmento de elisión suelto al inicio del
            # título ("d'appeler..." en vez de "appeler...", francés/italiano),
            # inconsistente entre llamadas idénticas al mismo mensaje — nunca es
            # información real, siempre es debris gramatical de haber cortado el
            # mensaje justo después de la contracción. Recorte determinista,
            # agnóstico de idioma (patrón universal: palabra de 1-3 letras +
            # apóstrofe, no lista de palabras).
            reminder_message = re.sub(r"^\w{1,3}['’]\s*", "", data["message"])
            reminder_id = self.memory.add_reminder(
                user_id, reminder_message, remind_at, recurrence=recurrence
            )

            logger.info(f"⏰ Reminder created #{reminder_id}: '{reminder_message}' at {remind_at}"
                        + (f" (recurrente: {recurrence})" if recurrence else ""))

            return {
                "id": reminder_id,
                "message": reminder_message,
                "remind_at": remind_at,
                "recurrence": recurrence,
            }

        except Exception as e:
            logger.debug(f"Reminder extraction skipped: {e}")
            return None

    @staticmethod
    def _next_weekday(target_weekday: int, hour: int, minute: int) -> datetime:
        """
        Próxima fecha cuyo día de la semana sea target_weekday (0=lunes..6=domingo),
        a la hora dada. Si hoy ES ese día pero la hora ya pasó, salta a la semana
        siguiente. Cálculo puro de Python — sin depender del modelo para la fecha.
        """
        from datetime import timedelta
        now = datetime.now()
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        days_ahead = (target_weekday - candidate.weekday()) % 7
        if days_ahead == 0 and candidate <= now:
            days_ahead = 7
        return candidate + timedelta(days=days_ahead)

    def format_confirmation(self, reminder: dict) -> str:
        remind_at = reminder["remind_at"]
        formatted = remind_at.strftime("%d/%m/%Y %H:%M")
        recurrence = reminder.get("recurrence", "")
        repeat_icon = {
            "daily": "🔁 1d",
            "weekly": "🔁 7d",
            "monthly": "🔁 30d",
        }.get(recurrence, "")
        text = f"⏰ *{reminder['message']}*\n📅 {formatted}"
        if repeat_icon:
            text += f"\n{repeat_icon}"
        text += "\n\n`/recordatorios`"
        return text
