"""
tests/general/test_types.py — Tests de tipos compartidos
═══════════════════════════════════════════════════════════
Prueba Candle, Signal, SignalType, PositionDirection y SignalSide.
"""
from __future__ import annotations

import pytest

from support.types import (
    Candle,
    Signal,
    SignalType,
    SignalSide,
    PositionDirection,
    HOLD,
    HOLD_LIST,
)


class TestCandle:
    def test_body_positive(self):
        c = Candle(ts=1, open=100.0, high=110.0, low=90.0, close=105.0, volume=1.0)
        assert c.body == 5.0

    def test_body_negative(self):
        c = Candle(ts=1, open=100.0, high=110.0, low=90.0, close=95.0, volume=1.0)
        assert c.body == -5.0

    def test_total_range(self):
        c = Candle(ts=1, open=100.0, high=110.0, low=90.0, close=105.0, volume=1.0)
        assert c.total_range == 20.0


class TestSignal:
    def test_signal_type_buy_side(self):
        s = Signal(signal_type=SignalType.OPEN_LONG, price=50000.0, reason="test", ts=1_700_000_000)
        assert s.side == SignalSide.BUY

    def test_signal_type_sell_side(self):
        s = Signal(signal_type=SignalType.OPEN_SHORT, price=50000.0)
        assert s.side == SignalSide.SELL

    def test_is_actionable(self):
        s = Signal(signal_type=SignalType.OPEN_LONG, price=50000.0)
        assert s.is_actionable is True

    def test_hold_not_actionable(self):
        assert HOLD.is_actionable is False

    def test_to_order_side_buy(self):
        s = Signal(signal_type=SignalType.OPEN_LONG, price=50000.0)
        assert s.to_order_side() == "BUY"

    def test_to_order_side_sell(self):
        s = Signal(signal_type=SignalType.OPEN_SHORT, price=50000.0)
        assert s.to_order_side() == "SELL"

    def test_to_order_side_hold(self):
        assert HOLD.to_order_side() is None

    def test_side_derivation_long(self):
        assert Signal(signal_type=SignalType.OPEN_LONG, price=1).side == SignalSide.BUY
        assert Signal(signal_type=SignalType.ADD_LONG, price=1).side == SignalSide.BUY
        assert Signal(signal_type=SignalType.REDUCE_LONG, price=1).side == SignalSide.SELL
        assert Signal(signal_type=SignalType.CLOSE_LONG, price=1).side == SignalSide.SELL

    def test_side_derivation_short(self):
        assert Signal(signal_type=SignalType.OPEN_SHORT, price=1).side == SignalSide.SELL
        assert Signal(signal_type=SignalType.ADD_SHORT, price=1).side == SignalSide.SELL
        assert Signal(signal_type=SignalType.REDUCE_SHORT, price=1).side == SignalSide.BUY
        assert Signal(signal_type=SignalType.CLOSE_SHORT, price=1).side == SignalSide.BUY


class TestPositionDirection:
    def test_values(self):
        assert PositionDirection.LONG.value == "LONG"
        assert PositionDirection.SHORT.value == "SHORT"
        assert PositionDirection.NONE.value == "NONE"


class TestConstants:
    def test_hold(self):
        assert HOLD.signal_type == SignalType.HOLD

    def test_hold_list(self):
        assert len(HOLD_LIST) == 1
        assert HOLD_LIST[0].signal_type == SignalType.HOLD