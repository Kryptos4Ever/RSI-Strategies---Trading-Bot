"""
Backtest.py — Lanzador Único (Director de Orquesta)
════════════════════════════════════════════════════
Configura y ejecuta un backtest para cualquier estrategia registrada.

Uso:
    python Backtest.py --strategy rsi_wilder
    python Backtest.py --strategy rsi_standard --rsi-period 7
"""
from __future__ import annotations

import argparse
import importlib
import os

import config_local as CL
from actors.clock        import LocalClock
from actors.order_book   import SimulatedLimitPostOnlyOrderBook, SimulatedLimitGTCOrderBook
from actors.price_feed   import SQLiteFeed, resolve_db_path
from actors.wallet       import JSONWallet
from engine.backtest_engine import BacktestEngine
from risk.risk_manager   import build_risk_manager
from state.state_manager import MemoryStateManager
from support.logger      import get_logger

log = get_logger("backtest_runner")


# ══════════════════════════════════════════════════════════════════════════════
# REGISTRO DE ESTRATEGIAS
# ══════════════════════════════════════════════════════════════════════════════
# Mapa nombre → ruta del módulo Python (module_path:ClassName)
# Para añadir una nueva estrategia, solo agregar una entrada aquí.
STRATEGY_REGISTRY = {
    "rsi_wilder": "strategies.rsi_wilder:RSIWilderStrategy",
    "rsi_standard": "strategies.rsi_standard:RSIStandardStrategy",
}


