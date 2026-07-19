"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agent/tools/gmail.py — Integración con Gmail y Google Calendar
OAuth2 gestionado externamente via clawlite/auth.py
El bot solo lee el token — nunca inicia flujos de autenticación.
"""

import json
import base64
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from clawlite.config import config
from clawlite.governance import action_guard, Mandate, MandateOrigin

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

CREDENTIALS_PATH = "./data/credentials.json"
# Migración legacy: el token vivía en texto plano en este archivo. Ahora
# vive cifrado en el Vault bajo _VAULT_TOKEN_KEY. Si el archivo legacy
# existe, se migra una sola vez (_migrate_legacy_token_file) y se borra
# el rastro plano -- no queda copia sin cifrar después de migrar.
_LEGACY_TOKEN_PATH = "./data/gmail_token.json"
_VAULT_TOKEN_KEY = "GMAIL_TOKEN_JSON"


def _migrate_legacy_token_file():
    """Migración de una sola vez, idempotente: si no hay archivo legacy o
    el Vault ya tiene el token, no hace nada. Transaccional en la
    práctica: solo borra el archivo legacy DESPUÉS de confirmar que el
    Vault guardó el valor y que se puede reconstruir Credentials desde
    él -- si algo falla en el medio (corte de energía, excepción,
    corrupción), el archivo legacy sigue existiendo y no se pierde el
    único refresh token."""
    legacy = Path(_LEGACY_TOKEN_PATH)
    if not legacy.exists() or config.vault.has(_VAULT_TOKEN_KEY):
        return
    try:
        token_json = legacy.read_text(encoding="utf-8")
        config.vault.set(_VAULT_TOKEN_KEY, token_json)
        # Verificación antes de borrar: releer del Vault y reconstruir
        # Credentials. Si esto falla, el archivo legacy NO se borra.
        stored = config.vault.get(_VAULT_TOKEN_KEY)
        Credentials.from_authorized_user_info(json.loads(stored), SCOPES)
        legacy.unlink(missing_ok=True)
        logger.info("🔐 Token de Gmail migrado del archivo plano a la bóveda cifrada")
    except Exception as e:
        logger.warning(f"⚠️ No pude migrar el token legacy de Gmail: {e}")


class GmailTool:

    def __init__(self):
        self._gmail_service = None
        self._calendar_service = None
        _migrate_legacy_token_file()

    def is_configured(self) -> bool:
        return Path(CREDENTIALS_PATH).exists()

    def is_authenticated(self) -> bool:
        return config.vault.has(_VAULT_TOKEN_KEY)

    def _get_creds(self) -> Credentials:
        """Obtiene credenciales válidas, refrescando si es necesario."""
        token_json = config.vault.get(_VAULT_TOKEN_KEY)
        if not token_json:
            raise Exception(
                "No hay token de autenticación. "
                "Ejecuta: python -m clawlite.auth"
            )

        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                config.vault.set(_VAULT_TOKEN_KEY, creds.to_json())
            else:
                raise Exception(
                    "Token inválido. Ejecuta: python -m clawlite.auth"
                )

        return creds

    def _get_gmail(self):
        if not self._gmail_service:
            self._gmail_service = build("gmail", "v1", credentials=self._get_creds())
        return self._gmail_service

    def _get_calendar(self):
        if not self._calendar_service:
            self._calendar_service = build("calendar", "v3", credentials=self._get_creds())
        return self._calendar_service

    # ── GMAIL ──────────────────────────────────────────────────────────────

    def get_unread_emails(self, user_id: str, max_results: int = 10, scheduled: bool = False) -> list[dict]:
        origin = MandateOrigin.SYSTEM_SCHEDULED if scheduled else MandateOrigin.USER_DIRECT
        decision = action_guard.authorize(
            "read_email",
            Mandate(origin=origin, user_id=user_id, summary="leer correos no leídos"),
        )
        if not decision.allowed:
            logger.warning(f"🛡️ Lectura de email DENEGADA por el kernel: {decision.reason}")
            return []
        try:
            service = self._get_gmail()
            results = service.users().messages().list(
                userId="me",
                labelIds=["INBOX", "UNREAD"],
                maxResults=max_results,
            ).execute()

            emails = []
            for msg in results.get("messages", []):
                email = self._get_email_detail(service, msg["id"])
                if email:
                    emails.append(email)

            logger.info(f"📧 {len(emails)} correos no leídos obtenidos")
            return emails

        except HttpError as e:
            logger.error(f"❌ Error obteniendo emails: {e}")
            return []

    def _get_email_detail(self, service, msg_id: str) -> dict | None:
        try:
            msg = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()

            headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
            body = self._extract_body(msg["payload"])

            return {
                "id": msg_id,
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", "(Sin asunto)"),
                "date": headers.get("Date", ""),
                "body": body[:2000],
                "snippet": msg.get("snippet", ""),
            }
        except Exception as e:
            logger.debug(f"Error obteniendo email {msg_id}: {e}")
            return None

    def _extract_body(self, payload: dict) -> str:
        if "parts" in payload:
            for part in payload["parts"]:
                if part["mimeType"] == "text/plain":
                    data = part["body"].get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore").strip()
        data = payload["body"].get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore").strip()
        return ""

    def send_email(self, to: str, subject: str, body: str, user_id: str,
                    origin: MandateOrigin = MandateOrigin.USER_DIRECT) -> bool:
        # Acción de alto impacto: debe pasar por el kernel de gobernanza. `origin`
        # lo decide el llamador (p.ej. USER_APPROVED tras el "sí" del usuario);
        # default USER_DIRECT fail-safe para cualquier caller que no lo especifique.
        decision = action_guard.authorize(
            "send_email",
            Mandate(origin=origin, user_id=str(user_id), summary=f"enviar email a {to}"),
        )
        if not decision.allowed:
            logger.warning(f"🛡️ Envío de email DENEGADO por el kernel: {decision.reason}")
            return False

        try:
            service = self._get_gmail()
            message = MIMEText(body)
            message["to"] = to
            message["subject"] = subject
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            logger.info(f"📤 Email enviado a {to}")
            return True
        except Exception as e:
            logger.error(f"❌ Error enviando email: {e}")
            return False

    def mark_as_read(self, msg_id: str) -> bool:
        try:
            self._get_gmail().users().messages().modify(
                userId="me", id=msg_id,
                body={"removeLabelIds": ["UNREAD"]}
            ).execute()
            return True
        except Exception:
            return False

    def reply_to_email(self, msg_id: str, body: str, user_id: str,
                        origin: MandateOrigin = MandateOrigin.USER_DIRECT) -> bool:
        # Acción de alto impacto: debe pasar por el kernel de gobernanza. `origin`
        # lo decide el llamador (p.ej. USER_APPROVED tras el "sí" del usuario en
        # core.py); default USER_DIRECT fail-safe para cualquier caller que no lo
        # especifique. Antes este sink hardcodeaba USER_DIRECT, lo que autobloqueaba
        # el envío aunque el gate de core.py ya hubiera recibido USER_APPROVED (§15).
        decision = action_guard.authorize(
            "send_email",
            Mandate(origin=origin, user_id=str(user_id), summary=f"responder correo {msg_id}"),
        )
        if not decision.allowed:
            logger.warning(f"🛡️ Respuesta de email DENEGADA por el kernel: {decision.reason}")
            return False

        try:
            service = self._get_gmail()
            original = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()

            headers = {h["name"]: h["value"] for h in original["payload"]["headers"]}
            to = headers.get("From", "")
            subject = headers.get("Subject", "")
            message_id = headers.get("Message-ID", "")
            thread_id = original.get("threadId", "")

            if not subject.startswith("Re:"):
                subject = f"Re: {subject}"

            message = MIMEText(body)
            message["to"] = to
            message["subject"] = subject
            if message_id:
                message["In-Reply-To"] = message_id
                message["References"] = message_id

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            service.users().messages().send(
                userId="me",
                body={"raw": raw, "threadId": thread_id}
            ).execute()

            self.mark_as_read(msg_id)
            logger.info(f"📤 Reply enviado al hilo {thread_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Error enviando reply: {e}")
            return False

    def get_email_by_index(self, emails: list[dict], index: int) -> dict | None:
        if 1 <= index <= len(emails):
            return emails[index - 1]
        return None

    def format_emails_for_summary(self, emails: list[dict]) -> str:
        if not emails:
            return "No hay correos no leídos."
        parts = []
        for i, email in enumerate(emails, 1):
            parts.append(
                f"[{i}] De: {email['from']}\n"
                f"Asunto: {email['subject']}\n"
                f"Fecha: {email['date']}\n"
                f"Contenido: {email['snippet']}"
            )
        return "\n\n---\n\n".join(parts)

    # ── GOOGLE CALENDAR ────────────────────────────────────────────────────

    def get_upcoming_events(self, user_id: str, max_results: int = 10, scheduled: bool = False) -> list[dict]:
        origin = MandateOrigin.SYSTEM_SCHEDULED if scheduled else MandateOrigin.USER_DIRECT
        decision = action_guard.authorize(
            "read_calendar",
            Mandate(origin=origin, user_id=user_id, summary="leer próximos eventos"),
        )
        if not decision.allowed:
            logger.warning(f"🛡️ Lectura de calendario DENEGADA por el kernel: {decision.reason}")
            return []
        try:
            service = self._get_calendar()
            now = datetime.utcnow().isoformat() + "Z"

            events_result = service.events().list(
                calendarId="primary",
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            ).execute()

            events = []
            for e in events_result.get("items", []):
                start = e["start"].get("dateTime", e["start"].get("date", ""))
                end = e["end"].get("dateTime", e["end"].get("date", ""))
                events.append({
                    "id": e["id"],
                    "title": e.get("summary", "(Sin título)"),
                    "start": start,
                    "end": end,
                    "location": e.get("location", ""),
                    "description": e.get("description", ""),
                })

            logger.info(f"📅 {len(events)} eventos próximos obtenidos")
            return events

        except HttpError as e:
            logger.error(f"❌ Error obteniendo eventos: {e}")
            return []

    def create_event(
        self,
        title: str,
        start: datetime,
        user_id: str,
        end: datetime = None,
        description: str = "",
        location: str = "",
        origin: MandateOrigin = MandateOrigin.USER_DIRECT,
    ) -> dict | None:
        # Acción de alto impacto: debe pasar por el kernel de gobernanza. `origin`
        # lo decide el llamador (p.ej. USER_APPROVED tras el "sí" del usuario en
        # core.py); default USER_DIRECT fail-safe para cualquier caller que no lo
        # especifique. Antes este sink hardcodeaba USER_DIRECT, lo que autobloqueaba
        # la creación aunque el gate de core.py ya hubiera recibido USER_APPROVED (§15).
        decision = action_guard.authorize(
            "create_calendar_event",
            Mandate(origin=origin, user_id=str(user_id), summary=f"crear evento: {title}"),
        )
        if not decision.allowed:
            logger.warning(f"🛡️ Creación de evento DENEGADA por el kernel: {decision.reason}")
            return None

        try:
            service = self._get_calendar()

            if not end:
                end = start + timedelta(hours=1)

            event = {
                "summary": title,
                "description": description,
                "location": location,
                "start": {"dateTime": start.isoformat(), "timeZone": "America/Guayaquil"},
                "end": {"dateTime": end.isoformat(), "timeZone": "America/Guayaquil"},
            }

            created = service.events().insert(
                calendarId="primary", body=event
            ).execute()

            logger.info(f"📅 Evento creado: {title} at {start}")
            return {
                "id": created["id"],
                "title": title,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "link": created.get("htmlLink", ""),
            }

        except Exception as e:
            logger.error(f"❌ Error creando evento: {e}")
            return None

    def format_events(self, events: list[dict]) -> str:
        """Formatea eventos para mostrar en Telegram."""
        if not events:
            return "No tienes eventos próximos."

        parts = []
        for i, e in enumerate(events, 1):
            start = e["start"][:16].replace("T", " ") if "T" in e["start"] else e["start"]
            line = f"[{i}] 📅 *{e['title']}*\n🕐 {start}"
            if e.get("location"):
                line += f"\n📍 {e['location']}"
            parts.append(line)

        return "\n\n".join(parts)


gmail_tool = GmailTool()