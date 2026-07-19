"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

governance/action_guard.py — El kernel de gobernanza (reference monitor).

TODA acción con efecto en el mundo (enviar email, crear evento, ejecutar código,
borrar memoria, enviar un mensaje proactivo, etc.) DEBE pedir autorización aquí
antes de ejecutarse. Diseñado con las cuatro propiedades de un reference monitor:

  1. Mediación completa  — ningún camino actúa por su cuenta; todos llaman a authorize().
  2. Default-deny        — una acción solo se permite si su tipo está REGISTRADO con
                            política explícita. Lo no registrado se deniega. (Esa es la
                            regla de "ninguna puerta abierta": lo no permitido, prohibido.)
  3. A prueba de manipulación — kernel pequeño, sin dependencia del LLM. El contenido
                            externo (email/web/archivo) NUNCA puede originar un mandato
                            que autorice una acción de alto impacto (mata la inyección
                            que intenta disparar acciones).
  4. Verificable         — auditoría append-only de cada solicitud, decisión y motivo.

Y dos invariantes duras:
  · FAIL-CLOSED: cualquier error, estado ambiguo o no verificable → DENY. Nunca fail-open.
  · MANDATO HUMANO: las acciones de alto impacto exigen una orden/aprobación explícita
    del usuario en SU canal. El modelo PROPONE; el humano MANDA; el kernel EJECUTA.

