"""
tests/general/test_strategies.py — Tests de estrategias
═════════════════════════════════════════════════════════
Prueba BaseStrategy y la estrategia RSI Wilder (LONG + SHORT).
"""
from __future__ import annotations

import pytest

from support.types import Candle, Signal, SignalType, PositionDirection, SignalSide
from strategies.base_strategy import BaseStrategy
from strategies.rsi_wilder import RSIWilderStrategy
from actors.wallet import MemoryWallet


def _make_candle(ts: int, close: float) -> Candle:
    return Candle(ts=ts, open=close, high=close, low=close, close=close, volume=1.0)


def _make_wallet():
    return MemoryWallet(usd_initial=1000.0, max_posiciones=3, slot_factor=1.0)



class TestRSIWilderStrategy:
    def _make_strategy(self, **kwargs):
        return RSIWilderStrategy(**kwargs)

    def _warmup(self, strat, n=20, price=100.0, step=0.0):
        candles = [_make_candle(1000 + i, price + step * i) for i in range(n)]
        strat.load_warmup(candles)

    def test_initial_state(self):
        s = self._make_strategy()
        assert s.name == "RSI_Wilder"
        assert s._rsi_period == 14
        assert s._oversold == 30.0
        assert s._overbought == 70.0

    def test_warmup_flat_rsi_neutral_no_signals(self):
        """Warmup plano → RSI neutral → no emite señales."""
        s = self._make_strategy()
        self._warmup(s, n=20, price=100.0, step=0.0)
        sigs = s.on_candle(_make_candle(1020, 100.0), _make_wallet())
        assert all(sig.signal_type == SignalType.HOLD for sig in sigs)

    def test_processes_candles_returns_list(self):
        """La estrategia procesa velas y devuelve una lista de señales válidas."""
        s = self._make_strategy()
        self._warmup(s, n=20, price=100.0, step=1.0)
        sigs = s.on_candle(_make_candle(1020, 120.0), _make_wallet())
        assert isinstance(sigs, list)
        assert all(hasattr(sig, "signal_type") for sig in sigs)
        assert all(hasattr(sig, "side") for sig in sigs)
        for sig in sigs:
            assert isinstance(sig.side, SignalSide)

    def test_side_derivation_from_signal_type(self):
        """El side se deriva correctamente del signal_type."""
        # OPEN_SHORT → SELL (vender para abrir short)
        s = Signal(signal_type=SignalType.OPEN_SHORT, price=100.0)
        assert s.side == SignalSide.SELL
        # CLOSE_SHORT → BUY (comprar para cerrar short)
        s2 = Signal(signal_type=SignalType.CLOSE_SHORT, price=100.0)
        assert s2.side == SignalSide.BUY
        # OPEN_LONG → BUY
        s3 = Signal(signal_type=SignalType.OPEN_LONG, price=100.0)
        assert s3.side == SignalSide.BUY
        # REDUCE_LONG → SELL
        s4 = Signal(signal_type=SignalType.REDUCE_LONG, price=100.0)
        assert s4.side == SignalSide.SELL

    def test_describe(self):
        s = self._make_strategy()
        d = s.describe()
        assert d["estrategia"] == "RSI_Wilder"
        assert d["rsi_period"] == 14

    def test_get_default_config(self):
        cfg = RSIWilderStrategy.get_default_config()
        assert cfg["rsi_period"] == 14
        assert cfg["oversold_threshold"] == 30.0
        assert cfg["overbought_threshold"] == 70.0
        assert cfg["reduce_long"] == 50.0
        assert cfg["reduce_short"] == 50.0