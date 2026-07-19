"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

security/vault.py — Bóveda de credenciales con cifrado en reposo

Qué resuelve: las claves API y tokens (Groq, OpenAI, Anthropic, Tavily, Telegram,
OAuth de Gmail) no deben vivir en texto plano en .env, donde una fuga accidental
(commit a git, backup, screenshare) las expone — el riesgo nº1 de un proyecto
local-first y open-source.

Diseño — la mejor seguridad que el entorno permita, sin configurar nada:

  • NIVEL FUERTE (keystore del SO). La clave maestra que cifra la bóveda se guarda
    en el almacén de credenciales del sistema operativo (Windows Credential Manager,
    macOS Keychain, Linux Secret Service). El SO la custodia cifrada con las
    credenciales de la sesión del usuario. Protege incluso de otro usuario con
    acceso de lectura al disco. Es lo que hacen las apps serias.

  • NIVEL BASE (fallback local). Si la máquina no tiene keystore disponible
    (algunos Linux headless, contenedores), se cae a una clave maestra en un
    archivo de permisos restringidos. Protege de fugas accidentales (git, backups),
    que es el riesgo real del open-source, aunque no de acceso malicioso al disco.

  En ambos casos: arranque desatendido (no pide passphrase), credenciales cifradas
  en reposo, y NUNCA entran en un prompt — el vault solo las entrega a quien las usa
  para autenticar un SDK, jamás al texto que va al modelo.

  La degradación es automática y se loguea con honestidad: el usuario sabe qué
  nivel de protección tiene activo.

Contrato:
  vault.get(key)            → valor descifrado o None
  vault.set(key, value)     → cifra y persiste
  vault.has(key)            → bool
  vault.import_from_env(...) → migra claves de os.environ a la bóveda (una vez)
  vault.protection_level()  → "os_keystore" | "local_file" (para diagnóstico)
"""

import os
import json
import base64
import stat
from pathlib import Path
from loguru import logger
from cryptography.fernet import Fernet, InvalidToken

try:
    import keyring
    import keyring.errors
    _KEYRING_AVAILABLE = True
except Exception:
    _KEYRING_AVAILABLE = False


# Identificadores del keystore del SO. Estables: cambiarlos perdería el acceso a
# una bóveda ya cifrada.
_KEYSTORE_SERVICE = "clawlite-vault"
_KEYSTORE_KEY_NAME = "master-key"


class CredentialVault:
    """
    Bóveda de credenciales cifradas en reposo. La clave maestra vive en el
    keystore del SO si existe; si no, en un archivo local restringido.
    Los valores se cifran con Fernet (AES-128-CBC + HMAC) usando esa clave.
    """

    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        self._master_key_file = self.vault_path.parent / ".clawlite_master.key"
        self._level = "local_file"  # se actualiza al obtener la clave maestra
        self._fernet = Fernet(self._load_or_create_master_key())
        self._cache: dict | None = None

    # ── Clave maestra ──────────────────────────────────────────────────────

    def _load_or_create_master_key(self) -> bytes:
        """
        Obtiene la clave maestra. Preferencia: keystore del SO. Si no hay keystore
        disponible, archivo local con permisos restringidos. Si ya existe una
        clave (en cualquiera de los dos), la reutiliza — no se regenera nunca,
        porque eso invalidaría la bóveda cifrada existente.
        """
        # 1. Intentar keystore del SO (nivel fuerte).
        if _KEYRING_AVAILABLE:
            try:
                existing = keyring.get_password(_KEYSTORE_SERVICE, _KEYSTORE_KEY_NAME)
                if existing:
                    self._level = "os_keystore"
                    return existing.encode()
                # No existe aún: generar y guardar en el keystore.
                new_key = Fernet.generate_key()
                keyring.set_password(_KEYSTORE_SERVICE, _KEYSTORE_KEY_NAME, new_key.decode())
                self._level = "os_keystore"
                logger.info("🔐 Vault: clave maestra creada en el keystore del SO")
                return new_key
            except Exception as e:
                # El keystore existe como librería pero falla en esta máquina
                # (p. ej. Linux headless sin Secret Service). Caer a archivo local.
                logger.warning(f"🔐 Vault: keystore del SO no disponible ({e}); usando archivo local")

        # 2. Fallback: archivo local restringido (nivel base).
        return self._load_or_create_file_key()

    def _load_or_create_file_key(self) -> bytes:
        if self._master_key_file.exists():
            self._level = "local_file"
            return self._master_key_file.read_bytes().strip()

        new_key = Fernet.generate_key()
        self._master_key_file.write_bytes(new_key)
        # Permisos restrictivos: solo el dueño puede leer/escribir (0600).
        # En Windows os.chmod tiene efecto limitado, pero no daña.
        try:
            os.chmod(self._master_key_file, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
        self._level = "local_file"
        logger.info("🔐 Vault: clave maestra creada en archivo local restringido")
        return new_key

    def protection_level(self) -> str:
        """'os_keystore' (fuerte) o 'local_file' (base). Para diagnóstico/status."""
        return self._level

    # ── Almacén cifrado ──────────────────────────────────────────────────────

    def _read_all(self) -> dict:
        if self._cache is not None:
            return self._cache
        if not self.vault_path.exists():
            self._cache = {}
            return self._cache
        try:
            blob = self.vault_path.read_bytes()
            decrypted = self._fernet.decrypt(blob)
            self._cache = json.loads(decrypted.decode())
        except (InvalidToken, json.JSONDecodeError, ValueError) as e:
            # Bóveda corrupta o clave maestra equivocada. No reventar el arranque:
            # tratar como vacía y avisar. (Caso típico: se borró la clave maestra
            # pero quedó la bóveda; o al revés.)
            logger.error(f"🔐 Vault: no se pudo descifrar la bóveda ({type(e).__name__}). "
                         f"Se tratará como vacía. Si configuraste claves, re-impórtalas.")
            self._cache = {}
        return self._cache

    def _write_all(self, data: dict):
        blob = self._fernet.encrypt(json.dumps(data).encode())
        self.vault_path.write_bytes(blob)
        try:
            os.chmod(self.vault_path, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
        self._cache = data

    # ── API pública ──────────────────────────────────────────────────────────

    def get(self, key: str) -> str | None:
        return self._read_all().get(key) or None

    def set(self, key: str, value: str):
        data = self._read_all()
        data[key] = value
        self._write_all(data)

    def has(self, key: str) -> bool:
        return bool(self._read_all().get(key))

    def import_from_env(self, keys: list[str]) -> list[str]:
        """
        Migración: para cada nombre en `keys`, si está en os.environ con valor y
        AÚN NO está en la bóveda, lo cifra y guarda. Devuelve los nombres migrados.
        Idempotente: una vez en la bóveda, no se vuelve a tomar del entorno (la
        bóveda es la fuente de verdad).
        """
        migrated = []
        data = self._read_all()
        changed = False
        for k in keys:
            if data.get(k):
                continue  # ya en la bóveda — fuente de verdad
            env_val = os.getenv(k, "")
            if env_val:
                data[k] = env_val
                changed = True
                migrated.append(k)
        if changed:
            self._write_all(data)
        return migrated
