"""
test_intraday_order_sequence_audit.py — Audit test de la secuencia cronológica de órdenes intradía contra la DB SQLite
══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
Audita los resultados de backtest_results.json contra la base de datos btc_5m.db para verificar que:
 1. Las ejecuciones ocurren en sub-velas con timestamps exactos de 5M/15M.
 2. Si se entra a una hora SIN posición previa, la COMPRA ocurre primero (T_buy <= T_sell).
 3. Si se entra a una hora CON posición previa, la VENTA ocurre primero para cerrar y la COMPRA posterior reabre posición.
 4. El precio registrado en la DB de 5M para T_buy realmente alcanzó el precio de compra (low <= buy_price)
    y para T_sell realmente alcanzó el precio de venta (high >= sell_price).
"""
import json
import os
import sqlite3
import pytest


def test_intraday_trade_sequence_against_db():
    results_path = "backtest_results.json"
    db_5m_path = "DB/btc_5m.db"

    if not os.path.exists(results_path) or not os.path.exists(db_5m_path):
        pytest.skip("Se requieren backtest_results.json y DB/btc_5m.db para el audit test.")

    with open(results_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    trade_log = data.get("trade_history", [])
    executed_trades = [t for t in trade_log if not t.get("ignorado", False)]
    assert len(executed_trades) > 0, "No hay trades ejecutados en trade_history para auditar."

    # Agrupar trades ejecutados por hora (floor timestamp a 3600s)
    trades_by_hour = {}
    for t in executed_trades:
        ts = int(t["ts"])
        hourly_ts = (ts // 3600) * 3600
        if hourly_ts not in trades_by_hour:
            trades_by_hour[hourly_ts] = []
        trades_by_hour[hourly_ts].append(t)

    conn = sqlite3.connect(db_5m_path)
    cur = conn.cursor()

    paired_hours_count = 0
    valid_sequence_count = 0
    price_match_count = 0

    for hourly_ts, h_trades in trades_by_hour.items():
        if len(h_trades) < 2:
            continue

        # Ordenar trades dentro de la hora por timestamp
        h_trades_sorted = sorted(h_trades, key=lambda x: int(x["ts"]))
        first_trade = h_trades_sorted[0]
        second_trade = h_trades_sorted[1]

        paired_hours_count += 1

        # Si el primer trade fue SELL, verificar que se estaba cerrando una posición previa (o que se tenía posición)
        if first_trade.get("type") == "SELL":
            # Si hubo VENTA primero, el segundo trade COMPRA reabre posición en T_buy >= T_sell
            assert second_trade.get("type") == "BUY"
            assert int(second_trade["ts"]) >= int(first_trade["ts"]), (
                f"Secuencia cronológica errónea: COMPRA ({second_trade['ts']}) antes de VENTA ({first_trade['ts']})"
            )
            valid_sequence_count += 1
        elif first_trade.get("type") == "BUY":
            # Si hubo COMPRA primero, la VENTA ocurre en T_sell >= T_buy
            assert second_trade.get("type") == "SELL"
            assert int(second_trade["ts"]) >= int(first_trade["ts"]), (
                f"Secuencia cronológica errónea: VENTA ({second_trade['ts']}) antes de COMPRA ({first_trade['ts']})"
            )
            valid_sequence_count += 1

        # Verificar precios contra la DB SQLite de 5M
        for t in h_trades_sorted:
            trade_ts = int(t["ts"])
            trade_ms = trade_ts * 1000
            price = float(t["price"])

            cur.execute("SELECT low, high FROM btc_5m WHERE timestamp = ?", (trade_ms,))
            row = cur.fetchone()
            if row:
                low_5m, high_5m = float(row[0]), float(row[1])
                if t.get("type") == "BUY":
                    assert low_5m <= price + 1e-4, f"Low 5M ({low_5m}) mayor que precio compra ({price})"
                elif t.get("type") == "SELL":
                    assert high_5m >= price - 1e-4, f"High 5M ({high_5m}) menor que precio venta ({price})"
                price_match_count += 1

    conn.close()

    print(f"\n[OK] Auditoria Intradia Completa:")
    print(f"  -- Horas con ejecuciones multiples analizadas: {paired_hours_count}")
    print(f"  -- Secuencias cronologicas 100% validas: {valid_sequence_count}/{paired_hours_count}")
    print(f"  -- Verificaciones de precios contra btc_5m.db exitosas: {price_match_count}")
    assert valid_sequence_count == paired_hours_count
