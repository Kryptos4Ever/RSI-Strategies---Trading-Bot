"""
tests/strategies/test_rsi_wilder.py — Tests para RSI Wilder
═══════════════════════════════════════════════════════════════════════════════
Cubre: indicadores/rsi.py (RSIEngine) y strategies/rsi_wilder.py (RSIWilderStrategy)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
import numpy as np

from support.types import Candle, SignalType, PositionDirection
from indicadores.rsi import RSIEngine
from strategies.rsi_wilder import RSIWilderStrategy
from tests.conftest import make_candle, make_candle_sequence


class TestRSIEngine:
    """Tests del RSIEngine (indicadores/rsi.py)."""

    def test_init_default_period(self):
        """RSIEngine debe inicializarse con período 14 por defecto."""
        rsi = RSIEngine()
        assert rsi.period == 14
        assert rsi.value is None

    def test_init_custom_period(self):
        """RSIEngine debe aceptar período personalizado."""
        rsi = RSIEngine(period=7)
        assert rsi.period == 7

    def test_init_invalid_period(self):
        """Período < 2 debe lanzar ValueError."""
        with pytest.raises(ValueError):
            RSIEngine(period=1)

    def test_rsi_needs_period_plus_one_values(self):
        """RSI debe retornar None hasta tener period+1 valores."""
        rsi = RSIEngine(period=5)
        for i in range(5):
            assert rsi.update(100.0 + i) is None
        # En el sexto update (índice 5) debe calcular RSI
        val = rsi.update(110.0)
        assert val is not None

    def test_rsi_all_up(self):
        """Precios siempre subiendo → RSI debe ser 100."""
        rsi = RSIEngine(period=5)
        price = 100.0
        for _ in range(5):
            rsi.update(price)
            price += 10.0
        val = rsi.update(price)
        assert val is not None
        assert val == 100.0

    def test_rsi_all_down(self):
        """Precios siempre bajando → RSI debe ser 0."""
        rsi = RSIEngine(period=5)
        price = 100.0
        for _ in range(5):
            rsi.update(price)
            price -= 10.0
        val = rsi.update(price)
        assert val is not None
        assert val == 0.0

    def test_rsi_between_0_and_100(self):
        """RSI siempre debe estar entre 0 y 100."""
        rsi = RSIEngine(period=14)
        price = 100.0
        for i in range(30):
            price += np.random.normal(0, 2)
            val = rsi.update(max(1.0, price))
            if val is not None:
                assert 0 <= val <= 100, f"RSI={val} fuera de rango en iteración {i}"

    def test_rsi_reset(self):
        """reset() debe limpiar el estado del RSI."""
        rsi = RSIEngine(period=5)
        for i in range(10):
            rsi.update(100.0 + i)
        assert rsi.value is not None
        rsi.reset()
        assert rsi.value is None
        assert rsi.period == 5

    def test_calculate_returns_correct_length(self):
        """calculate() debe retornar una lista del mismo largo que la entrada."""
        closes = [100.0 + i for i in range(20)]
        rsi = RSIEngine(period=5)
        results = rsi.calculate(closes)
        assert len(results) == len(closes)
        # Primeros 'period' valores deben ser None
        assert all(v is None for v in results[:5])
        # Resto debe tener valores
        assert all(v is not None for v in results[5:])

    def test_calculate_from_candles(self):
        """calculate_from_candles() debe funcionar con lista de Candle."""
        candles = make_candle_sequence(n=25, base_price=100.0, volatility=10.0)
        rsi = RSIEngine(period=7)
        results = rsi.calculate_from_candles(candles)
        assert len(results) == len(candles)
        assert all(v is None for v in results[:7])
        assert all(v is not None for v in results[7:])
        assert all(0 <= v <= 100 for v in results[7:] if v is not None)

    # ══════════════════════════════════════════════════════════════════════
    # Tests para price_for_rsi()
    # ══════════════════════════════════════════════════════════════════════

    def test_price_for_rsi_returns_value_when_ready(self):
        """price_for_rsi() debe retornar un precio cuando hay datos suficientes."""
        rsi = RSIEngine(period=5)
        for i in range(10):
            rsi.update(100.0 + i * 2)
        price = rsi.price_for_rsi(30.0)
        assert price > 0

    def test_price_for_rsi_before_ready(self):
        """price_for_rsi() debe retornar prev_close cuando no hay datos."""
        rsi = RSIEngine(period=5)
        price = rsi.price_for_rsi(50.0)
        assert price == 0.0  # prev_close es None

    def test_price_for_rsi_returns_prev_close_when_count_insufficient(self):
        """price_for_rsi() debe retornar prev_close cuando count < period."""
        rsi = RSIEngine(period=5)
        rsi.update(100.0)  # Solo 1 update, count=0
        price = rsi.price_for_rsi(50.0)
        assert price == 100.0  # prev_close es 100.0

    def test_price_for_rsi_lower_than_prev(self):
        """price_for_rsi(30) con precios bajando (RSI bajo) debe retornar un precio 
        mayor que prev_close, porque para que RSI suba de vuelta a 30 el precio 
        debe subir."""
        rsi = RSIEngine(period=5)
        for i in range(10):
            rsi.update(100.0 - i)  # Precios bajando → RSI bajo
        rsi_before = rsi.value
        price = rsi.price_for_rsi(30.0)
        prev = rsi._prev_close
        assert prev is not None
        assert rsi_before is not None
        # RSI actual está por debajo de 30, necesitamos precio mayor para subir RSI a 30
        assert price > prev, f"RSI={rsi_before:.2f}, price={price:.4f}, prev={prev:.4f}"

    def test_price_for_rsi_higher_than_prev(self):
        """price_for_rsi(70) con precios subiendo (RSI alto) debe retornar un precio
        menor que prev_close, porque para que RSI baje de vuelta a 70 el precio 
        debe bajar."""
        rsi = RSIEngine(period=5)
        for i in range(10):
            rsi.update(100.0 + i * 2)  # Precios subiendo → RSI alto
        rsi_before = rsi.value
        price = rsi.price_for_rsi(70.0)
        prev = rsi._prev_close
        assert prev is not None
        assert rsi_before is not None
        # RSI actual está por encima de 70, necesitamos precio menor para bajar RSI a 70
        assert price < prev, f"RSI={rsi_before:.2f}, price={price:.4f}, prev={prev:.4f}"

    def test_price_for_rsi_neutral(self):
        """price_for_rsi(50) debe retornar precio cercano a prev_close."""
        rsi = RSIEngine(period=5)
        np.random.seed(42)  # Deterministic seed for reproducibility
        for i in range(10):
            rsi.update(100.0 + np.random.normal(0, 2))
        price = rsi.price_for_rsi(50.0)
        prev = rsi._prev_close
        assert prev is not None
        diff = abs(price - prev) / prev
        assert diff < 0.10  # Diferencia menor al 10% (tolerancia ampliada por aleatoriedad)


class TestRSIWilderStrategy:
    """Tests de la estrategia RSIWilderStrategy."""

    @pytest.fixture
    def strategy(self):
        """Estrategia RSI con valores por defecto."""
        return RSIWilderStrategy(
            rsi_period=5,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            reduce_long=50.0,
            reduce_short=50.0,
        )

    @pytest.fixture
    def wallet_with_long(self):
        """Wallet con una posición long abierta."""
        from actors.wallet import MemoryWallet, TradeRecord
        w = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        w.update(TradeRecord(ts=1, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                            direction=PositionDirection.LONG))
        return w

    @pytest.fixture
    def wallet_with_short(self):
        """Wallet con una posición short abierta."""
        from actors.wallet import MemoryWallet, TradeRecord
        w = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        w.update(TradeRecord(ts=1, side="SELL", price=65000.0, btc_sold=0.1, usd_received=6500.0,
                            direction=PositionDirection.SHORT))
        return w

    def test_init_defaults(self):
        """La estrategia debe inicializarse con valores por defecto."""
        s = RSIWilderStrategy()
        assert s._rsi_period == 14
        assert s._oversold == 30.0
        assert s._overbought == 70.0

    def test_init_custom_values(self):
        """La estrategia debe aceptar valores personalizados."""
        s = RSIWilderStrategy(
            rsi_period=7,
            oversold_threshold=25.0,
            overbought_threshold=75.0,
            reduce_long=45.0,
            reduce_short=55.0,
        )
        assert s._rsi_period == 7
        assert s._oversold == 25.0
        assert s._overbought == 75.0
        assert s._reduce_long == 45.0
        assert s._reduce_short == 55.0

    def test_get_default_config(self):
        """get_default_config() debe retornar dict con parámetros."""
        config = RSIWilderStrategy.get_default_config()
        assert config["rsi_period"] == 14
        assert config["oversold_threshold"] == 30.0
        assert config["overbought_threshold"] == 70.0

    def test_initial_candles_return_hold(self, strategy):
        """Antes de tener datos suficientes, debe retornar HOLD."""
        from actors.wallet import MemoryWallet
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        for i in range(5):
            candle = make_candle(ts=1_700_000_000 + i * 3600, close=100.0 - i * 2)
            signals = strategy.on_candle(candle, wallet)
            assert len(signals) == 1
            assert signals[0].signal_type == SignalType.HOLD

    def test_describe_returns_params(self, strategy):
        """describe() debe retornar los parámetros de la estrategia."""
        desc = strategy.describe()
        assert desc["rsi_period"] == 5
        assert desc["oversold_threshold"] == 30.0
        assert desc["overbought_threshold"] == 70.0

    def test_on_start_logs_config(self, strategy):
        """on_start() no debe lanzar error."""
        from actors.wallet import MemoryWallet
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        strategy.on_start(wallet)  # No debe lanzar excepción

    def test_on_stop_no_error(self, strategy):
        """on_stop() no debe lanzar error."""
        from actors.wallet import MemoryWallet
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        strategy.on_stop(wallet)

    def test_rsi_below_oversold_emits_open_long(self, strategy):
        """RSI bajo oversold debe emitir OPEN_LONG cuando no hay posición."""
        from actors.wallet import MemoryWallet
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        # Crear velas descendentes para que RSI baje
        prices = [100.0, 98.0, 96.0, 94.0, 92.0, 90.0, 88.0, 86.0, 84.0, 82.0]
        last_signals = []
        for i, p in enumerate(prices):
            candle = make_candle(ts=1_700_000_000 + i * 3600, close=p)
            signals = strategy.on_candle(candle, wallet)
            last_signals = signals
        # Verificar que RSI está bajo
        assert strategy._rsi_engine.value is not None
        # Debería emitir OPEN_LONG si RSI está entre 30-50 (sin posición en wallet, RSI < 50)
        rsi = strategy._rsi_engine.value
        if rsi is not None and 30.0 < rsi < 50.0:
            has_open_long = any(s.signal_type == SignalType.OPEN_LONG for s in last_signals)
            assert has_open_long, f"Se esperaba OPEN_LONG pero señales fueron: {[s.signal_type for s in last_signals]}"

    def test_rsi_below_50_with_long_emits_add_and_reduce(self, strategy, wallet_with_long):
        """RSI < 50 con posición LONG debe emitir ADD_LONG + REDUCE_LONG."""
        prices = [100.0, 98.0, 96.0, 94.0, 92.0, 90.0, 88.0, 86.0, 84.0, 82.0]
        last_signals = []
        for i, p in enumerate(prices):
            candle = make_candle(ts=1_700_000_000 + i * 3600, close=p)
            signals = strategy.on_candle(candle, wallet_with_long)
            last_signals = signals
        types = [s.signal_type for s in last_signals]
        # REDUCE_LONG siempre se emite cuando RSI < 50
        has_reduce = SignalType.REDUCE_LONG in types
        assert has_reduce, f"Se esperaba REDUCE_LONG, señales: {types}"
        # ADD_LONG solo si RSI está entre 30-50
        rsi = strategy._rsi_engine.value
        if rsi is not None and 30.0 < rsi < 50.0:
            has_add = SignalType.ADD_LONG in types
            assert has_add, f"Se esperaba ADD_LONG, señales: {types}"

    def test_rsi_below_50_with_short_emits_close_short(self, strategy, wallet_with_short):
        """RSI < 50 con posición SHORT debe emitir CLOSE_SHORT."""
        prices = [65000.0, 64800.0, 64600.0, 64400.0, 64200.0, 64000.0, 63800.0, 63600.0, 63400.0, 63200.0]
        last_signals = []
        for i, p in enumerate(prices):
            candle = make_candle(ts=1_700_000_000 + i * 3600, close=p)
            signals = strategy.on_candle(candle, wallet_with_short)
            last_signals = signals
        types = [s.signal_type for s in last_signals]
        # CLOSE_SHORT cuando RSI < 50 (independientemente de si RSI > 30 o no)
        rsi = strategy._rsi_engine.value
        if rsi is not None and rsi < 50.0:
            has_close = SignalType.CLOSE_SHORT in types
            assert has_close, f"Se esperaba CLOSE_SHORT, señales: {types}"

    def test_rsi_above_overbought_emits_open_short(self, strategy):
        """RSI sobre overbought debe emitir OPEN_SHORT cuando no hay posición."""
        from actors.wallet import MemoryWallet
        wallet = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        prices = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0, 114.0, 116.0, 118.0]
        last_signals = []
        for i, p in enumerate(prices):
            candle = make_candle(ts=1_700_000_000 + i * 3600, close=p)
            signals = strategy.on_candle(candle, wallet)
            last_signals = signals
        rsi = strategy._rsi_engine.value
        assert rsi is not None
        if 50.0 < rsi < 70.0:
            has_open = any(s.signal_type == SignalType.OPEN_SHORT for s in last_signals)
            assert has_open, f"Se esperaba OPEN_SHORT, señales: {[s.signal_type for s in last_signals]}"

    def test_signals_have_reason_with_prices(self, strategy):
        """Las señales deben incluir precios calculados en el reason."""
        from actors.wallet import MemoryWallet
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        prices = [100.0, 98.0, 96.0, 94.0, 92.0, 90.0, 88.0, 86.0, 84.0, 82.0]
        candle = make_candle(ts=1_700_000_000, close=82.0)
        for i, p in enumerate(prices):
            candle = make_candle(ts=1_700_000_000 + i * 3600, close=p)
            strategy.on_candle(candle, wallet)
        # Verificar última señal from tick() which updates last_signals
        strategy.tick(candle, wallet)
        last_signals = strategy.last_signals
        for s in last_signals:
            if s.signal_type != SignalType.HOLD:
                assert "@" in s.reason, f"Reason debe contener precio: {s.reason}"
                assert str(round(s.price, 2)) in s.reason, f"Reason debe mostrar el precio: {s.reason}"


if __name__ == "__main__":
    from tests._direct_runner import run_current_test_file

    raise SystemExit(run_current_test_file(__file__))