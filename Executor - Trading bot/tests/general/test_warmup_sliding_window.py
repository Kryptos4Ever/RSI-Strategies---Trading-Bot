"""
tests/general/test_warmup_sliding_window.py — Tests de warm-up de estrategia
═══════════════════════════════════════════════════════════════════════════════
Verifica que la estrategia RSI Wilder se inicializa correctamente con
warm-up y que el preview no muta el estado.
"""
from __future__ import annotations

import pytest

from support.types import Candle, SignalType
from strategies.rsi_wilder import RSIWilderStrategy
from actors.wallet import MemoryWallet


def _make_candle(ts: int, close: float) -> Candle:
    return Candle(ts=ts, open=close, high=close, low=close, close=close, volume=1.0)


def _make_wallet():
    return MemoryWallet(usd_initial=1000.0, max_posiciones=3, slot_factor=1.0)


class TestRSIWarmup:
    def _make_strategy(self, **kwargs):
        return RSIWilderStrategy(**kwargs)

    def test_warmup_flat_initializes_rsi(self):
        """Con warmup plano, el RSI se estabiliza y no emite señales en vela neutral."""
        s = self._make_strategy()
        candles = [_make_candle(1000 + i, 100.0) for i in range(20)]
        s.load_warmup(candles)
        s.on_start(None)

        # RSI debe estar inicializado (no None)
        assert s._rsi_engine.value is not None

        # Vela neutral no emite señales
        sigs = s.on_candle(_make_candle(1020, 100.0), _make_wallet())
        assert all(sig.signal_type == SignalType.HOLD for sig in sigs)

    def test_warmup_uptrend_returns_valid_signals(self):
        """Warmup con tendencia alcista procesa velas y devuelve señales válidas."""
        s = self._make_strategy()
        candles = [_make_candle(1000 + i, 100.0 + i * 2.0) for i in range(20)]
        s.load_warmup(candles)
        s.on_start(None)

        ts = 1020
        price = 140.0
        for _ in range(10):
            price += 2.0
            sigs = s.on_candle(_make_candle(ts, price), _make_wallet())
            ts += 1
            assert isinstance(sigs, list)
            assert all(hasattr(sig, "signal_type") for sig in sigs)

    def test_warmup_downtrend_returns_valid_signals(self):
        """Warmup con tendencia bajista procesa velas y devuelve señales válidas."""
        s = self._make_strategy()
        candles = [_make_candle(1000 + i, 100.0 - i * 2.0) for i in range(20)]
        s.load_warmup(candles)
        s.on_start(None)

        ts = 1020
        price = 60.0
        for _ in range(10):
            price -= 2.0
            sigs = s.on_candle(_make_candle(ts, price), _make_wallet())
            ts += 1
            assert isinstance(sigs, list)
            assert all(hasattr(sig, "signal_type") for sig in sigs)

    def test_preview_does_not_mutate(self):
        """El preview del tick no debe mutar el estado de la estrategia."""
        from engine.live_engine import _preview_open_signals
        import copy

        s = self._make_strategy()
        candles = [_make_candle(1000 + i, 100.0 + i * 2.0) for i in range(20)]
        s.load_warmup(candles)
        s.on_start(None)

        # Snapshot del RSI antes del preview (usar deepcopy del estado interno)
        rsi_before = copy.deepcopy(s._rsi_engine.__dict__)
        state_before = copy.deepcopy(s.__dict__)
        _preview_open_signals(s, _make_candle(1020, 140.0), _make_wallet())

        state_after = s.__dict__
        assert state_before.keys() == state_after.keys()
        for k in state_before:
            if k == "_rsi_engine":
                # El objeto RSIEngine es restaurado por deepcopy (referencia distinta),
                # pero su estado interno debe ser idéntico.
                assert state_before[k].__dict__ == state_after[k].__dict__, f"Estado mutado en {k}"
            else:
                assert state_before[k] == state_after[k], f"Estado mutado en {k}"
        # El RSI interno no debe cambiar tras el preview
        assert s._rsi_engine.__dict__ == rsi_before
