"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

setup.py — Bootstrap interactivo previo al primer arranque.
Corre una sola vez desde la terminal, antes de que exista conexión a
Telegram (el bot no puede arrancar sin TELEGRAM_BOT_TOKEN, así que este
paso no puede resolverse dentro de una conversación de Telegram). Mismo
patrón que auth.py: script standalone, el bot principal nunca lo invoca.
"""

import re
import shutil
import sys
from pathlib import Path

def _bundle_root() -> Path:
    """Carpeta donde vive el PROGRAMA (plantillas de solo lectura que vienen
    con la instalación), a diferencia de la carpeta de DATOS del usuario
    (cwd, ver launcher._set_data_home -- ese chdir es correcto para .env,
    que sí es dato de usuario, pero .env.example nunca fue dato de usuario).
    En un build de PyInstaller (onedir u onefile), sys._MEIPASS apunta a la
    carpeta del bundle. En modo dev (no empaquetado), es la raíz del repo."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


ENV_PATH = Path("./.env")  # dato de usuario -- correcto relativo al cwd/data-home
ENV_EXAMPLE_PATH = _bundle_root() / ".env.example"  # plantilla de solo lectura -- del bundle, no del cwd


def _read_env_values(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _read_env_hints(path: Path) -> dict:
    """Extrae, para cada KEY= en .env.example, el comentario inmediatamente
    anterior como pista de dónde conseguir el valor. CONTRATO documentado
    en el propio .env.example (ver su encabezado): un comentario de una
    línea justo arriba de cada clave. .env.example es la ÚNICA fuente de
    esa documentación — no se duplica en este módulo."""
    hints = {}
    pending = ""
    if not path.exists():
        return hints
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            text = stripped.lstrip("#").strip()
            if text and not all(c in "─═-=" for c in text):
                pending = text
            continue
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key:
                hints[key] = pending
        pending = ""
    return hints


def _write_env_value(path: Path, key: str, value: str) -> None:
    """Actualiza (o agrega) una clave en el .env. Abre con newline="" en
    lectura y escritura para que Python NO traduzca terminadores de línea
    en ningún sentido. Preserva íntegramente todas las líneas NO
    modificadas (incluyendo su terminador exacto); la línea que coincide
    con la clave se reconstruye como "KEY=value" — cualquier espaciado,
    comentario en línea o formato distinto que tuviera esa línea
    específica se pierde (comportamiento esperado: el objetivo es fijar
    el valor, no preservar el formato previo de esa única línea)."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    # [^\r\n]* sin ancla "$" -- "." SI matchea \r (solo excluye \n), asi que
    # con newline="" (sin traduccion) un .*$ se comia el \r de los finales
    # \r\n y lo perdia en la sustitucion. Excluir \r del caracter de clase
    # evita eso, pero un "$" detras exige llegar justo antes de un \n, y
    # [^\r\n]* nunca puede cruzar el \r para alcanzarlo -- falla el match
    # por completo en lineas CRLF. [^\r\n]* solo (sin "$") ya se autolimita
    # a una sola linea (se detiene ante \r, \n o fin de archivo), sin
    # necesitar ancla de cierre.
    pattern = re.compile(rf"^{re.escape(key)}=[^\r\n]*", re.MULTILINE)
    if pattern.search(raw):
        raw = pattern.sub(f"{key}={value}", raw, count=1)
    else:
        sep = "" if not raw or raw.endswith("\n") else "\n"
        raw = f"{raw}{sep}{key}={value}\n"
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(raw)


def _claim_telegram_owner_arm(token: str) -> int | None:
    """
    Primera mitad de la captura automática de TELEGRAM_OWNER_ID: vacía la
    cola de updates viejos (de quien sea, de antes de este momento) y
    devuelve el timestamp de referencia -- solo cuenta lo que llegue
    DESPUÉS de este punto. None si no se pudo contactar la API de Telegram.

    Separado de _claim_telegram_owner_poll (antes era una sola función con
    un input() bloqueante en el medio) para que el wizard web pueda hacer
    lo mismo en dos llamadas HTTP en vez de una espera bloqueante de
    terminal -- misma lógica, reusada por ambos wizards (terminal y web).
    """
    import time
    import requests
    api = f"https://api.telegram.org/bot{token}"
    try:
        resp = requests.get(f"{api}/getUpdates", params={"offset": -1}, timeout=10)
        resp.raise_for_status()
        old_updates = resp.json().get("result", [])
        if old_updates:
            last_id = old_updates[-1]["update_id"]
            requests.get(f"{api}/getUpdates", params={"offset": last_id + 1}, timeout=10)
    except Exception as e:
        print(f"⚠️  No pude verificar el bot en Telegram: {e}")
        return None
    return int(time.time())


def _claim_telegram_owner_poll(token: str, since: int) -> str | None:
    """
    Segunda mitad: busca UN mensaje nuevo posterior a `since` (doble
    anclaje temporal pedido por el auditor -- no solo offset de cola,
    también timestamp del mensaje). Devuelve el user_id de quien lo mandó,
    o None si todavía no llegó nada (el llamador decide si reintentar).
    """
    import requests
    api = f"https://api.telegram.org/bot{token}"
    try:
        resp = requests.get(f"{api}/getUpdates", params={"timeout": 3}, timeout=10)
        resp.raise_for_status()
        updates = resp.json().get("result", [])
        for update in updates:
            msg = update.get("message")
            if not msg or not msg.get("from", {}).get("id"):
                continue
            if msg.get("date", 0) < since:
                continue  # mensaje anterior al armado -- no cuenta, aunque haya sobrevivido al offset
            return str(msg["from"]["id"])
    except Exception:
        pass
    return None


def _claim_telegram_owner(token: str) -> str | None:
    """
    Determina automáticamente el TELEGRAM_OWNER_ID llamando a la API de
    Telegram directamente (getUpdates) -- sin que el usuario tenga que
    buscar su propio ID manualmente en un bot de terceros. La decisión de
    quién es el dueño queda anclada a QUIEN corre este script LOCALMENTE
    (presiona Enter en esta terminal), no al canal de Telegram en sí --
    una versión anterior de este mecanismo (reclamar por el primer
    mensaje que el BOT recibiera durante operación normal) fue rechazada
    por el auditor de seguridad por mover la decisión de propiedad al
    mismo canal no autenticado que se busca proteger. Este diseño evita
    ese defecto: el bot NUNCA acepta mensajes de nadie hasta que este
    bootstrap ya terminó y escribió TELEGRAM_OWNER_ID en el .env.

    Versión de terminal (compat, sin cambio de comportamiento): compone
    _claim_telegram_owner_arm + input() bloqueante + hasta 3 intentos de
    _claim_telegram_owner_poll con sleep(2), igual que antes.
    """
    import time
    since = _claim_telegram_owner_arm(token)
    if since is None:
        return None

    print("\n📱 Mandale CUALQUIER mensaje a tu bot en Telegram ahora mismo.")
    input("   Presioná Enter acá apenas lo hayas mandado: ")

    for _ in range(3):
        result = _claim_telegram_owner_poll(token, since)
        if result:
            return result
        time.sleep(2)

    print("⚠️  No recibí ningún mensaje nuevo. Vas a tener que configurarlo manualmente.")
    return None


def main():
    from clawlite.config import BOOTSTRAP_REQUIRED_KEYS

    print("\n🔧 ClawLite — Configuración inicial\n")

    if not ENV_PATH.exists():
        if not ENV_EXAMPLE_PATH.exists():
            print(f"❌ No encontré {ENV_EXAMPLE_PATH} — repo incompleto.")
            sys.exit(1)
        shutil.copy(ENV_EXAMPLE_PATH, ENV_PATH)
        print(f"✅ Creé {ENV_PATH} a partir de {ENV_EXAMPLE_PATH}.")

    current = _read_env_values(ENV_PATH)
    missing = [k for k in BOOTSTRAP_REQUIRED_KEYS if not current.get(k)]

    if not missing:
        print("✅ Ya tienes las claves imprescindibles configuradas.")
        print("🚀 Puedes arrancar ClawLite: python -m clawlite.main\n")
        return

    hints = _read_env_hints(ENV_EXAMPLE_PATH)
    print("Necesito un par de datos para poder arrancar:\n")
    try:
        for key in missing:
            # TELEGRAM_OWNER_ID: intentar automatizarlo vía la API de
            # Telegram (requiere TELEGRAM_BOT_TOKEN ya configurado -- el
            # orden de BOOTSTRAP_REQUIRED_KEYS lo garantiza, TOKEN va
            # antes que OWNER_ID). Si falla por cualquier motivo, cae al
            # mismo flujo manual de siempre -- nunca bloquea el setup.
            if key == "TELEGRAM_OWNER_ID":
                token = current.get("TELEGRAM_BOT_TOKEN") or _read_env_values(ENV_PATH).get("TELEGRAM_BOT_TOKEN", "")
                value = _claim_telegram_owner(token) if token else None
                if value:
                    _write_env_value(ENV_PATH, key, value)
                    print(f"✅ TELEGRAM_OWNER_ID capturado automáticamente: {value}")
                    continue

            hint = hints.get(key, "")
            prompt = f"{key}" + (f" — {hint}" if hint else "") + ": "
            value = input(prompt).strip()
            while not value:
                value = input(f"  (no puede quedar vacío) {key}: ").strip()
            _write_env_value(ENV_PATH, key, value)
    except EOFError:
        print("\n❌ No se puede leer de la entrada estándar (¿estás en una terminal interactiva?).")
        print(f"   Edita {ENV_PATH} manualmente y agrega las claves que falten, luego reintenta.")
        sys.exit(1)

    print(f"\n✅ Configuración guardada en {ENV_PATH}.")
    print("🚀 Ahora puedes arrancar ClawLite: python -m clawlite.main\n")


if __name__ == "__main__":
    main()
