"""
Backtest_RSI_Wilder.py — Lanzador específico para RSI Wilder
═══════════════════════════════════════════════════════════════════════════════
Configura y ejecuta un backtest para la estrategia RSI Wilder
con un selector simple de parámetros al inicio del archivo.

Los parámetros de temporalidad, fechas, capital, comisión y salida se toman
directamente de config_local.py.

Uso:
    python Backtest_RSI_Wilder.py
"""
from __future__ import annotations

import os

import config_local as CL
from actors.clock        import LocalClock
from actors.order_book   import SimulatedLimitPostOnlyOrderBook, SimulatedLimitGTCOrderBook
from actors.price_feed   import SQLiteFeed, resolve_db_path
from actors.wallet       import JSONWallet
from engine.backtest_engine import BacktestEngine
from risk.risk_manager   import build_risk_manager
from state.state_manager import MemoryStateManager
from strategies.rsi_wilder import RSIWilderStrategy
from support.logger      import get_logger

log = get_logger("backtest_rsi_wilder_runner")


# ══════════════════════════════════════════════════════════════════════════════
# SELECTOR DE PARÁMETROS — Editar aquí antes de ejecutar
# ══════════════════════════════════════════════════════════════════════════════

# ── Parámetros de la estrategia ──────────────────────────────────────────────
RSI_PERIOD            = 13
OVERSOLD_THRESHOLD    = 27
OVERBOUGHT_THRESHOLD  = 67
REDUCE_LONG           = 49
REDUCE_SHORT          = 46
MAX_POSICIONES        = 3
SLOT_FACTOR           = 1.5
MODO_OPERACION        = "limite_gtc"   # "limit_post_only" o "limite_gtc"

# ── Los siguientes parámetros se toman directamente de config_local.py:
#    PRIMARY_TIMEFRAME, SECONDARY_TIMEFRAME,
#    FECHA_INICIO, FECHA_FIN,
#    SALDO_USD_INICIAL, COMMISSION_PCT, RESULTS_JSON, SYMBOL
# ══════════════════════════════════════════════════════════════════════════════


def main() -> None:
    # ── 1. Feed ──────────────────────────────────────────────────────────────
    db_primary_path = resolve_db_path(CL.PRIMARY_TIMEFRAME)
    if not os.path.exists(db_primary_path):
        raise FileNotFoundError(
            f"Base de datos no encontrada: {db_primary_path}\n"
            f"Ejecute el script de descarga correspondiente a {CL.PRIMARY_TIMEFRAME}"
        )
    feed = SQLiteFeed(db_path=db_primary_path)

    # ── 2. Clock ─────────────────────────────────────────────────────────────
    clock = LocalClock(feed, start=CL.FECHA_INICIO, end=CL.FECHA_FIN,
                       symbol=CL.SYMBOL)

    # ── 3. Wallet ────────────────────────────────────────────────────────────
    if os.path.exists(CL.RESULTS_JSON):
        os.remove(CL.RESULTS_JSON)

    wallet = JSONWallet(
        json_path=CL.RESULTS_JSON,
        usd_initial=CL.SALDO_USD_INICIAL,
        max_posiciones=MAX_POSICIONES,
        slot_factor=SLOT_FACTOR,
    )

    # ── 4. OrderBook ─────────────────────────────────────────────────────────
    if MODO_OPERACION == "limit_post_only":
        ob = SimulatedLimitPostOnlyOrderBook(
            commission_pct=CL.COMMISSION_PCT,
            max_posiciones=MAX_POSICIONES,
        )
    else:
        ob = SimulatedLimitGTCOrderBook(
            commission_pct=CL.COMMISSION_PCT,
            max_posiciones=MAX_POSICIONES,
        )

    # ── 5. Risk ──────────────────────────────────────────────────────────────
    risk = build_risk_manager(usd_initial=CL.SALDO_USD_INICIAL)

    # ── 6. State ─────────────────────────────────────────────────────────────
    state = MemoryStateManager()

    # ── 7. Estrategia ────────────────────────────────────────────────────────
    strategy = RSIWilderStrategy(
        rsi_period=RSI_PERIOD,
        oversold_threshold=OVERSOLD_THRESHOLD,
        overbought_threshold=OVERBOUGHT_THRESHOLD,
        reduce_long=REDUCE_LONG,
        reduce_short=REDUCE_SHORT,
        max_positions=MAX_POSICIONES,
    )

    # ── 8. Engine ────────────────────────────────────────────────────────────
    engine = BacktestEngine(
        clock, wallet, ob, risk, state, feed,
        usd_initial=CL.SALDO_USD_INICIAL,
        fecha_inicio=CL.FECHA_INICIO,
        fecha_fin=CL.FECHA_FIN,
        commission_pct=CL.COMMISSION_PCT,
        results_json=CL.RESULTS_JSON,
        max_posiciones=MAX_POSICIONES,
        primary_timeframe=CL.PRIMARY_TIMEFRAME,
        secondary_timeframe=CL.SECONDARY_TIMEFRAME or None,
        modo_operacion=MODO_OPERACION,
    )
    # ── 9. Ejecutar ──────────────────────────────────────────────────────────
    engine.print_config(strategy.name)

    print("-" * 72)
    print("  Parámetros de Estrategia:")
    for key, value in strategy.describe().items():
        if key != "estrategia":
            print(f"    {key:25s}: {value}")
    print(f"  Modo Operación  : {MODO_OPERACION}")
    print(f"  Slot Factor     : {SLOT_FACTOR}")
    print("-" * 72)

    summary = engine.run(strategy)
    engine.print_summary(summary)


if __name__ == "__main__":
    main()