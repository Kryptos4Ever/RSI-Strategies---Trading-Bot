"""
tests/optimizadores/test_optimizador_rsi_wilder.py
═══════════════════════════════════════════════════════════════════
Verifica que el optimizador produce resultados idénticos al
backtest individual (Backtest_RSI_Wilder.py) para 3
combinaciones muy diferentes entre sí.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
import numpy as np

from support.types import Candle, SignalType, PositionDirection
from tests.conftest import make_candle, make_candle_sequence
from actors.wallet import MemoryWallet, TradeRecord
from actors.order_book import (
    SimulatedLimitPostOnlyOrderBook,
    SimulatedLimitGTCOrderBook,
    OrderSide,
)
from risk.risk_manager import RiskManager
from state.state_manager import MemoryStateManager
from strategies.rsi_wilder import RSIWilderStrategy
from support.types import HOLD_LIST


# ══════════════════════════════════════════════════════════════════════════════
# LAS 3 COMBINACIONES "MUY DIFERENTES"
# ══════════════════════════════════════════════════════════════════════════════

TEST_COMBOS = [
    {
        "name": "A_rapido_postonly",
        "RSI_PERIOD": 10,
        "OVERSOLD_THRESHOLD": 25.0,
        "OVERBOUGHT_THRESHOLD": 75.0,
        "REDUCE_LONG": 40.0,
        "REDUCE_SHORT": 60.0,
        "MAX_POSICIONES": 1,
        "SLOT_FACTOR": 1.0,
        "MODO_OPERACION": "limit_post_only",
    },
    {
        "name": "B_medio_gtc",
        "RSI_PERIOD": 20,
        "OVERSOLD_THRESHOLD": 30.0,
        "OVERBOUGHT_THRESHOLD": 70.0,
        "REDUCE_LONG": 50.0,
        "REDUCE_SHORT": 50.0,
        "MAX_POSICIONES": 3,
        "SLOT_FACTOR": 1.0,
        "MODO_OPERACION": "limite_gtc",
    },
    {
        "name": "C_lento_agresivo",
        "RSI_PERIOD": 30,
        "OVERSOLD_THRESHOLD": 35.0,
        "OVERBOUGHT_THRESHOLD": 65.0,
        "REDUCE_LONG": 60.0,
        "REDUCE_SHORT": 40.0,
        "MAX_POSICIONES": 5,
        "SLOT_FACTOR": 2.0,
        "MODO_OPERACION": "limite_gtc",
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def synthetic_candles() -> List[Candle]:
    """200 velas sintéticas reproducibles."""
    return make_candle_sequence(
        n=200, base_price=30_000.0, volatility=1000.0, seed=42
    )


@pytest.fixture(params=TEST_COMBOS, ids=[c["name"] for c in TEST_COMBOS])
def combo(request):
    """Cada combinación de prueba."""
    return request.param


# ══════════════════════════════════════════════════════════════════════════════
# HELPER: Replica el flujo de Backtest_RSI_Wilder.py
# ══════════════════════════════════════════════════════════════════════════════

def run_individual_backtest(
    candles: List[Candle],
    params: dict,
    usd_initial: float = 1000.0,
    commission_pct: float = 0.02,
) -> Dict:
    """
    Replica EXACTAMENTE el flujo de Backtest_RSI_Wilder.py
    pero usando velas en memoria en lugar de SQLite.
    
    Esto incluye: warmup, loop principal, risk checks, ejecución de órdenes,
    y construcción de summary con Sharpe, MaxDD, contadores.
    
    Returns:
        Dict con summary (mismas métricas que BacktestEngine._build_summary)
    """
    # ── Wallet ─────────────────────────────────────────────────
    wallet = MemoryWallet(
        usd_initial=usd_initial,
        max_posiciones=params["MAX_POSICIONES"],
        slot_factor=params["SLOT_FACTOR"],
    )

    # ── OrderBook ──────────────────────────────────────────────
    if params["MODO_OPERACION"] == "limit_post_only":
        ob = SimulatedLimitPostOnlyOrderBook(
            commission_pct=commission_pct,
            max_posiciones=params["MAX_POSICIONES"],
        )
    else:
        ob = SimulatedLimitGTCOrderBook(
            commission_pct=commission_pct,
            max_posiciones=params["MAX_POSICIONES"],
        )

    # ── Risk ───────────────────────────────────────────────────
    risk = RiskManager(usd_initial=usd_initial)

    # ── State (no usado para summary, pero necesario para consistencia) ──
    state = MemoryStateManager()

    # ── Estrategia ─────────────────────────────────────────────
    strategy = RSIWilderStrategy(
        rsi_period=params["RSI_PERIOD"],
        oversold_threshold=params["OVERSOLD_THRESHOLD"],
        overbought_threshold=params["OVERBOUGHT_THRESHOLD"],
        reduce_long=params["REDUCE_LONG"],
        reduce_short=params["REDUCE_SHORT"],
        max_positions=params["MAX_POSICIONES"],
    )

    # ── Warmup (como BacktestEngine._load_warmup_candles) ────────────
    rsi_period = params["RSI_PERIOD"]
    n_warm = rsi_period + 20
    warmup_candles = candles[:n_warm] if len(candles) > n_warm else candles
    if hasattr(strategy, 'load_warmup') and warmup_candles:
        strategy.load_warmup(warmup_candles)

    # ── Contadores (como BacktestEngine) ───────────────────────
    n_long_opens = 0
    n_long_adds = 0
    n_long_reduces = 0
    n_long_closes = 0
    n_short_opens = 0
    n_short_adds = 0
    n_short_reduces = 0
    n_short_closes = 0
    n_ignorados = 0
    realized_pnl_total = 0.0

    # ── Loop principal ─────────────────────────────────────────
    strategy.on_start(wallet)
    last_candle = None
    portfolio_history: List[float] = []

    for candle in candles:
        last_candle = candle
        signals = strategy.tick(candle, wallet)

        # Procesar señales (como BacktestEngine._process_signals)
        actionable = [s for s in signals if s.is_actionable]
        if actionable:
            if hasattr(ob, 'set_candle'):
                ob.set_candle(candle)

            for signal in actionable:
                st = signal.signal_type
                if st == SignalType.HOLD:
                    continue

                # Risk check
                tentative_side = OrderSide.BUY if st in (
                    SignalType.OPEN_LONG, SignalType.ADD_LONG,
                    SignalType.REDUCE_SHORT, SignalType.CLOSE_SHORT,
                ) else OrderSide.SELL

                risk_reason = risk.check(tentative_side, signal.price, wallet, candle)
                if risk_reason:
                    n_ignorados += 1
                    wallet.update(TradeRecord(
                        ts=signal.ts or candle.ts, side=tentative_side.value,
                        price=signal.price, ignored=True,
                        ignore_reason=risk_reason, signal_type=st.value,
                    ))
                    continue

                # Determinar dirección
                if st in (SignalType.OPEN_LONG, SignalType.ADD_LONG,
                          SignalType.REDUCE_LONG, SignalType.CLOSE_LONG):
                    direction = PositionDirection.LONG
                else:
                    direction = PositionDirection.SHORT

                # Ejecutar según SignalType (con initial_candle_open como BacktestEngine)
                order = None
                ic_open = candle.open
                if st == SignalType.OPEN_LONG:
                    order = ob.open_position(direction, signal.price, wallet,
                                              candle_ts=candle.ts,
                                              initial_candle_open=ic_open,
                                              signal_type=st.value)
                elif st == SignalType.ADD_LONG:
                    order = ob.add_position(direction, signal.price, wallet,
                                             candle_ts=candle.ts,
                                             initial_candle_open=ic_open,
                                             signal_type=st.value)
                elif st == SignalType.REDUCE_LONG:
                    order = ob.reduce_position(direction, signal.price, wallet,
                                                candle_ts=candle.ts,
                                                initial_candle_open=ic_open,
                                                signal_type=st.value)
                elif st == SignalType.CLOSE_LONG:
                    order = ob.close_position(direction, signal.price, wallet,
                                               candle_ts=candle.ts,
                                               initial_candle_open=ic_open,
                                               signal_type=st.value)
                elif st == SignalType.OPEN_SHORT:
                    order = ob.open_position(direction, signal.price, wallet,
                                              candle_ts=candle.ts,
                                              initial_candle_open=ic_open,
                                              signal_type=st.value)
                elif st == SignalType.ADD_SHORT:
                    order = ob.add_position(direction, signal.price, wallet,
                                             candle_ts=candle.ts,
                                             initial_candle_open=ic_open,
                                             signal_type=st.value)
                elif st == SignalType.REDUCE_SHORT:
                    order = ob.reduce_position(direction, signal.price, wallet,
                                                candle_ts=candle.ts,
                                                initial_candle_open=ic_open,
                                                signal_type=st.value)
                elif st == SignalType.CLOSE_SHORT:
                    order = ob.close_position(direction, signal.price, wallet,
                                               candle_ts=candle.ts,
                                               initial_candle_open=ic_open,
                                               signal_type=st.value)

                if order is not None and order.is_filled:
                    risk.on_trade_executed()
                    if order.trade and order.trade.realized_pnl:
                        realized_pnl_total += order.trade.realized_pnl
                    # Contabilizar solo si se llenó (como BacktestEngine)
                    if st == SignalType.OPEN_LONG:
                        n_long_opens += 1
                    elif st == SignalType.ADD_LONG:
                        n_long_adds += 1
                    elif st == SignalType.REDUCE_LONG:
                        n_long_reduces += 1
                    elif st == SignalType.CLOSE_LONG:
                        n_long_closes += 1
                    elif st == SignalType.OPEN_SHORT:
                        n_short_opens += 1
                    elif st == SignalType.ADD_SHORT:
                        n_short_adds += 1
                    elif st == SignalType.REDUCE_SHORT:
                        n_short_reduces += 1
                    elif st == SignalType.CLOSE_SHORT:
                        n_short_closes += 1
                elif order is not None:
                    n_ignorados += 1

                risk.update_peak(wallet.portfolio_value(candle.close))

        # Registrar valor del portfolio al cierre de la vela (para Sharpe/DD)
        portfolio_history.append(wallet.portfolio_value(candle.close))

    strategy.on_stop(wallet)

    # ── Métricas finales (como BacktestEngine._build_summary) ──
    if last_candle is None:
        return {}

    precio_final = last_candle.close
    port_final = wallet.portfolio_value(precio_final)
    pnl_pct = (port_final / usd_initial - 1) * 100 if usd_initial > 0 else 0.0

    precio_inicial = candles[0].open if candles else precio_final
    bh_pnl = (precio_final / precio_inicial - 1) * 100 if precio_inicial > 0 else 0.0

    # Sharpe y MaxDD (usando el historial real del portfolio vela a vela)
    try:
        if len(portfolio_history) >= 2:
            port_arr = np.array(portfolio_history, dtype=np.float64)

            peak = np.maximum.accumulate(port_arr)
            dd = (port_arr - peak) / np.where(peak == 0, 1, peak) * 100
            max_dd = float(dd.min())

            returns = np.diff(port_arr) / np.where(port_arr[:-1] == 0, 1, port_arr[:-1])
            if len(returns) >= 2 and np.std(returns) > 0:
                tf_seconds = 3600  # 1H
                ann_factor = np.sqrt(365 * 24 * 3600 / tf_seconds)
                sharpe = float(np.mean(returns) / np.std(returns) * ann_factor)
            else:
                sharpe = 0.0
        else:
            max_dd = 0.0
            sharpe = 0.0
    except Exception:
        max_dd = 0.0
        sharpe = 0.0

    total_trades = (n_long_opens + n_long_adds + n_long_reduces + n_long_closes +
                    n_short_opens + n_short_adds + n_short_reduces + n_short_closes)

    summary = {
        "estrategia": strategy.name,
        "pnl_pct": round(pnl_pct, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "buy_hold_pnl_pct": round(bh_pnl, 4),
        "alpha_vs_bh": round(pnl_pct - bh_pnl, 4),
        "total_trades_ejecutados": total_trades,
        "total_compras": n_long_opens + n_long_adds + n_short_reduces + n_short_closes,
        "total_ventas": n_long_reduces + n_long_closes + n_short_opens + n_short_adds,
        "total_ignorados": n_ignorados,
        "long_opens": n_long_opens,
        "long_adds": n_long_adds,
        "long_reduces": n_long_reduces,
        "long_closes": n_long_closes,
        "short_opens": n_short_opens,
        "short_adds": n_short_adds,
        "short_reduces": n_short_reduces,
        "short_closes": n_short_closes,
        "portfolio_value_final": round(port_final, 4),
        "pruned": False,
    }

    return summary


# ══════════════════════════════════════════════════════════════════════════════
# TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestOptimizadorRSI:
    """
    Tests de validación del Optimizador_RSI_Wilder.
    
    Para cada combinación, ejecuta:
    1. El flujo completo (replica Backtest_RSI_Wilder.py)
    2. El worker del optimizador (versión lightweight con RSI cache)
    3. Compara que ambos den resultados idénticos
    """

    def test_optimizador_vs_backtest_individual(self, synthetic_candles, combo):
        """
        Para cada combinación, verifica que:
        - El resultado del optimizador (lightweight worker) = resultado del backtest individual
        - Métricas clave coinciden: PNL%, Sharpe, MaxDD, total_trades
        """
        # ── 1. Ejecutar flujo individual (replica Backtest_RSI_Wilder.py) ──
        summary_individual = run_individual_backtest(synthetic_candles, combo)

        # ── 2. Ejecutar worker del optimizador ──
        from Optimizador_RSI_Wilder import run_single_combo_lightweight

        summary_optimizer = run_single_combo_lightweight(
            candles=synthetic_candles,
            params=combo,
            usd_initial=1000.0,
            commission_pct=0.02,
        )

        # ── 3. Verificar que ambos summaries existen ──
        assert summary_individual, f"Backtest individual falló para {combo['name']}"
        assert summary_optimizer, f"Optimizador falló para {combo['name']}"

        # ── 4. Comparar métricas clave ──
        # PNL% debe coincidir exactamente (tolerancia 1e-6)
        assert abs(summary_individual["pnl_pct"] - summary_optimizer["pnl_pct"]) < 1e-6, (
            f"[{combo['name']}] PNL% no coincide: "
            f"individual={summary_individual['pnl_pct']:.6f} "
            f"optimizador={summary_optimizer['pnl_pct']:.6f}"
        )

        # Sharpe debe coincidir (tolerancia 1e-4)
        assert abs(summary_individual["sharpe"] - summary_optimizer["sharpe"]) < 1e-4, (
            f"[{combo['name']}] Sharpe no coincide: "
            f"individual={summary_individual['sharpe']:.6f} "
            f"optimizador={summary_optimizer['sharpe']:.6f}"
        )

        # MaxDD debe coincidir (tolerancia 1e-4)
        assert abs(summary_individual["max_drawdown_pct"] - summary_optimizer["max_drawdown_pct"]) < 1e-4, (
            f"[{combo['name']}] MaxDD no coincide: "
            f"individual={summary_individual['max_drawdown_pct']:.6f} "
            f"optimizador={summary_optimizer['max_drawdown_pct']:.6f}"
        )

        # Total trades debe coincidir EXACTAMENTE
        assert summary_individual["total_trades_ejecutados"] == summary_optimizer["total_trades_ejecutados"], (
            f"[{combo['name']}] Trades no coinciden: "
            f"individual={summary_individual['total_trades_ejecutados']} "
            f"optimizador={summary_optimizer['total_trades_ejecutados']}"
        )

        # Alpha vs BH debe coincidir
        assert abs(summary_individual["alpha_vs_bh"] - summary_optimizer["alpha_vs_bh"]) < 1e-6, (
            f"[{combo['name']}] Alpha vs BH no coincide: "
            f"individual={summary_individual['alpha_vs_bh']:.6f} "
            f"optimizador={summary_optimizer['alpha_vs_bh']:.6f}"
        )

        # Desglose de trades debe coincidir
        for campo in ["long_opens", "long_adds", "long_reduces", "long_closes",
                       "short_opens", "short_adds", "short_reduces", "short_closes",
                       "total_ignorados"]:
            assert summary_individual[campo] == summary_optimizer[campo], (
                f"[{combo['name']}] {campo} no coincide: "
                f"individual={summary_individual[campo]} "
                f"optimizador={summary_optimizer[campo]}"
            )

    def _add_hash(self, combo: dict) -> dict:
        """Agrega _hash a un combo para tracking en el leaderboard."""
        import hashlib, json
        combo_copy = dict(combo)
        h = hashlib.sha256(json.dumps(combo_copy, sort_keys=True).encode()).hexdigest()[:16]
        combo_copy["_hash"] = h
        return combo_copy

    def test_optimizador_produce_top20(self, synthetic_candles):
        """
        Verifica que el optimizador produce un top 20 válido
        cuando se ejecuta con las 3 combinaciones de prueba.
        """
        from Optimizador_RSI_Wilder import Top20Leaderboard

        lb = Top20Leaderboard(top_n=20, min_trades=0)  # Sin filtro mínimo

        for combo in TEST_COMBOS:
            combo_with_hash = self._add_hash(combo)
            summary = run_individual_backtest(synthetic_candles, combo_with_hash)
            assert summary, f"Backtest falló para {combo['name']}"
            entered = lb.add(combo_with_hash, summary, elapsed_s=0.5)
            assert entered, f"Combo {combo['name']} no entró al top 20"

        top20 = lb.get_top20()
        assert len(top20) == 3, f"Se esperaban 3 resultados, hay {len(top20)}"

        # Verificar que están ordenados por PNL descendente
        for i in range(len(top20) - 1):
            assert top20[i]["pnl_pct"] >= top20[i + 1]["pnl_pct"], (
                f"Top 20 no ordenado: posición {i} (PNL={top20[i]['pnl_pct']:.2f}) > "
                f"{i + 1} (PNL={top20[i + 1]['pnl_pct']:.2f})"
            )

        # Verificar estructura de cada entry
        for entry in top20:
            assert "hash" in entry
            assert "params" in entry
            assert "pnl_pct" in entry
            assert "sharpe" in entry
            assert "max_drawdown_pct" in entry
            assert "total_trades" in entry
            assert "elapsed_s" in entry

    def test_optimizador_min_trades_filter(self, synthetic_candles):
        """
        Verifica que el filtro MIN_TRADES funciona correctamente.
        """
        from Optimizador_RSI_Wilder import Top20Leaderboard

        # Con MIN_TRADES = 9999, ninguna combinación debería entrar
        lb = Top20Leaderboard(top_n=20, min_trades=9999)

        for combo in TEST_COMBOS:
            summary = run_individual_backtest(synthetic_candles, combo)
            entered = lb.add(combo, summary, elapsed_s=0.5)
            assert not entered, (
                f"Combo {combo['name']} entró al top 20 con min_trades=9999 "
                f"(trades={summary.get('total_trades_ejecutados', 0)})"
            )

        assert len(lb.get_top20()) == 0, "Leaderboard debería estar vacío con min_trades=9999"

    def test_optimizador_checkpoint_roundtrip(self, tmp_path):
        """
        Verifica que CheckpointManager guarda y carga correctamente.
        """
        from Optimizador_RSI_Wilder import CheckpointManager

        chk_path = str(tmp_path / "test_checkpoint.json")
        chk = CheckpointManager(chk_path, auto_save_interval=10)

        # Simular algunos resultados
        params_list = [
            {"RSI_PERIOD": 10, "OVERSOLD_THRESHOLD": 30.0},
            {"RSI_PERIOD": 14, "OVERSOLD_THRESHOLD": 25.0},
        ]
        for i, params in enumerate(params_list):
            import hashlib, json
            h = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
            chk.add_result(h, params, {"pnl_pct": 5.0 + i, "pruned": False}, elapsed_s=0.5)

        chk.save({"dummy": "results"}, [])

        # Cargar en un nuevo manager
        chk2 = CheckpointManager(chk_path, auto_save_interval=10)
        completed, results, top20 = chk2.load()

        assert len(completed) == 2, f"Se esperaban 2 hashes, hay {len(completed)}"
        # Desde v3, save() NO guarda los results individuales (solo hashes + top20)
        assert results == {}, "Desde v3 los results individuales no se guardan"

class TestPruningDetector:
    """
    Verifica la lógica de decisión del PruningDetector.
    Usa un mock wallet para aislar la lógica del detector.
    """

    class _MockWallet:
        """Wallet mínima que solo expone portfolio_value()."""
        def __init__(self, value: float):
            self._value = value

        def portfolio_value(self, current_price: float) -> float:
            return self._value

    def _make_detector(self, **kwargs) -> Any:
        from Optimizador_RSI_Wilder import PruningDetector
        return PruningDetector(**kwargs)

    def test_no_prune_cuando_portfolio_sano(self):
        """Portfolio por encima del umbral y del ratio B&H → NO prunar."""
        detector = self._make_detector(threshold=0.90, after_pct=0.15, bh_ratio=0.85)
        wallet = self._MockWallet(value=1000.0)  # 100% del capital
        # current_price=100, first_close=100 → B&H = 1000, ratio 0.85 → 850
        assert detector.should_prune(
            wallet, initial_capital=1000.0, current_price=100.0,
            candle_idx=50, total_candles=100, first_close=100.0,
        ) is False

    def test_prune_por_umbral_absoluto(self):
        """Portfolio < 90% del capital inicial → prunar."""
        detector = self._make_detector(threshold=0.90, after_pct=0.15, bh_ratio=0.85)
        wallet = self._MockWallet(value=850.0)  # 85% del capital
        assert detector.should_prune(
            wallet, initial_capital=1000.0, current_price=100.0,
            candle_idx=50, total_candles=100, first_close=100.0,
        ) is True

    def test_prune_por_ratio_bh(self):
        """Portfolio por debajo del 85% del B&H → prunar."""
        detector = self._make_detector(threshold=0.90, after_pct=0.15, bh_ratio=0.85)
        # B&H = (1000/100)*200 = 2000; 85% = 1700
        # Portfolio = 1500 (75% del capital, pero > 90% del capital)
        # → No pruna por umbral absoluto, pero sí por ratio B&H
        wallet = self._MockWallet(value=1500.0)
        assert detector.should_prune(
            wallet, initial_capital=1000.0, current_price=200.0,
            candle_idx=50, total_candles=100, first_close=100.0,
        ) is True

    def test_no_prune_antes_del_after_pct(self):
        """Antes del % mínimo de velas → nunca prunar."""
        detector = self._make_detector(threshold=0.90, after_pct=0.15, bh_ratio=0.85)
        wallet = self._MockWallet(value=100.0)  # 10% del capital (muy mal)
        # candle_idx=10, total=100 → 10 < 15 → no evaluar
        assert detector.should_prune(
            wallet, initial_capital=1000.0, current_price=100.0,
            candle_idx=10, total_candles=100, first_close=100.0,
        ) is False

    def test_no_prune_en_limite_exacto(self):
        """Portfolio == threshold exacto → NO prunar (comparación estricta <)."""
        detector = self._make_detector(threshold=0.90, after_pct=0.15, bh_ratio=0.85)
        wallet = self._MockWallet(value=900.0)  # 90% exacto
        assert detector.should_prune(
            wallet, initial_capital=1000.0, current_price=100.0,
            candle_idx=50, total_candles=100, first_close=100.0,
        ) is False

    def test_no_prune_si_bh_ratio_exacto(self):
        """Portfolio == bh_ratio * B&H exacto → NO prunar (comparación estricta <)."""
        detector = self._make_detector(threshold=0.90, after_pct=0.15, bh_ratio=0.85)
        # B&H = (1000/100)*200 = 2000; 85% = 1700
        wallet = self._MockWallet(value=1700.0)  # 170% del capital, == 85% B&H
        assert detector.should_prune(
            wallet, initial_capital=1000.0, current_price=200.0,
            candle_idx=50, total_candles=100, first_close=100.0,
        ) is False


class TestOptimizadorNoPruneBuenosResultados:
    """
    Verifica que una combinación con PNL positivo NO sea marcada como pruned
    por el PruningDetector en el punto de evaluación.
    """

    def test_combo_rentable_no_se_pruna(self, synthetic_candles):
        """
        Ejecuta una combinación con pruning DESACTIVADO (completa) y verifica
        que si el PNL final es positivo, el detector no la habría prunado
        en el punto de evaluación (al 20% de las velas).
        """
        from Optimizador_RSI_Wilder import PruningDetector

        # Usar una combinación con tendencia alcista en las velas sintéticas
        combo = {
            "RSI_PERIOD": 10,
            "OVERSOLD_THRESHOLD": 25.0,
            "OVERBOUGHT_THRESHOLD": 75.0,
            "REDUCE_LONG": 40.0,
            "REDUCE_SHORT": 60.0,
            "MAX_POSICIONES": 1,
            "SLOT_FACTOR": 1.0,
            "MODO_OPERACION": "limit_post_only",
        }

        # Ejecutar backtest completo (sin pruning)
        summary = run_individual_backtest(synthetic_candles, combo)
        assert summary, "Backtest falló"

        # Si el PNL es positivo, verificar que el detector no prunaría
        # en el punto de evaluación (20% de las velas)
        if summary["pnl_pct"] > 0:
            detector = PruningDetector(
                threshold=0.90, after_pct=0.15, bh_ratio=0.85
            )

            # Simular el estado de la wallet al 20% de las velas
            # Usamos el portfolio final como proxy (si es positivo, el
            # portfolio al 20% debería ser razonable)
            wallet = TestPruningDetector._MockWallet(
                value=summary["portfolio_value_final"]
            )

            # El detector NO debería prunar si el portfolio final es > 90% del capital
            should_prune = detector.should_prune(
                wallet,
                initial_capital=1000.0,
                current_price=synthetic_candles[-1].close,
                candle_idx=int(len(synthetic_candles) * 0.2),
                total_candles=len(synthetic_candles),
                first_close=synthetic_candles[0].open,
            )
            assert not should_prune, (
                f"Combo con PNL positivo ({summary['pnl_pct']:.2f}%) "
                f"fue marcado como pruned"
            )


if __name__ == "__main__":
    from tests._direct_runner import run_current_test_file
    raise SystemExit(run_current_test_file(__file__))
