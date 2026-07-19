"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

auth.py — Autenticación OAuth2 independiente del bot
Corre una sola vez desde la terminal. El bot nunca toca este proceso.
"""

import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

from clawlite.config import config

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

CREDENTIALS_PATH = "./data/credentials.json"
_VAULT_TOKEN_KEY = "GMAIL_TOKEN_JSON"


def main():
    print("\n🔐 ClawLite — Autenticación de Google\n")

    if not Path(CREDENTIALS_PATH).exists():
        print(f"❌ No encontré {CREDENTIALS_PATH}")
        print("   Descarga el credentials.json desde Google Cloud Console")
        print("   y colócalo en D:\\ClawLite\\data\\")
        sys.exit(1)

    print("📋 Servicios que se van a autorizar:")
    print("   • Gmail — leer, enviar y responder correos")
    print("   • Google Calendar — ver y crear eventos")
    print("\n🌐 Abriendo el navegador para autorizar...\n")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            access_type="offline",
        )

        config.vault.set(_VAULT_TOKEN_KEY, creds.to_json())

        print(f"\n✅ Autenticación completada.")
        print(f"   Token guardado de forma cifrada en la bóveda de ClawLite.")
        print(f"\n🚀 Ahora puedes arrancar ClawLite:")
        print(f"   python -m clawlite.main\n")

    except Exception as e:
        print(f"\n❌ Error durante la autenticación: {e}")
        print("   Intenta de nuevo o revisa tu credentials.json")
        sys.exit(1)


if __name__ == "__main__":
    main()