Este módulo es DETERMINISTA y autónomo: no llama al modelo, no depende del idioma, no
usa listas de palabras. Decide por contrato.
"""

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from loguru import logger
from clawlite.sandbox.guard import redact_secrets


# ── Vocabulario del kernel ───────────────────────────────────────────────────

class RiskTier(str, Enum):
    LOW = "low"        # lecturas / sin efecto en el mundo real
    MEDIUM = "medium"  # cambios de estado local (memoria, config)
    HIGH = "high"      # acción saliente, destructiva o con efecto sobre terceros


class MandateOrigin(str, Enum):
    """De DÓNDE viene la autoridad para actuar. Es la pieza anti-inyección: una
    acción de alto impacto solo puede nacer del usuario, NUNCA de contenido externo."""
    USER_DIRECT = "user_direct"            # el usuario lo pidió explícitamente, en su canal
    USER_APPROVED = "user_approved"        # el usuario aprobó una propuesta concreta vía gate
    SYSTEM_SCHEDULED = "system_scheduled"  # tarea recurrente que el usuario autorizó antes
    EXTERNAL_CONTENT = "external_content"   # email/web/archivo — ORIGEN NO CONFIABLE


class Outcome(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"   # permitido en principio, pero falta el 'sí' humano


@dataclass(frozen=True)
class Mandate:
    """La autoridad bajo la que se pide una acción. `summary` se audita (sin secretos)."""
    origin: MandateOrigin
    user_id: str
    summary: str = ""


@dataclass(frozen=True)
class Policy:
    action_type: str
    risk: RiskTier
    requires_approval: bool
    allowed_origins: frozenset  # qué orígenes de mandato pueden autorizar esta acción


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    action_type: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.outcome == Outcome.ALLOW


class GovernanceDenied(Exception):
    """Se lanza cuando enforce() recibe un no-ALLOW. Hace el enforcement FAIL-CLOSED por
    construcción: no hay Decision que el llamador pueda ignorar — un no-ALLOW aborta la
    ejecución. Lleva la Decision para auditar/responder con su motivo."""
    def __init__(self, decision: "Decision"):
        self.decision = decision
        super().__init__(decision.reason)


# ── Registro de capacidades (la ÚNICA fuente de lo permitido) ────────────────
# Añadir una acción nueva con efecto = registrarla aquí con su política. Si no está,
# el kernel la deniega. EXTERNAL_CONTENT no aparece en ningún `allowed_origins`: por
# diseño, ningún contenido externo puede autorizar ninguna acción.

_U_DIRECT = MandateOrigin.USER_DIRECT
_U_APPROVED = MandateOrigin.USER_APPROVED
_SCHEDULED = MandateOrigin.SYSTEM_SCHEDULED

_POLICIES: dict[str, Policy] = {
    # Acciones SALIENTES / destructivas — exigen aprobación humana explícita.
    "send_email": Policy(
        "send_email", RiskTier.HIGH, True, frozenset({_U_DIRECT, _U_APPROVED})
    ),
    "create_calendar_event": Policy(
        "create_calendar_event", RiskTier.HIGH, True, frozenset({_U_DIRECT, _U_APPROVED})
    ),
    "clear_memory": Policy(
        "clear_memory", RiskTier.HIGH, True, frozenset({_U_DIRECT, _U_APPROVED})
    ),
    # Ejecución de código: aislada en Docker, pero es alto impacto (red + arbitrario).
    # Nace de una petición del usuario — directa (síncrona) o vía un job que el propio
    # usuario creó (SYSTEM_SCHEDULED). La aprobación se podrá exigir luego.
    "execute_code": Policy(
        "execute_code", RiskTier.HIGH, False, frozenset({_U_DIRECT, _U_APPROVED, _SCHEDULED})
    ),
    # Mensaje proactivo (el agente inicia): de una tarea recurrente ya autorizada.
    "send_proactive_message": Policy(
        "send_proactive_message", RiskTier.MEDIUM, False, frozenset({_SCHEDULED, _U_DIRECT})
    ),
    # Lecturas externas — sin efecto en el mundo, pero igual mediadas y auditadas.
    # Permiten origen programado: el brief diario y los watches leen por una tarea
    # recurrente que el usuario autorizó (SYSTEM_SCHEDULED), no solo a petición directa.
    "read_email": Policy("read_email", RiskTier.LOW, False, frozenset({_U_DIRECT, _SCHEDULED})),
    "read_calendar": Policy("read_calendar", RiskTier.LOW, False, frozenset({_U_DIRECT, _SCHEDULED})),
    "web_search": Policy(
        "web_search", RiskTier.LOW, False, frozenset({_U_DIRECT, _SCHEDULED})
    ),
}


class ActionGuard:
    """Punto de decisión de política (PDP). Decide ALLOW / DENY / REQUIRE_APPROVAL,
    fail-closed, y deja rastro de auditoría. NO ejecuta: la ejecución es del llamador
    tras recibir ALLOW (separación decisión/ejecución)."""

    def __init__(self, audit_path: str = "./data/audit_log.jsonl"):
        self._audit_path = Path(audit_path)
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # nunca debe impedir arrancar; el audit fallará suave luego
            logger.warning(f"🛡️ ActionGuard: no pude preparar el log de auditoría: {e}")
        logger.info("🛡️ ActionGuard (motor de gobernanza) iniciado — default-deny, fail-closed")

    def authorize(self, action_type: str, mandate: Mandate, *, payload_summary: str = "") -> Decision:
        """Decide si una acción puede ejecutarse. Fail-closed: cualquier excepción → DENY."""
        try:
            policy = _POLICIES.get(action_type)

            # 1. Default-deny: acción no registrada = prohibida.
            if policy is None:
                return self._finish(
                    Outcome.DENY, action_type, mandate, payload_summary,
                    "acción no registrada (default-deny)",
                )

            # 2. Origen del mandato: el contenido externo NUNCA autoriza nada; y cada
            #    acción solo acepta los orígenes de su política.
            if mandate.origin not in policy.allowed_origins:
                return self._finish(
                    Outcome.DENY, action_type, mandate, payload_summary,
                    f"origen de mandato '{mandate.origin.value}' no autorizado para esta acción",
                )

            # 3. Acciones de alto impacto: exigen el 'sí' humano explícito. Si el mandato
            #    aún no es 'aprobado', se pide aprobación (no se ejecuta nada todavía).
            if policy.requires_approval and mandate.origin != MandateOrigin.USER_APPROVED:
                return self._finish(
                    Outcome.REQUIRE_APPROVAL, action_type, mandate, payload_summary,
                    "requiere aprobación humana explícita",
                )

            return self._finish(
                Outcome.ALLOW, action_type, mandate, payload_summary,
                "autorizado por política",
            )

        except Exception as e:
            # Invariante dura: ante CUALQUIER fallo, denegar. Nunca fail-open.
            logger.error(f"🛡️ ActionGuard error inesperado → DENY (fail-closed): {e}")
            try:
                self._write_audit({
                    "ts": time.time(), "action_type": action_type,
                    "outcome": Outcome.DENY.value, "reason": f"error del kernel: {e}",
                    "user_id": getattr(mandate, "user_id", "?"),
                })
            except Exception:
                pass
            return Decision(Outcome.DENY, action_type, f"error del kernel (fail-closed): {e}")

    def enforce(self, action_type: str, mandate: Mandate, *, payload_summary: str = "") -> Decision:
        """Como authorize(), pero FAIL-CLOSED por construcción: si la decisión no es ALLOW,
        LANZA GovernanceDenied. Elimina el modo de fallo de llamar a authorize() e ignorar
        el resultado — un no-ALLOW (DENY o REQUIRE_APPROVAL) aborta la ejecución. Devuelve
        la Decision (ALLOW) para quien la quiera auditar."""
        decision = self.authorize(action_type, mandate, payload_summary=payload_summary)
        if not decision.allowed:
            raise GovernanceDenied(decision)
        return decision

    def policy_for(self, action_type: str) -> Policy | None:
        """Introspección (para el modelo de amenazas / pruebas)."""
        return _POLICIES.get(action_type)

    def registered_actions(self) -> list[str]:
        return sorted(_POLICIES.keys())

    # ── Auditoría append-only ────────────────────────────────────────────────

    def _finish(self, outcome: Outcome, action_type: str, mandate: Mandate,
                payload_summary: str, reason: str) -> Decision:
        entry = {
            "ts": time.time(),
            "user_id": mandate.user_id,
            "action_type": action_type,
            "mandate_origin": mandate.origin.value,
            "outcome": outcome.value,
            "reason": reason,
            # Resumen SIEMPRE redactado de secretos antes de tocar disco.
            "mandate_summary": redact_secrets(mandate.summary or "")[:300],
            "payload_summary": redact_secrets(payload_summary or "")[:300],
        }
        self._write_audit(entry)
        icon = {"allow": "✅", "deny": "🚫", "require_approval": "🔐"}.get(outcome.value, "•")
        logger.info(f"🛡️ ActionGuard {icon} {action_type} [{mandate.origin.value}] → {outcome.value} ({reason})")
        return Decision(outcome, action_type, reason)

    def _write_audit(self, entry: dict):
        """Escribe una línea JSON en el log append-only. Nunca lanza (un fallo de
        auditoría no debe abrir una puerta: el peor caso es perder la línea, no permitir)."""
        try:
            with open(self._audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"🛡️ ActionGuard: fallo al escribir auditoría (se continúa): {e}")


# Instancia única del kernel — la consume todo el sistema.
action_guard = ActionGuard()
