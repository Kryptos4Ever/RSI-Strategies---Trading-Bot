import sqlite3
import json
import os

_DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_DB_DIR, 'btc_1h.db')
OUTPUT_PATH = os.path.join(_DB_DIR, 'btc_1h.json')

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute('SELECT timestamp AS ts, datetime, open, high, low, close FROM btc_1h ORDER BY timestamp')
rows = cursor.fetchall()

data = [
    {
        'ts': row[0],
        'datetime': row[1],
        'open': row[2],
        'high': row[3],
        'low': row[4],
        'close': row[5]
    }
    for row in rows
]

with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)

conn.close()

print(f"Exportacion completada: {len(data)} registros -> {OUTPUT_PATH}")
print(f"Tamaño del archivo: {os.path.getsize(OUTPUT_PATH) / 1024 / 1024:.2f} MB")