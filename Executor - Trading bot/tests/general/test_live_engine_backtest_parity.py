"""
tests/general/test_live_engine_backtest_parity.py — Paridad Executor vs Backtesting
═══════════════════════════════════════════════════════════════════════════════════
Verifica que la estrategia RSI Wilder es byte-idéntica entre el Executor
y el Backtesting, y que el preview/commit del tick no muta el estado de la estrategia.
"""
from __future__ import annotations

import os
import sys

import pytest

from support.types import Candle, SignalType
from strategies.base_strategy import BaseStrategy
from strategies.rsi_wilder import RSIWilderStrategy
from actors.wallet import MemoryWallet

# Ruta raíz del Executor
EXECUTOR_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Ruta raíz del Backtesting (hermano)
BACKTEST_ROOT = os.path.join(os.path.dirname(EXECUTOR_ROOT), "Backtesting - Trading bot")


def _make_wallet():
    return MemoryWallet(usd_initial=1000.0, max_posiciones=3, slot_factor=1.0)


def _candles(count: int = 8) -> list[Candle]:
    """Genera velas sintéticas de 1h con precios incrementales."""
    return [
        Candle(ts=1_700_000_000 + i * 3600, open=100.0 + i * 3.0,
               high=100.0 + i * 3.0 + 2.0, low=100.0 + i * 3.0 - 1.0,
               close=100.0 + i * 3.0, volume=1.0)
        for i in range(count)
    ]


def _next_candle() -> Candle:
    """Vela adicional para el preview."""
    return Candle(ts=1_700_000_000 + 8 * 3600, open=130.0, high=134.0,
                  low=128.0, close=132.0, volume=1.0)


class TestStrategyFileParity:
    """Verifica que los archivos de estrategia son byte-idénticos entre repos."""

    def test_rsi_strategy_file_identical(self):
        executor_file = os.path.join(EXECUTOR_ROOT, "strategies", "rsi_wilder.py")
        backtest_file = os.path.join(BACKTEST_ROOT, "strategies", "rsi_wilder.py")
        assert os.path.exists(executor_file), f"No existe {executor_file}"
        assert os.path.exists(backtest_file), f"No existe {backtest_file}"
        with open(executor_file, "rb") as f:
            executor_content = f.read()
        with open(backtest_file, "rb") as f:
            backtest_content = f.read()
        assert executor_content == backtest_content, (
            "El archivo rsi_wilder.py del Executor NO es idéntico al del Backtesting"
        )

    def test_base_strategy_file_identical(self):
        executor_file = os.path.join(EXECUTOR_ROOT, "strategies", "base_strategy.py")
        backtest_file = os.path.join(BACKTEST_ROOT, "strategies", "base_strategy.py")
        assert os.path.exists(executor_file)
        assert os.path.exists(backtest_file)
        with open(executor_file, "rb") as f:
            executor_content = f.read()
        with open(backtest_file, "rb") as f:
            backtest_content = f.read()
        assert executor_content == backtest_content


class TestPreviewCommitParity:
    """Verifica que preview/commit del tick no muta el estado de la estrategia."""

    def _make_strategy(self):
        return RSIWilderStrategy(rsi_period=14, max_positions=3)

    def test_preview_does_not_mutate_strategy(self):
        from engine.live_engine import _preview_open_signals, _commit_closed_candle

        strat = self._make_strategy()
        strat.load_warmup(_candles())
        strat.on_start(None)

        # Snapshot del estado interno
        import copy
        rsi_before = copy.deepcopy(strat._rsi_engine.__dict__)
        state_before = copy.deepcopy(strat.__dict__)

        candle = _next_candle()
        preview_signals = _preview_open_signals(strat, candle, _make_wallet())

        # El estado no debe mutar tras el preview
        state_after = strat.__dict__
        assert state_before.keys() == state_after.keys()
        for k in state_before:
            if k == "_rsi_engine":
                # El objeto RSIEngine es restaurado por deepcopy (referencia distinta),
                # pero su estado interno debe ser idéntico.
                assert state_before[k].__dict__ == state_after[k].__dict__, f"Estado mutado en {k}"
            else:
                assert state_before[k] == state_after[k], f"Estado mutado en {k}"
        # El RSI interno no debe cambiar tras el preview
        assert strat._rsi_engine.__dict__ == rsi_before

        # El commit sí avanza el estado (una vez)
        commit_signals = _commit_closed_candle(strat, candle, _make_wallet())
        assert strat.candles_seen >= 1

    def test_preview_returns_list_of_signals(self):
        from engine.live_engine import _preview_open_signals

        strat = self._make_strategy()
        strat.load_warmup(_candles())
        strat.on_start(None)

        signals = _preview_open_signals(strat, _next_candle(), _make_wallet())
        assert isinstance(signals, list)
        assert all(hasattr(s, "signal_type") for s in signals)
