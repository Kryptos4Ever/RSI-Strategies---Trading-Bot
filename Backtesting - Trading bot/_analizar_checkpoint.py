import json, hashlib
from collections import Counter

with open('optimizer_checkpoint_rsi_standard.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
completed = set(data.get('completed_hashes', []))

print(f'Total hashes completados: {len(completed)}')
print(f'Version: {data.get("version")}')
print(f'Updated at: {data.get("updated_at")}')
print(f'Total evaluados: {data.get("total_evaluated")}')
print(f'Top20 entries: {len(data.get("top20", []))}')

PARAM_RANGES = {
    'RSI_PERIOD': [8, 9, 10, 11, 12, 13, 14, 15],
    'OVERSOLD_THRESHOLD': [25.0, 26.0, 27.0, 28.0, 29.0, 30.0],
    'OVERBOUGHT_THRESHOLD': [65.0, 66.0, 67.0, 68.0, 69.0, 70.0],
    'REDUCE_LONG': [45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60],
    'REDUCE_SHORT': [45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60],
    'MAX_POSICIONES': [1, 2, 3],
    'SLOT_FACTOR': [1.0, 1.2, 1.5],
    'MODO_OPERACION': ['limite_gtc', 'limit_post_only'],
}

def _hash_params(params):
    raw = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

keys = list(PARAM_RANGES.keys())
values = list(PARAM_RANGES.values())
combos = []
def cartesian(idx, current):
    if idx == len(keys):
        combo = dict(current)
        combo['_hash'] = _hash_params(combo)
        combos.append(combo)
        return
    k = keys[idx]
    vals = values[idx]
    if k == 'SLOT_FACTOR' and current.get('MAX_POSICIONES') == 1:
        vals = [vals[0]]
    for v in vals:
        current[k] = v
        cartesian(idx + 1, current)

cartesian(0, {})
print(f'Total combinaciones grid: {len(combos)}')

total_by_modo = Counter()
completed_by_modo = Counter()
for c in combos:
    modo = c['MODO_OPERACION']
    total_by_modo[modo] += 1
    if c['_hash'] in completed:
        completed_by_modo[modo] += 1

print()
print('=== Por MODO_OPERACION ===')
for modo in ['limite_gtc', 'limit_post_only']:
    print(f'{modo}: {completed_by_modo[modo]}/{total_by_modo[modo]} completados')

top20 = data.get('top20', [])
print()
print('=== TOP 20 en checkpoint ===')
for i, e in enumerate(top20, 1):
    p = e.get('params', {})
    print(f'#{i}: PNL={e.get("pnl_pct")}% Modo={p.get("MODO_OPERACION")} RSI={p.get("RSI_PERIOD")} OS={p.get("OVERSOLD_THRESHOLD")} OB={p.get("OVERBOUGHT_THRESHOLD")} RL={p.get("REDUCE_LONG")} RS={p.get("REDUCE_SHORT")} MP={p.get("MAX_POSICIONES")} SF={p.get("SLOT_FACTOR")}')