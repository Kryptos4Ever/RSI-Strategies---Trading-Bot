"""Script temporal para inspeccionar updates de Telegram."""
import json, sys
sys.path.insert(0, ".")
from support.setup_telegram_topics import load_dotenv, get_updates

env = load_dotenv()
token = env.get("TELEGRAM_BOT_TOKEN", "")
updates = get_updates(token)
print(f"Total updates: {len(updates)}")
print()
for i, u in enumerate(updates):
    print(f"=== Update #{i} ===")
    print(json.dumps(u, indent=2, ensure_ascii=False))
    print()