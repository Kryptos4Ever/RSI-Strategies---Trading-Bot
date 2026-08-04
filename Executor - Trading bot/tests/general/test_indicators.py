"""
test_indicators.py — Tests unitarios para indicadores/bollinger_bands.py
=======================================================================
Cubre: BollingerBandsEngine
"""
from __future__ import annotations

import math
import numpy as np
import pytest

from indicadores.bollinger_bands import BollingerBandsEngine


class TestBollingerBandsEngine:

    @pytest.fixture
    def engine(self):
        return BollingerBandsEngine(period=5, std_mult=2.0)

    def test_initial_state(self, engine):
        assert engine._period == 5
        assert engine._k == 2.0
        assert engine.buffer_size == 0
        assert engine.is_ready is False

    def test_feed_single_value(self, engine):
        engine.update(30000.0)
        assert engine.buffer_size == 1
        assert engine.is_ready is False

    def test_feed_multiple_values(self, engine):
        for v in [30000.0, 30100.0, 29900.0, 30200.0, 29800.0]:
            engine.update(v)
        assert engine.buffer_size == 5

    def test_get_bollinger_bands_insufficient_data(self, engine):
        engine.update(30000.0)
        assert math.isnan(engine.upper)
        assert math.isnan(engine.middle)
        assert math.isnan(engine.lower)

    def test_get_bollinger_bands_exact_period(self, engine):
        closes = [30000.0, 30100.0, 29900.0, 30200.0, 29800.0]
        for v in closes:
            engine.update(v)
        assert not math.isnan(engine.upper)
        assert not math.isnan(engine.middle)
        assert not math.isnan(engine.lower)
        expected_middle = sum(closes) / 5
        assert engine.middle == pytest.approx(expected_middle, rel=1e-6)
        assert engine.upper > engine.middle
        assert engine.lower < engine.middle

    def test_get_bollinger_bands_overflow(self, engine):
        for i in range(100):
            engine.update(30000.0 + float(np.random.default_rng(i).normal(0, 500)))
        assert not math.isnan(engine.upper)
        assert not math.isnan(engine.middle)
        assert not math.isnan(engine.lower)

    def test_reset(self, engine):
        engine.update(30000.0)
        engine.reset()
        assert engine.buffer_size == 0
        assert engine.is_ready is False

    def test_different_periods(self):
        for period in [2, 5, 10, 20]:
            e = BollingerBandsEngine(period=period, std_mult=2.0)
            for i in range(period + 5):
                e.update(30000.0 + i * 10)
            assert not math.isnan(e.upper), f"Falló con period={period}"
            assert not math.isnan(e.middle), f"Falló con period={period}"
            assert not math.isnan(e.lower), f"Falló con period={period}"

    def test_std_mult_effect(self):
        e1 = BollingerBandsEngine(period=5, std_mult=1.0)
        e2 = BollingerBandsEngine(period=5, std_mult=3.0)
        closes = [30000.0, 30100.0, 29900.0, 30200.0, 29800.0]
        for v in closes:
            e1.update(v)
            e2.update(v)
        assert e1.middle == e2.middle  # misma media
        assert (e2.upper - e2.middle) > (e1.upper - e1.middle)  # std_mult mayor → bandas más anchas
        assert (e2.middle - e2.lower) > (e1.middle - e1.lower)

    def test_feed_method(self, engine):
        """Test del método feed() con array numpy."""
        arr = np.array([30000.0, 30100.0, 29900.0, 30200.0, 29800.0])
        engine.feed(arr)
        assert engine.buffer_size == 5
        assert engine.is_ready is True
        assert not math.isnan(engine.upper)

    def test_compute_intra(self, engine):
        """Test del método compute_intra()."""
        engine.update(30000.0)
        engine.update(30100.0)
        engine.update(29900.0)
        engine.update(30200.0)
        engine.update(29800.0)
        u, m, l = engine.compute_intra([30000.0, 30100.0, 29900.0, 30200.0, 29800.0])
        assert not math.isnan(u)
        assert not math.isnan(m)
        assert not math.isnan(l)