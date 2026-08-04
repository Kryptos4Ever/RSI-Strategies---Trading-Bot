"""
tests/actors/test_clock.py — Smoke tests para LocalClock
=======================================================
Cubre: actors/clock.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from actors.clock import LocalClock
from support.types import Candle


class MockFeed:
    """Feed simulado para testear LocalClock."""
    def __init__(self, candles):
        self._candles = candles
    def get_candles(self, start, end, symbol="BTCUSDT"):
        return self._candles


class TestLocalClock:
    """Tests básicos de LocalClock."""

    def test_init_and_properties(self):
        """Verificar que LocalClock se inicializa correctamente."""
        candles = [
            Candle(ts=1_700_000_000, open=100.0, high=101.0, low=99.0, close=100.5, volume=100.0)
        ]
        feed = MockFeed(candles)
        clock = LocalClock(feed, start="2024-01-01", end="2024-01-02")

        assert isinstance(clock, LocalClock)
        assert clock.total_candles == 0
        assert clock.progress_pct == 0.0
        clock.tick()
        assert clock.total_candles == 1
        assert clock.progress_pct == 100.0

    def test_tick_returns_candles(self):
        """tick() debe retornar las velas en orden y luego None."""
        candles = [
            Candle(ts=1_700_000_000, open=100.0, high=101.0, low=99.0, close=100.5, volume=100.0),
            Candle(ts=1_700_003_600, open=101.0, high=102.0, low=100.0, close=101.5, volume=100.0),
        ]
        feed = MockFeed(candles)
        clock = LocalClock(feed, start="2024-01-01", end="2024-01-02")

        c1 = clock.tick()
        assert c1 is not None
        assert c1.close == 100.5

        c2 = clock.tick()
        assert c2 is not None
        assert c2.close == 101.5

        c3 = clock.tick()
        assert c3 is None

    def test_reset_restarts_cursor(self):
        """reset() debe reiniciar el cursor al inicio."""
        candles = [
            Candle(ts=1_700_000_000, open=100.0, high=101.0, low=99.0, close=100.5, volume=100.0),
        ]
        feed = MockFeed(candles)
        clock = LocalClock(feed, start="2024-01-01", end="2024-01-02")

        clock.tick()  # consume
        assert clock.tick() is None  # no more

        clock.reset()
        assert clock.tick() is not None  # restart

    def test_iter_yields_candles(self):
        """__iter__ debe yieldear todas las velas."""
        candles = [
            Candle(ts=1_700_000_000, open=100.0, high=101.0, low=99.0, close=100.5, volume=100.0),
            Candle(ts=1_700_003_600, open=101.0, high=102.0, low=100.0, close=101.5, volume=100.0),
        ]
        feed = MockFeed(candles)
        clock = LocalClock(feed, start="2024-01-01", end="2024-01-02")

        results = list(clock)
        assert len(results) == 2
        assert results[0].close == 100.5
        assert results[1].close == 101.5

    def test_progress_pct(self):
        """progress_pct debe reflejar el avance."""
        candles = [
            Candle(ts=1_700_000_000, open=100.0, high=101.0, low=99.0, close=100.5, volume=100.0),
            Candle(ts=1_700_003_600, open=101.0, high=102.0, low=100.0, close=101.5, volume=100.0),
        ]
        feed = MockFeed(candles)
        clock = LocalClock(feed, start="2024-01-01", end="2024-01-02")

        clock.tick()
        assert clock.progress_pct == 50.0  # 1/2

        clock.tick()
        assert clock.progress_pct == 100.0


if __name__ == "__main__":
    from tests._direct_runner import run_current_test_file

    raise SystemExit(run_current_test_file(__file__))