def _load_strategy_class(strategy_name: str):
    """
    Carga la clase de estrategia desde STRATEGY_REGISTRY.
    Usa import dinámico para permitir agregar estrategias sin modificar este archivo.
    """
    entry = STRATEGY_REGISTRY.get(strategy_name)
    if not entry:
        raise ValueError(
            f"Estrategia '{strategy_name}' no encontrada. "
            f"Opciones: {list(STRATEGY_REGISTRY.keys())}"
        )

    module_path, class_name = entry.split(":")
    module = importlib.import_module(module_path)
    strategy_class = getattr(module, class_name)
    return strategy_class


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest Trading Bot — Lanzador Único",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── Estrategia ───────────────────────────────────────────────────────────
    parser.add_argument("--strategy", default="rsi_wilder",
                        choices=list(STRATEGY_REGISTRY.keys()),
                        help="Nombre de la estrategia a ejecutar")

    # ── Parámetros generales ─────────────────────────────────────────────────
    parser.add_argument("--max-posiciones", type=int, default=3,
                        help="Máximo número de posiciones simultáneas")
    parser.add_argument("--slot-factor", type=float, default=1.0,
                        help="Factor de progresión de slots (1.0 = igualitario)")
    parser.add_argument("--modo-operacion", default="limit_post_only",
                        choices=["limite_gtc", "limit_post_only"],
                        help="Modo de ejecución de órdenes (limite_gtc, limit_post_only)")

    # ── Configuración general ────────────────────────────────────────────────
    parser.add_argument("--db-path", default=None,
                        help="Ruta a la base primaria (default: DB/btc_{primary_timeframe}.db)")
    parser.add_argument("--db-secondary-path", default=None,
                        help="Ruta a la base secundaria (default: DB/btc_{secondary_timeframe}.db)")
    parser.add_argument("--start", default=CL.FECHA_INICIO,
                        help="Fecha de inicio del backtest (YYYY-MM-DD)")
    parser.add_argument("--end", default=CL.FECHA_FIN,
                        help="Fecha de fin del backtest (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=CL.SALDO_USD_INICIAL,
                        help="Capital inicial en USDT")
    parser.add_argument("--commission", type=float, default=CL.COMMISSION_PCT,
                        help="Comisión en porcentaje")
    parser.add_argument("--output", default=CL.RESULTS_JSON,
                        help="Archivo JSON de salida")
    parser.add_argument("--symbol", default="BTCUSDT",
                        help="Símbolo del activo")
    parser.add_argument("--primary-timeframe", default=CL.PRIMARY_TIMEFRAME,
                        help="Temporalidad principal (1h, 15m, 5m)")
    parser.add_argument("--secondary-timeframe", default=CL.SECONDARY_TIMEFRAME,
                        help="Temporalidad secundaria (15m, 5m, 1m) o vacío")

    # ── Cargar estrategia y sus parámetros por defecto ───────────────────────
    basic_args, _ = parser.parse_known_args()
    strategy_name = basic_args.strategy

    strategy_class = _load_strategy_class(strategy_name)
    strategy_defaults = strategy_class.get_default_config()

    # Generar argumentos CLI dinámicamente desde los defaults de la estrategia
    for param_name, param_default in strategy_defaults.items():
        param_type = type(param_default) if param_default is not None else str
        parser.add_argument(
            f"--{param_name.replace('_', '-')}",
            type=param_type,
            default=param_default,
            help=f"Parámetro de estrategia '{param_name}'",
        )

    # Parsear todos los argumentos (incluyendo los de la estrategia)
    args = parser.parse_args()

    # Extraer parámetros específicos de la estrategia
    strategy_params = {
        k: v for k, v in vars(args).items()
        if k in strategy_defaults
    }

    # ── 1. Feed ──────────────────────────────────────────────────────────────
    db_primary_path = args.db_path or resolve_db_path(args.primary_timeframe)
    feed = SQLiteFeed(db_path=db_primary_path)

    # ── 2. Clock ─────────────────────────────────────────────────────────────
    clock = LocalClock(feed, start=args.start, end=args.end, symbol=args.symbol)

    # ── 3. Wallet ────────────────────────────────────────────────────────────
    if os.path.exists(args.output):
        os.remove(args.output)

    wallet = JSONWallet(
        json_path=args.output,
        usd_initial=args.capital,
        max_posiciones=args.max_posiciones,
        slot_factor=args.slot_factor,
    )

    # ── 4. OrderBook ─────────────────────────────────────────────────────────
    modo_operacion = strategy_params.get("modo_operacion", args.modo_operacion)
    if modo_operacion == "limit_post_only":
        ob = SimulatedLimitPostOnlyOrderBook(
            commission_pct=args.commission,
            max_posiciones=args.max_posiciones,
        )
    elif modo_operacion == "limite_gtc":
        ob = SimulatedLimitGTCOrderBook(
            commission_pct=args.commission,
            max_posiciones=args.max_posiciones,
        )
    else:
        ob = SimulatedLimitPostOnlyOrderBook(
            commission_pct=args.commission,
            max_posiciones=args.max_posiciones,
        )

    # ── 5. Risk ──────────────────────────────────────────────────────────────
    risk = build_risk_manager(usd_initial=args.capital)

    # ── 6. State ─────────────────────────────────────────────────────────────
    state = MemoryStateManager()

    # ── 7. Estrategia ────────────────────────────────────────────────────────
    strategy = strategy_class(**strategy_params)

    # ── 8. Engine ────────────────────────────────────────────────────────────
    engine = BacktestEngine(
        clock, wallet, ob, risk, state, feed,
        usd_initial=args.capital,
        fecha_inicio=args.start,
        fecha_fin=args.end,
        commission_pct=args.commission,
        results_json=args.output,
        max_posiciones=args.max_posiciones,
        primary_timeframe=args.primary_timeframe,
        secondary_timeframe=args.secondary_timeframe or None,
        modo_operacion=modo_operacion,
    )

    # ── 9. Ejecutar ──────────────────────────────────────────────────────────
    engine.print_config(strategy.name)

    # Mostrar parámetros específicos de la estrategia
    if hasattr(strategy, 'describe'):
        params = strategy.describe()
        strategy_params_display = {k: v for k, v in params.items()
                                   if k not in ['name', 'version']}
        if strategy_params_display:
            print("-" * 72)
            print("  Parámetros de Estrategia:")
            for key, value in strategy_params_display.items():
                print(f"    {key:25s}: {value}")

    print("-" * 72)

    summary = engine.run(strategy)
    engine.print_summary(summary)


if __name__ == "__main__":
    main()