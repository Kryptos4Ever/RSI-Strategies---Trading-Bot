#!/usr/bin/env python3
"""
Executor_RSI_Standard.py — Lanzador rápido para la estrategia RSI Standard en vivo
═══════════════════════════════════════════════════════════════════════════════════════════════
Configura y ejecuta la estrategia RSI Standard (Cutler's RSI) (LONG + SHORT) en el entorno de
ejecución en vivo. Acepta argumentos CLI que sobreescriben las constantes de configuración.

Uso:
    python Executor_RSI_Standard.py
    python Executor_RSI_Standard.py --hyperliquid_mainnet
    python Executor_RSI_Standard.py --papper --rsi-period 14 --oversold 30 --overbought 70
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import sys

# Asegurar que el directorio raíz está en el path
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.live_engine import LiveEngine
from risk.risk_manager import build_risk_manager
from state.state_manager import build_state_manager
from support.logger import get_logger
from support.secrets import secrets

log = get_logger("executor_rsi_standard_runner")

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN  ←  EDITAR AQUÍ o pasar por CLI
# ══════════════════════════════════════════════════════════════════════════════

# Entorno de ejecución:
# Opciones: "papper", "hyperliquid_mainnet", "hyperliquid_testnet"
ENVIRONMENT = "papper"

# Modo de operación
# Opciones: "limit_post_only", "limit_gtc"
MODO_OPERACION = "limit_gtc"

# ── Parámetros de capital ───────────────────────────────────────────────────
MAX_POSICIONES      = 3
SLOT_FACTOR         = 1.0

# ── Parámetros RSI ──────────────────────────────────────────────────────────
RSI_PERIOD          = 14
OVERSOLD_THRESHOLD  = 30.0
OVERBOUGHT_THRESHOLD = 70.0
REDUCE_LONG         = 50.0
REDUCE_SHORT        = 50.0

# ══════════════════════════════════════════════════════════════════════════════
# REGISTRO DE ESTRATEGIAS (no editar)
# ══════════════════════════════════════════════════════════════════════════════

STRATEGY_REGISTRY = {
    "rsi_standard": "strategies.rsi_standard:RSIStandardStrategy",
}

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════


def _load_strategy_class(strategy_name: str):
    """Importa dinámicamente una estrategia por su nombre corto."""
    entry = STRATEGY_REGISTRY.get(strategy_name)
    if not entry:
        raise ValueError(
            f"Estrategia '{strategy_name}' no encontrada. "
            f"Opciones: {list(STRATEGY_REGISTRY.keys())}"
        )
    module_path, class_name = entry.split(":")
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _resolve_order_type(modo_operacion: str) -> str:
    """Valida y retorna el tipo de orden. Lanza ValueError si no es reconocido."""
    validos = {"limit_post_only", "limit_gtc", "market"}
    if modo_operacion not in validos:
        raise ValueError(
            f"Modo de operación '{modo_operacion}' no válido. "
            f"Opciones: {sorted(validos)}"
        )
    return modo_operacion


def _build_actors_for_mode(mode: str, max_posiciones: int, slot_factor: float, order_type: str):
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

    from support.secrets import secrets
    symbol = secrets("SYMBOL", "BTCUSDT")

    # Derivar símbolo HL desde SYMBOL (fuente única) quitando sufijo USDT/USDC
    hl_symbol = symbol.upper().replace("USDT", "").replace("USDC", "").replace("USD", "").replace("PERP", "")

    # ── 1. Feed ────────────────────────────────────────────────────────
    if is_hyperliquid:
        if hl_testnet:
            from actors.hyperliquid_testnet.hyperliquid_testnet_feed import HyperliquidWSFeed as FeedClass
        else:
            from actors.hyperliquid_mainnet.hyperliquid_mainnet_feed import HyperliquidWSFeed as FeedClass
        feed = FeedClass()
    else:
        from actors.papper.papper_feed import PapperWSFeed
        feed = PapperWSFeed()

    # ── 2. Wallet ──────────────────────────────────────────────────────
    json_filename = f"live_results_standard_{mode}.json"
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
        hl_leverage = int(secrets("HL_LEVERAGE", "1"))
        ob = HyperliquidOrderBook(
            max_posiciones=max_posiciones,
            commission_pct=float(secrets("COMMISSION_PCT", "0.05")),
            symbol=hl_symbol,       # derivado desde SYMBOL (fuente única)
            leverage=hl_leverage,
            order_type_mode=order_type,   # propaga "limit_gtc" o "limit_post_only"
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


# ══════════════════════════════════════════════════════════════════════════════
# CLI ARGUMENTS
# ══════════════════════════════════════════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(
        description="Executor RSI Standard — Lanzador rápido de la estrategia RSI en vivo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python Executor_RSI_Standard.py\n"
            "  python Executor_RSI_Standard.py --hyperliquid_mainnet\n"
            "  python Executor_RSI_Standard.py --papper --rsi-period 14 --oversold 30 --overbought 70\n"
        ),
    )

    # ── Flags directos de entorno ──────────────────────────────────────
    env_group = parser.add_argument_group("entorno (flags directos)")
    env_group.add_argument("--papper", action="store_true", dest="flag_papper",
                           help="Entorno: papper (paper trading)")
    env_group.add_argument("--hyperliquid_mainnet", action="store_true", dest="flag_hyperliquid_mainnet",
                           help="Entorno: Hyperliquid Perps (real)")
    env_group.add_argument("--hyperliquid_testnet", action="store_true", dest="flag_hyperliquid_testnet",
                           help="Entorno: Hyperliquid Testnet")

    # ── Flags con valor ────────────────────────────────────────────────
    compat_group = parser.add_argument_group("parámetros (flags con valor)")
    compat_group.add_argument("--env", default=None,
                              help="Entorno (alternativa a flag directo)")
    compat_group.add_argument("--modo", default=None,
                              help="Modo operación")
    compat_group.add_argument("--max-pos", type=int, default=None,
                              help="Máx posiciones")
    compat_group.add_argument("--slot-factor", type=float, default=None,
                              help="Slot factor")
    compat_group.add_argument("--rsi-period", type=int, default=None,
                              help="Período RSI")
    compat_group.add_argument("--oversold", type=float, default=None,
                              help="Umbral sobreventa (oversold)")
    compat_group.add_argument("--overbought", type=float, default=None,
                              help="Umbral sobrecompra (overbought)")
    compat_group.add_argument("--reduce-long", type=float, default=None,
                              help="Precio de reducción LONG")
    compat_group.add_argument("--reduce-short", type=float, default=None,
                              help="Precio de reducción SHORT")
    compat_group.add_argument("--timeframe", default=None,
                              help="Temporalidad de las velas (1m, 5m, 15m, 30m, 1h, 4h, 1d...)")
    return parser.parse_args()


# ══════════════════════════════════════════════════════════════════════════════
# EJECUCIÓN PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════


def main() -> None:
    args = parse_args()

    # ── Resolver entorno (flag directo > --env > constante) ────────────────
    env_flags = {
        "papper": args.flag_papper,
        "hyperliquid_mainnet": args.flag_hyperliquid_mainnet,
        "hyperliquid_testnet": args.flag_hyperliquid_testnet,
    }
    env_from_flag = None
    for env_name, activated in env_flags.items():
        if activated:
            env_from_flag = env_name
            break
    environment = env_from_flag or args.env or ENVIRONMENT
    valid_environments = {"papper", "hyperliquid_mainnet", "hyperliquid_testnet"}
    if environment not in valid_environments:
        print(f"✗ Entorno '{environment}' no válido. Opciones: {sorted(valid_environments)}")
        return

    # ── Resolver resto de parámetros ───────────────────────────────────────
    modo_operacion = args.modo or MODO_OPERACION
    max_posiciones = args.max_pos if args.max_pos is not None else MAX_POSICIONES
    slot_factor    = args.slot_factor if args.slot_factor is not None else SLOT_FACTOR
    rsi_period     = args.rsi_period if args.rsi_period is not None else RSI_PERIOD
    oversold       = args.oversold if args.oversold is not None else OVERSOLD_THRESHOLD
    overbought     = args.overbought if args.overbought is not None else OVERBOUGHT_THRESHOLD
    reduce_long    = args.reduce_long if args.reduce_long is not None else REDUCE_LONG
    reduce_short   = args.reduce_short if args.reduce_short is not None else REDUCE_SHORT
    # Temporalidad: CLI > .env > "1h"
    timeframe      = args.timeframe or secrets("TIMEFRAME", "1h")

    # ══════════════════════════════════════════════════════════════════════════
    # 1. Resolver estrategia
    # ══════════════════════════════════════════════════════════════════════════
    print("  → Resolviendo estrategia...")
    strategy_class = _load_strategy_class("rsi_standard")
    strategy = strategy_class(
        rsi_period=rsi_period,
        oversold_threshold=oversold,
        overbought_threshold=overbought,
        reduce_long=reduce_long,
        reduce_short=reduce_short,
        max_positions=max_posiciones,
    )
    print(f"  ✓ Estrategia: {strategy.name}")

    # ══════════════════════════════════════════════════════════════════════════
    # 2. Construir actores (feed, wallet, order_book)
    # ══════════════════════════════════════════════════════════════════════════
    order_type = _resolve_order_type(modo_operacion)
    print(f"  → Construyendo actores para modo: {environment} (order_type={order_type})...")
    actors = _build_actors_for_mode(
        mode=environment,
        max_posiciones=max_posiciones,
        slot_factor=slot_factor,
        order_type=order_type,
    )
    print(f"  ✓ Actores construidos.")

    # ══════════════════════════════════════════════════════════════════════════
    # 3. Construir Risk Manager
    # ══════════════════════════════════════════════════════════════════════════
    print("  → Construyendo Risk Manager...")
    risk = build_risk_manager(
        usd_initial=actors["saldo_inicial"],
        enable_live_controls=True,
    )
    print(f"  ✓ Risk Manager listo.")

    # ══════════════════════════════════════════════════════════════════════════
    # 4. Construir State Manager (solo para logging/auditoría, no restauración)
    # ══════════════════════════════════════════════════════════════════════════
    print("  → Construyendo State Manager...")
    state_path = f"live_results_standard_{environment}.json"
    state = build_state_manager(mode="results", path=state_path)
    prev = state.load_latest()
    if prev and prev.risk_state:
        risk.restore_state(prev.risk_state)
        opened = prev.risk_state.get("circuit_open", False)
        log.info("RiskManager restaurado desde live_results", circuit_open=opened)
        print(f"  ✓ Risk restaurado desde live_results (circuit={opened}).")
    else:
        print(f"  ✓ Sin risk_state previo: RiskManager iniciado desde cero.")
    print(f"  ✓ State Manager listo.")

    # ══════════════════════════════════════════════════════════════════════════
    # 5. Construir Telegram Notifier (usar singleton global)
    # ══════════════════════════════════════════════════════════════════════════
    from notifications.telegram_notifier import get_notifier as get_telegram_notifier
    telegram = get_telegram_notifier()

    # ══════════════════════════════════════════════════════════════════════════
    # 6. Inyectar todo en el Engine
    # ══════════════════════════════════════════════════════════════════════════
    engine = LiveEngine(
        feed=actors["feed"],
        wallet=actors["wallet"],
        ob=actors["ob"],
        risk=risk,
        state=state,
        strategy=strategy,
        telegram=telegram,
        environment=environment,
        symbol=actors["symbol"],
        saldo_inicial=actors["saldo_inicial"],
        commission_pct=actors["commission_pct"],
        order_type=order_type,
        max_posiciones=max_posiciones,
        slot_factor=slot_factor,
        dashboard_port=None,
        candle_interval=timeframe,
    )

    # ══════════════════════════════════════════════════════════════════════════
    # 7. Ejecutar
    # ══════════════════════════════════════════════════════════════════════════
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()