"""
setup_telegram_topics.py — Script de configuración de Topics de Telegram
════════════════════════════════════════════════════════════════════════
Uso:
    python -m support.setup_telegram_topics

Propósito:
    Detecta automáticamente los topic_id de cada modo de trading
    (papper, testnet, real, hyperliquid, hl-testnet) desde los mensajes
    existentes en un grupo de Telegram con Topics habilitados,
    y genera las líneas para copiar al archivo .env.

Requisitos:
    - Tener un grupo de Telegram con "Topics" habilitado.
    - El bot debe ser miembro ADMIN del grupo.
    - Haber enviado AL MENOS UN MENSAJE a cada topic.
    - Tener el TELEGRAM_BOT_TOKEN configurado en el .env

Documentación oficial:
    https://core.telegram.org/bots/api#sendMessage
    https://core.telegram.org/bots/api#forumtopic

Ejemplo de .env resultante:
    TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
    TELEGRAM_CHAT_ID=-1001234567890
    TELEGRAM_TOPIC_PAPPER=1
    TELEGRAM_TOPIC_HYPERLIQUID_MAINNET=4
    TELEGRAM_TOPIC_HYPERLIQUID_TESTNET=5
"""

import json
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: Falta la libreria 'requests'.")
    print("  Instalala con: pip install requests")
    sys.exit(1)


# ── Leer token desde .env ────────────────────────────────────────────────────

def load_dotenv(path: str = ".env") -> dict:
    """Carga variables del archivo .env (formato CLAVE=valor)."""
    env = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


# ── API Telegram ─────────────────────────────────────────────────────────────

def get_updates(token: str) -> list:
    """Obtiene actualizaciones del bot via getUpdates."""
    resp = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        print(f"  ERROR en getUpdates: {data.get('description', 'desconocido')}")
        return []
    return data.get("result", [])


# ── Palabras clave para detectar cada modo ────────────────────────────────────

# Los modos coinciden EXACTAMENTE con los del executor:
#   papper, hyperliquid_mainnet, hyperliquid_testnet
MODO_KEYWORDS = {
    "papper":              ["papper", "paper", "papel"],
    "hyperliquid_mainnet":   ["hyperliquid", "hl perps", "perps"],
    "hyperliquid_testnet": ["hyperliquid testnet", "hl-testnet", "hl testnet"],
}

MODO_NOMBRES = {
    "papper":              "Papper (paper trading)",
    "hyperliquid_mainnet":   "Hyperliquid Perps (produccion)",
    "hyperliquid_testnet": "Hyperliquid Testnet",
}

MODO_ENV_KEYS = {
    "papper":              "TELEGRAM_TOPIC_PAPPER",
    "hyperliquid_mainnet":   "TELEGRAM_TOPIC_HYPERLIQUID_MAINNET",
    "hyperliquid_testnet": "TELEGRAM_TOPIC_HYPERLIQUID_TESTNET_PERPS",
}


def detect_topics(token: str, chat_id: int) -> dict:
    """
    Busca en los mensajes del grupo los topic_ids de cada modo.
    Retorna dict { "papper": 1, "testnet": 2, ... }.

    Detecta topics por:
      1. forum_topic_created.name (mensaje de creacion del topic)
      2. Contenido del texto del mensaje
    """
    updates = get_updates(token)
    found: dict = {}

    for mode, keywords in MODO_KEYWORDS.items():
        for update in updates:
            msg = update.get("message")
            if not msg:
                continue
            if msg.get("chat", {}).get("id") != chat_id:
                continue
            thread_id = msg.get("message_thread_id")
            if not thread_id:
                continue

            # Metodo 1: forum_topic_created.name (mas confiable)
            topic_created = msg.get("forum_topic_created") or {}
            topic_name = (topic_created.get("name") or "").lower()
            if topic_name and any(w in topic_name for w in keywords):
                if mode not in found:
                    found[mode] = thread_id
                    continue

            # Metodo 2: texto del mensaje
            text = (msg.get("text") or msg.get("caption") or "").lower()
            if any(w in text for w in keywords):
                if mode not in found:
                    found[mode] = thread_id
                    break

    return found


