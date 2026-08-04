"""
test_telegram_topics.py — Envia un mensaje de prueba a cada Topic de Telegram
═══════════════════════════════════════════════════════════════════════════════
Uso:
    python -m support.test_telegram_topics

Requiere:
    - TELEGRAM_BOT_TOKEN configurado en .env
    - TELEGRAM_CHAT_ID configurado en .env
    - TELEGRAM_TOPIC_* configurados en .env
"""
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: Falta la libreria 'requests'.")
    print("  Instalala con: pip install requests")
    sys.exit(1)


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


def main():
    """Envía un mensaje de prueba a cada Topic de Telegram configurado en .env."""
    env = load_dotenv()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID deben estar en .env")
        sys.exit(1)

    # Topics configurados
    topics = {}
    for mode in ("papper", "testnet", "real", "hyperliquid", "hl-testnet"):
        key = f"TELEGRAM_TOPIC_{mode.upper()}"
        val = env.get(key, "")
        if val:
            try:
                topics[mode.upper()] = int(val)
            except ValueError:
                pass

    print(f"Token: {token[:10]}...{token[-5:]}")
    print(f"Chat ID: {chat_id}")
    print(f"Topics detectados: {len(topics)}/5")
    print()

    # Enviar a cada topic
    todos_ok = True
    for mode, topic_id in topics.items():
        text = (
            f"Prueba de Topic - {mode}\n"
            f"Si ves esto, el topic_id={topic_id} funciona correctamente."
        )
        payload = {
            "chat_id": chat_id,
            "text": text,
            "message_thread_id": topic_id,
        }
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
                timeout=10,
            )
            data = resp.json()
            if data.get("ok"):
                print(f"  [OK] {mode:12s} (topic_id={topic_id})")
            else:
                print(f"  [ERR] {mode:12s} (topic_id={topic_id}) -> {data.get('description', 'desconocido')}")
                todos_ok = False
        except Exception as e:
            print(f"  [ERR] {mode:12s} (topic_id={topic_id}) -> {e}")
            todos_ok = False

    # Enviar al chat principal (sin topic)
    print()
    text_principal = "Prueba Chat Principal (sin Topic)\nEste mensaje esta en el chat general del grupo."
    payload = {
        "chat_id": chat_id,
        "text": text_principal,
    }
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            print("  [OK] Chat principal (sin topic)")
        else:
            print(f"  [ERR] Chat principal -> {data.get('description', 'desconocido')}")
            todos_ok = False
    except Exception as e:
        print(f"  [ERR] Chat principal -> {e}")
        todos_ok = False

    print()
    if todos_ok:
        print("OK. Todos los mensajes enviados.")
        print("Revisa Telegram para confirmar que cada mensaje")
        print("esta en el Topic correcto.")
    else:
        print("Algunos mensajes fallaron. Revisa los errores arriba.")
        sys.exit(1)


if __name__ == "__main__":
    main()