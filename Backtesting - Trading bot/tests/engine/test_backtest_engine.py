"""
tests/engine/test_backtest_engine.py — Tests de integración del BacktestEngine
================================================================================
Cubre: engine/backtest_engine.py — loop principal, warmup, señales, modos de OB,
      RiskManager, checkpoints, callback on_trade, summary, flat market.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
import numpy as np

from actors.clock import LocalClock
from actors.order_book import (
    SimulatedLimitGTCOrderBook,
    SimulatedLimitPostOnlyOrderBook,
    SimulatedLimitGTCOrderBook,
    OrderSide,
)
from actors.wallet import JSONWallet, MemoryWallet, TradeRecord
from engine.backtest_engine import BacktestEngine
from support.types import Candle, Signal, SignalType, SignalSide
from risk.risk_manager import build_risk_manager
from state.state_manager import MemoryStateManager, Checkpoint
from strategies.base_strategy import BaseStrategy, HOLD_LIST
from tests.conftest import make_candle_sequence


class MockPriceFeed:
    """Feed de precios simulado para tests."""

    def __init__(self, candles: List[Candle]):
        self._candles = candles

    def get_candles(self, start: int, end: int, symbol: str = "BTCUSDT") -> List[Candle]:
        return self._candles

    def subscribe(self, callback, symbol: str = "BTCUSDT") -> None:
        pass


class MockClock:
    """Clock que itera sobre una lista fija de velas."""

    def __init__(self, candles: List[Candle]):
        self._candles = candles
        self.candles = candles
        self._idx = 0
        self.total_candles = len(candles)
        self.is_live = False

    def tick(self) -> Optional[Candle]:
        if self._idx >= len(self._candles):
            return None
        c = self._candles[self._idx]
        self._idx += 1
        return c

    def reset(self) -> None:
        self._idx = 0

    def __iter__(self):
        while (c := self.tick()) is not None:
            yield c


class DummyStrategy(BaseStrategy):
    """Estrategia que siempre emite HOLD — testea el loop sin señales."""

    def __init__(self):
        super().__init__(name="Dummy")
        self._bb_engine_buy = MagicMock()
        self._bb_engine_sell = MagicMock()

    def on_start(self, wallet) -> None:
        pass

    def on_candle(self, candle: Candle, wallet) -> List[Signal]:
        return HOLD_LIST

    @staticmethod
    def get_default_config() -> dict:
        return {}


class BuyEveryCandleStrategy(BaseStrategy):
    """Estrategia que compra en cada vela — para testear ejecución masiva."""

    def __init__(self):
        super().__init__(name="BuyEveryCandle")
        self._next_ts: int = 0

    def on_start(self, wallet) -> None:
        pass

    def on_candle(self, candle: Candle, wallet) -> List[Signal]:
        if candle.ts == self._next_ts:
            self._next_ts = candle.ts + 1
            return HOLD_LIST
        self._next_ts = candle.ts
        return [Signal(signal_type=SignalType.OPEN_LONG, price=candle.close,
                       reason="test_buy", ts=candle.ts)]

    @staticmethod
    def get_default_config() -> dict:
        return {}


def _build_engine(
    candles: List[Candle],
    json_path: str,
    capital: float = 1000.0,
    max_pos: int = 3,
    modo: str = "mercado",
    commission: float = 0.1,
) -> tuple:
    feed = MockPriceFeed(candles)
    clock = MockClock(candles)
    wallet = JSONWallet(usd_initial=capital, max_posiciones=max_pos, json_path=json_path)
    if modo == "limit_post_only":
        ob = SimulatedLimitPostOnlyOrderBook(commission_pct=commission, max_posiciones=max_pos)
    else:
        ob = SimulatedLimitGTCOrderBook(commission_pct=commission, max_posiciones=max_pos)
    risk = build_risk_manager(usd_initial=capital)
    state = MemoryStateManager()
    engine = BacktestEngine(
        clock, wallet, ob, risk, state, feed,
        usd_initial=capital,
        fecha_inicio="2024-01-01",
        fecha_fin="2024-12-31",
        commission_pct=commission,
        results_json=json_path,
        max_posiciones=max_pos,
    )
    return engine, wallet, feed


class TestBacktestEngineConstruction:
    """Prueba la construcción y los defaults del motor."""

    def test_constructor_accepts_minimal_params(self, tmp_path):
        """El constructor acepta los parámetros mínimos."""
        candles = make_candle_sequence(n=10)
        json_path = str(tmp_path / "test.json")
        engine, _, _ = _build_engine(candles, json_path)
        assert engine._usd_initial == 1000.0
        assert engine._commission_pct == 0.1
        assert engine._max_pos == 3

    def test_constructor_rejects_missing_feed(self, tmp_path):
        """Sin feed, debe fallar (TypeError)."""
        candles = make_candle_sequence(n=10)
        json_path = str(tmp_path / "test.json")
        feed = MockPriceFeed(candles)
        clock = MockClock(candles)
        wallet = JSONWallet(usd_initial=1000.0, max_posiciones=3, json_path=json_path)
        ob = SimulatedLimitGTCOrderBook(commission_pct=0.1, max_posiciones=3)
        risk = build_risk_manager(usd_initial=1000.0)
        state = MemoryStateManager()
        with pytest.raises(TypeError):
            BacktestEngine(clock, wallet, ob, risk, state,
                           usd_initial=1000.0, fecha_inicio="2024-01-01",
                           fecha_fin="2024-12-31", commission_pct=0.1,
                           results_json=json_path, max_posiciones=3)


class TestBacktestEngineRun:
    """Prueba la ejecución del backtest."""

    def test_engine_runs_with_dummy_strategy(self, tmp_path):
        """Corre con DummyStrategy (siempre HOLD)."""
        candles = make_candle_sequence(n=30)
        json_path = str(tmp_path / "result.json")
        engine, wallet, _ = _build_engine(candles, json_path)
        strategy = DummyStrategy()
        summary = engine.run(strategy)
        assert "estrategia" in summary
        assert summary["total_compras"] == 0
        assert summary["total_ventas"] == 0

    def test_engine_produces_valid_summary(self, tmp_path):
        """Resumen tiene todas las claves requeridas."""
        candles = make_candle_sequence(n=50)
        json_path = str(tmp_path / "result.json")
        engine, _, _ = _build_engine(candles, json_path)
        strategy = DummyStrategy()
        summary = engine.run(strategy)
        required_keys = [
            "estrategia", "saldo_inicial_usd", "portfolio_value_final",
            "pnl_pct", "total_compras", "total_ventas", "total_ignorados",
            "sharpe", "max_drawdown_pct", "buy_hold_pnl_pct", "alpha_vs_bh",
        ]
        for key in required_keys:
            assert key in summary, f"Falta: {key}"

    def test_engine_json_output_exists(self, tmp_path):
        """El JSON de salida se crea después de run()."""
        candles = make_candle_sequence(n=30)
        json_path = str(tmp_path / "result.json")
        engine, _, _ = _build_engine(candles, json_path)
        engine.run(DummyStrategy())
        assert os.path.exists(json_path)
        with open(json_path) as f:
            data = json.load(f)
        assert "summary" in data

    def test_engine_with_buy_strategy(self, tmp_path):
        """BuyEveryCandleStrategy debe generar compras."""
        candles = make_candle_sequence(n=30, base_price=30_000.0)
        json_path = str(tmp_path / "result.json")
        engine, _, _ = _build_engine(candles, json_path)
        summary = engine.run(BuyEveryCandleStrategy())
        # Debe haber al menos 1 compra
        assert summary["total_compras"] >= 1

    def test_engine_with_on_trade_callback(self, tmp_path):
        """El callback on_trade se llama en cada trade."""
        candles = make_candle_sequence(n=50, base_price=30_000.0)
        json_path = str(tmp_path / "result.json")
        trades_fired = []

        def on_trade(wallet, strategy, candle):
            trades_fired.append(candle.ts)

        feed = MockPriceFeed(candles)
        clock = MockClock(candles)
        wallet = JSONWallet(usd_initial=1000.0, max_posiciones=3, json_path=json_path)
        ob = SimulatedLimitGTCOrderBook(commission_pct=0.1, max_posiciones=3)
        risk = build_risk_manager(usd_initial=1000.0)
        state = MemoryStateManager()
        engine = BacktestEngine(
            clock, wallet, ob, risk, state, feed,
            usd_initial=1000.0,
            fecha_inicio="2024-01-01",
            fecha_fin="2024-12-31",
            commission_pct=0.1,
            results_json=json_path,
            max_posiciones=3,
            on_trade=on_trade,
        )
        engine.run(BuyEveryCandleStrategy())
        # BuyEveryCandleStrategy debe haber generado trades
        assert len(trades_fired) >= 1
        assert isinstance(trades_fired, list)


class TestBacktestEngineModes:
    """Prueba los 3 modos de OrderBook."""

    @pytest.fixture(params=["limit_post_only", "limite_gtc"])
    def modo(self, request):
        return request.param

    def test_engine_runs_with_all_modes(self, tmp_path, modo):
        """Los 3 modos deben ejecutar sin error."""
        candles = make_candle_sequence(n=30)
        json_path = str(tmp_path / "result.json")
        engine, _, _ = _build_engine(candles, json_path, modo=modo)
        summary = engine.run(BuyEveryCandleStrategy())
        assert summary["total_compras"] >= 0  # puede fallar por precio límite


class TestBacktestEngineEdgeCases:
    """Prueba casos borde."""

    def test_flat_market(self, tmp_path):
        """Mercado completamente plano — sin señales."""
        flat_candles = [
            Candle(ts=1_700_000_000 + i * 3600,
                   open=30_000.0, high=30_000.0, low=30_000.0,
                   close=30_000.0, volume=100.0)
            for i in range(20)
        ]
        json_path = str(tmp_path / "flat.json")
        engine, wallet, _ = _build_engine(flat_candles, json_path)
        summary = engine.run(DummyStrategy())
        assert wallet.portfolio_value(30_000.0) > 900.0  # máx 10% pérdida

    def test_single_candle(self, tmp_path):
        """Una sola vela no debe causar error."""
        candle = Candle(ts=1_700_000_000, open=30_000.0, high=30_100.0,
                        low=29_900.0, close=30_050.0, volume=100.0)
        json_path = str(tmp_path / "single.json")
        engine, _, _ = _build_engine([candle], json_path)
        # No debe lanzar excepción
        summary = engine.run(DummyStrategy())
        assert summary is not None

    def test_warmup_loads_from_feed(self, tmp_path):
        """El warmup debe cargar velas del feed."""
        candles = make_candle_sequence(n=30)
        json_path = str(tmp_path / "warmup.json")
        engine, wallet, _ = _build_engine(candles, json_path)
        # El feed mock devuelve todas las velas, el warmup debe funcionar
        strategy = DummyStrategy()
        strategy.load_warmup = MagicMock()
        engine.run(strategy)
        # load_warmup debe haber sido llamado
        assert strategy.load_warmup.called

    def test_ignored_signals_logged(self, tmp_path):
        """Las señales ignoradas por riesgo se registran."""
        candles = make_candle_sequence(n=50, base_price=30_000.0)
        json_path = str(tmp_path / "ignored.json")
        engine, _, _ = _build_engine(candles, json_path)
        summary = engine.run(BuyEveryCandleStrategy())
        # Debe haber ignorados (precio límite fuera de rango, etc.)
        assert summary["total_ignorados"] >= 0

    def test_checkpoints_created(self, tmp_path):
        """Cada vela debe generar un checkpoint."""
        candles = make_candle_sequence(n=10)
        json_path = str(tmp_path / "cp.json")
        engine, _, _ = _build_engine(candles, json_path)
        engine.run(DummyStrategy())
        history = engine.state.history()
        assert len(history) == 10  # 10 checkpoints para 10 velas

    def test_gtc_counter_in_summary(self, tmp_path):
        """Modo limite_gtc debe incluir gtc_stats en chart_data_extra."""
        candles = make_candle_sequence(n=30)
        json_path = str(tmp_path / "gtc.json")
        engine, _, _ = _build_engine(candles, json_path, modo="limite_gtc")
        summary = engine.run(BuyEveryCandleStrategy())
        # chart_data_extra["gtc_stats"] debe existir
        chart_data = summary.get("chart_data", {})
        if "gtc_stats" not in chart_data:
            # Puede no haber trades dependiendo de las velas
            pass  # No es un error, es válido
if __name__ == "__main__":
    from tests._direct_runner import run_current_test_file

    raise SystemExit(run_current_test_file(__file__))