def find_forum_group(updates: list) -> dict | None:
    """Busca en los updates un supergrupo con is_forum=True."""
    for update in updates:
        for src in ("message", "my_chat_member", "channel_post"):
            msg = update.get(src)
            if not msg:
                continue
            chat = msg.get("chat", {})
            if chat.get("type") == "supergroup" and chat.get("is_forum"):
                return chat
    return None


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    """Punto de entrada: detecta topics de Telegram y genera configuración para .env."""
    print("+" + "=" * 63 + "+")
    print("|     SETUP TELEGRAM TOPICS — Trading Bot                     |")
    print("+" + "=" * 63 + "+")
    print()

    # 1. Cargar .env
    env = load_dotenv()
    token = env.get("TELEGRAM_BOT_TOKEN", "")

    if not token:
        print("ERROR: No se encontro TELEGRAM_BOT_TOKEN en el archivo .env")
        print("  Asegurate de tener un archivo .env en la raiz del proyecto")
        print("  con la linea: TELEGRAM_BOT_TOKEN=tu_token_aqui")
        sys.exit(1)

    print(f"  Token cargado: {token[:10]}...{token[-5:]}")
    print()

    # 2. Validar token
    resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
    data = resp.json()
    if not data.get("ok"):
        print(f"  ERROR: Token invalido: {data.get('description', 'desconocido')}")
        sys.exit(1)
    bot_info = data["result"]
    print(f"  Bot conectado: @{bot_info.get('username')}")
    print()

    # 3. Obtener updates
    print("  Obteniendo actualizaciones de Telegram...")
    updates = get_updates(token)
    print(f"  {len(updates)} actualizaciones encontradas.")
    print()

    # 4. Encontrar grupo con Topics
    print("  Buscando grupo con Topics habilitados...")
    group = find_forum_group(updates)
    if not group:
        print()
        print("  ERROR: No se encontro un grupo con Topics.")
        print()
        print("  Posibles causas:")
        print("    1. El bot NO es administrador del grupo.")
        print("    2. El grupo no tiene Topics habilitados.")
        print("    3. No hay mensajes recientes en el grupo.")
        print()
        print("  Solucion:")
        print("    1. Agrega @" + bot_info.get("username", "?") + " como ADMIN al grupo.")
        print("    2. Activa Topics en: Grupo -> Info -> Group Type -> Topics")
        print("    3. Envia cualquier mensaje en el grupo (ej: /start).")
        print("    4. Vuelve a ejecutar este script.")
        sys.exit(1)

    chat_id = group["id"]
    title = group.get("title", "Sin nombre")
    print(f"  Grupo encontrado!")
    print(f"    Chat ID: {chat_id}")
    print(f"    Titulo:  {title}")
    print(f"    Topics:  {'SI' if group.get('is_forum') else 'NO'}")
    print()

    if not group.get("is_forum"):
        print("  ERROR: El grupo NO tiene Topics habilitados.")
        print("  Activalos en: Grupo -> Info -> Group Type -> Topics")
        sys.exit(1)

    # 5. Detectar topics
    print("  Detectando topics...")
    topic_ids = detect_topics(token, chat_id)

    # 6. Mostrar resultados
    print()
    todos_ok = True
    for mode in ["papper", "hyperliquid_mainnet", "hyperliquid_testnet"]:
        nombre = MODO_NOMBRES.get(mode, mode)
        tid = topic_ids.get(mode)
        if tid:
            print(f"    OK  {nombre:30s} -> topic_id={tid}")
        else:
            print(f"    --  {nombre:30s} -> NO DETECTADO")
            todos_ok = False
    print()

    # 7. Si faltan, pedir al usuario que los complete
    if not todos_ok:
        print("  Algunos topics no se detectaron automaticamente.")
        print()
        print("  Para completarlos manualmente:")
        print("    1. Abri Telegram en el grupo.")
        print("    2. Hace clic en cada topic.")
        print("    3. Envia un mensaje como 'topic papper' en el topic de Papper.")
        print("    4. Repeti para todos los topics.")
        print("    5. Vuelve a ejecutar este script.")
        print()
        print("  O podes ingresar los IDs manualmente ahora:")
        for mode in ["papper", "hyperliquid_mainnet", "hyperliquid_testnet"]:
            if mode not in topic_ids:
                try:
                    val = input(f"    topic_id para '{MODO_NOMBRES.get(mode, mode)}': ").strip()
                    if val.isdigit():
                        topic_ids[mode] = int(val)
                except EOFError:
                    pass
        print()

    # 8. Imprimir configuracion para .env
    print("=" * 65)
    print("  AGREGALO A TU ARCHIVO .env")
    print("=" * 65)
    print(f'TELEGRAM_BOT_TOKEN={token}')
    print(f'TELEGRAM_CHAT_ID={chat_id}')
    print(f'TELEGRAM_ENABLED=true')
    print(f'TELEGRAM_MIN_LEVEL=INFO')
    print()
    for mode in ["papper", "hyperliquid_mainnet", "hyperliquid_testnet"]:
        env_key = MODO_ENV_KEYS[mode]
        tid = topic_ids.get(mode)
        if tid:
            print(f"{env_key}={tid}")
        else:
            print(f"# {env_key}=?   (NO DETECTADO)")
    print()
    print("=" * 65)
    print("  Script completado.")
    print("  Copia las lineas TELEGRAM_TOPIC_* a tu archivo .env")
    print("=" * 65)


if __name__ == "__main__":
    main()
