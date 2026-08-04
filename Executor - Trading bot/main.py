#!/usr/bin/env python3
"""
main.py — Lanzador Único del Executor - Trading bot
═══════════════════════════════════════════════════
Orquestador puro: construye todos los actores y los inyecta en LiveEngine.

Uso:
  python main.py --mode papper
  python main.py --mode hyperliquid_mainnet --max-posiciones 5
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import os

# Asegurar que el directorio raíz está en el path
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.live_engine import LiveEngine
from risk.risk_manager import build_risk_manager
from state.state_manager import build_state_manager
from notifications.telegram_notifier import TelegramNotifier
from support.secrets import secrets
from strategies.rsi_wilder import RSIWilderStrategy


def build_actors_for_mode(mode: str, max_posiciones: int, slot_factor: float, order_type: str):
    """
    Construye feed, wallet y order_book según el modo de ejecución.
    Retorna un dict con todos los actores + metadata.
    """
    mode = mode.lower()
    valid_modes = {"papper", "hyperliquid_mainnet", "hyperliquid_testnet"}
    if mode not in valid_modes:
        raise ValueError(f"Entorno '{mode}' no soportado. Opciones: {sorted(valid_modes)}")

    is_hyperliquid = mode in ("hyperliquid_mainnet", "hyperliquid_testnet")
    hl_testnet = (mode == "hyperliquid_testnet")
    is_papper = (mode == "papper")
    is_real = not is_papper

    symbol = secrets("SYMBOL", "BTCUSDT")

    # ── 1. Feed ────────────────────────────────────────────────────────
    if is_hyperliquid:
        if hl_testnet:
            from actors.hyperliquid_testnet.hyperliquid_testnet_feed import HyperliquidWSFeed as FeedClass
        else:
            from actors.hyperliquid_mainnet.hyperliquid_mainnet_feed import HyperliquidWSFeed as FeedClass
        feed = FeedClass()
    else:
        # papper
        from actors.papper.papper_feed import PapperWSFeed
        feed = PapperWSFeed()

    # ── 2. Wallet ──────────────────────────────────────────────────────
    json_filename = f"live_results_wilder_{mode}.json"
    if is_papper:
        from actors.papper.papper_wallet import JSONWallet
        wallet = JSONWallet(
            usd_initial=float(secrets("PAPPER_SALDO_INICIAL", secrets("SALDO_USD_INICIAL", "1000.0"))),
            max_posiciones=max_posiciones,
            json_path=json_filename,
            slot_factor=slot_factor,
            environment=mode,
            symbol=symbol,
            collateral_currency=secrets("PAPPER_COLLATERAL_CURRENCY", "USD"),
        )
    elif is_hyperliquid:
        if hl_testnet:
            from actors.hyperliquid_testnet.hyperliquid_testnet_wallet import HyperliquidWallet
        else:
            from actors.hyperliquid_mainnet.hyperliquid_mainnet_wallet import HyperliquidWallet
        wallet = HyperliquidWallet.from_account(
            max_posiciones=max_posiciones,
            json_path=json_filename,
            slot_factor=slot_factor,
        )
    # ── Actualizar saldo_inicial con autodescubrimiento ─────────────────
    if is_real and hasattr(wallet, '_usd_initial') and wallet._usd_initial > 0:
        saldo_inicial = wallet._usd_initial
    else:
        saldo_inicial = float(secrets("PAPPER_SALDO_INICIAL", secrets("SALDO_USD_INICIAL", "1000.0")))

    # ── 3. OrderBook ───────────────────────────────────────────────────
    commission_pct = float(secrets("PAPPER_COMMISSION_PCT", secrets("COMMISSION_PCT", "0.1")))
    if is_papper:
        from actors.papper.papper_order_book import SimulatedOrderBook
        ob = SimulatedOrderBook(
            commission_pct=commission_pct,
            max_posiciones=max_posiciones,
        )
    elif is_hyperliquid:
        if hl_testnet:
            from actors.hyperliquid_testnet.hyperliquid_testnet_order_book import HyperliquidOrderBook
        else:
            from actors.hyperliquid_mainnet.hyperliquid_mainnet_order_book import HyperliquidOrderBook
        hl_symbol = secrets("HL_SYMBOL", "BTC")
        hl_leverage = int(secrets("HL_LEVERAGE", "1"))
        ob = HyperliquidOrderBook(
            max_posiciones=max_posiciones,
            commission_pct=float(secrets("COMMISSION_PCT", "0.05")),
            symbol=hl_symbol,
            leverage=hl_leverage,
            order_type_mode=order_type,
        )
    # ── Asegurar _usd_initial > 0 ─────────────────────────────────────
    if hasattr(wallet, '_usd_initial') and wallet._usd_initial <= 0:
        wallet._usd_initial = max(saldo_inicial, 100.0)

    return {
        "feed": feed,
        "wallet": wallet,
        "ob": ob,
        "symbol": symbol,
        "saldo_inicial": saldo_inicial,
        "commission_pct": commission_pct,
    }


def load_history_candles(mode: str, symbol: str, strategy, warm_up_count: int = 50):
    """
    Carga velas históricas para warm-up desde REST.
    Retorna lista de Candle o [] si falla.
    """
    mode = mode.lower()
    is_hyperliquid = mode in ("hyperliquid_mainnet", "hyperliquid_testnet")
    hl_testnet = (mode == "hyperliquid_testnet")

    if is_hyperliquid:
        if hl_testnet:
            from actors.hyperliquid_testnet.hyperliquid_testnet_feed import HyperliquidRESTFeed
        else:
            from actors.hyperliquid_mainnet.hyperliquid_mainnet_feed import HyperliquidRESTFeed
        rest_feed = HyperliquidRESTFeed()
    else:
        from actors.papper.papper_feed import PapperRESTFeed
        rest_feed = PapperRESTFeed()

    import time
    try:
        candles_list = rest_feed.get_candles(
            int(time.time()) - warm_up_count * 3600,
            int(time.time()),
            symbol,
        )
        print(f"  ✓ Cargadas {len(candles_list)} velas históricas vía REST.")
        return candles_list
    except Exception as e:
        print(f"  ⚠ Error al cargar históricos: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(
        description="Trading Bot Executor — Estrategia RSI Wilder (LONG + SHORT)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--mode", default="papper",
        choices=["papper", "hyperliquid_mainnet", "hyperliquid_testnet"],
        help="Entorno de ejecución",
    )
    parser.add_argument(
        "--order-type", default="limit_gtc",
        choices=["market", "limit_post_only", "limit_gtc"],
        help="Tipo de órdenes",
    )
    parser.add_argument("--max-posiciones", type=int, default=3,
                        help="Máximo de posiciones simultáneas")
    parser.add_argument("--slot-factor", type=float, default=1.0,
                        help="Factor de slot (1.0 = 100%% del slot disponible)")
    parser.add_argument("--rsi-period", type=int, default=14,
                        help="Período RSI")
    parser.add_argument("--oversold", type=float, default=30.0,
                        help="Umbral sobreventa")
    parser.add_argument("--overbought", type=float, default=70.0,
                        help="Umbral sobrecompra")
    parser.add_argument("--reduce-long", type=float, default=50.0,
                        help="Precio de reducción LONG")
    parser.add_argument("--reduce-short", type=float, default=50.0,
                        help="Precio de reducción SHORT")
    parser.add_argument("--dashboard-port", type=int, default=None,
                        help="Puerto del dashboard (opcional, si no se especifica usa el preasignado del modo en .env)")
    args = parser.parse_args()

    # ═══════════════════════════════════════════════════════════════════
    # 1. Resolver estrategia
    # ═══════════════════════════════════════════════════════════════════
    print("  → Resolviendo estrategia...")
    strategy = RSIWilderStrategy(
        rsi_period=args.rsi_period,
        oversold_threshold=args.oversold,
        overbought_threshold=args.overbought,
        reduce_long=args.reduce_long,
        reduce_short=args.reduce_short,
        max_positions=args.max_posiciones,
    )
    print(f"  ✓ Estrategia: {strategy.name}")

    # ═══════════════════════════════════════════════════════════════════
    # 2. Construir actores (feed, wallet, order_book)
    # ═══════════════════════════════════════════════════════════════════
    print(f"  → Construyendo actores para modo: {args.mode}...")
    actors = build_actors_for_mode(
        mode=args.mode,
        max_posiciones=args.max_posiciones,
        slot_factor=args.slot_factor,
        order_type=args.order_type,
    )
    print(f"  ✓ Actores construidos.")

    # ═══════════════════════════════════════════════════════════════════
    # 3. Construir Risk Manager
    # ═══════════════════════════════════════════════════════════════════
    print("  → Construyendo Risk Manager...")
    risk = build_risk_manager(
        usd_initial=actors["saldo_inicial"],
        enable_live_controls=True,
    )
    print(f"  ✓ Risk Manager listo.")

    # ═══════════════════════════════════════════════════════════════════
    # 4. Construir State Manager + restaurar risk desde live_results
    # ═══════════════════════════════════════════════════════════════════
    print("  → Construyendo State Manager...")
    state_path = f"live_results_wilder_{args.mode}.json"
    state = build_state_manager(mode="results", path=state_path)
    prev = state.load_latest()
    if prev and prev.risk_state:
        risk.restore_state(prev.risk_state)
        opened = prev.risk_state.get("circuit_open", False)
        from support.logger import get_logger
        log = get_logger("main")
        log.info("RiskManager restaurado desde live_results", circuit_open=opened)
        print(f"  ✓ Risk restaurado desde live_results (circuit={opened}).")
    else:
        print(f"  ✓ Sin checkpoint previo: RiskManager iniciado desde cero.")
    print(f"  ✓ State Manager listo.")

    # ═══════════════════════════════════════════════════════════════════
    # 5. Cargar velas históricas para warm-up
    # ═══════════════════════════════════════════════════════════════════
    print("  → Cargando velas históricas para warm-up...")
    history_candles = load_history_candles(
        mode=args.mode,
        symbol=actors["symbol"],
        strategy=strategy,
    )

    # ═══════════════════════════════════════════════════════════════════
    # 6. Construir Telegram Notifier
    # ═══════════════════════════════════════════════════════════════════
    telegram = TelegramNotifier()

    # ═══════════════════════════════════════════════════════════════════
    # 7. Inyectar todo en el Engine
    # ═══════════════════════════════════════════════════════════════════
    engine = LiveEngine(
        feed=actors["feed"],
        wallet=actors["wallet"],
        ob=actors["ob"],
        risk=risk,
        state=state,
        strategy=strategy,
        telegram=telegram,
        environment=args.mode,
        symbol=actors["symbol"],
        saldo_inicial=actors["saldo_inicial"],
        commission_pct=actors["commission_pct"],
        order_type=args.order_type,
        max_posiciones=args.max_posiciones,
        slot_factor=args.slot_factor,
        dashboard_port=args.dashboard_port,
    )

    # ═══════════════════════════════════════════════════════════════════
    # 8. Ejecutar
    # ═══════════════════════════════════════════════════════════════════
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()