"""
test_risk_manager.py — Tests unitarios para risk/risk_manager.py
=================================================================
Cubre: risk/risk_manager.py
"""
from __future__ import annotations

import time
import pytest

from risk.risk_manager import RiskManager, build_risk_manager
from actors.order_book import OrderSide


class FakeWallet:
    def __init__(self, usd=1000.0, btc=0.0):
        self._usd = usd
        self._btc = btc

    def get_usd_balance(self) -> float:
        return self._usd

    def portfolio_value(self, current_price: float = 0.0) -> float:
        return self._usd + self._btc * (current_price or 50000.0)

    def get_positions(self):
        if self._btc > 0:
            class FP:
                total_btc = self._btc
                avg_entry_price = 50000.0
            return [FP()]
        return []

    @property
    def positions_count(self):
        return 1 if self._btc > 0 else 0


class FakeCandle:
    def __init__(self, close=50000.0):
        self.close = close


class TestRiskManagerInit:
    def test_default_values(self):
        rm = RiskManager(usd_initial=1000.0)
        assert rm._max_drawdown_pct == 100.0
        assert rm._max_trades_per_day == 0
        assert rm._cooldown_seconds == 0
        assert rm._circuit_breaker_n == 0
        assert rm._stop_loss_pct == 0.0
        assert rm._peak == 1000.0

    def test_custom_values(self):
        rm = RiskManager(usd_initial=5000.0, max_drawdown_pct=25.0,
                         max_trades_per_day=3, cooldown_seconds=60,
                         circuit_breaker_n=5, stop_loss_pct=10.0)
        assert rm._max_drawdown_pct == 25.0
        assert rm._max_trades_per_day == 3
        assert rm._circuit_breaker_n == 5
        assert rm._stop_loss_pct == 10.0


class TestDrawdown:
    def test_no_drawdown_returns_none(self):
        rm = RiskManager(usd_initial=1000.0, max_drawdown_pct=25.0)
        wallet = FakeWallet(usd=900.0)
        reason = rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle())
        assert reason is None

    def test_drawdown_exceeded_rejects(self):
        rm = RiskManager(usd_initial=1000.0, max_drawdown_pct=10.0)
        wallet = FakeWallet(usd=800.0)
        reason = rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle())
        assert reason is not None
        assert "drawdown_max" in reason

    def test_drawdown_zero_allows_all(self):
        rm = RiskManager(usd_initial=1000.0, max_drawdown_pct=0.0)
        wallet = FakeWallet(usd=0.1)
        reason = rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle())
        assert reason is None

    def test_update_peak_increases(self):
        rm = RiskManager(usd_initial=1000.0)
        assert rm._peak == 1000.0
        rm.update_peak(1500.0)
        assert rm._peak == 1500.0
        rm.update_peak(1200.0)
        assert rm._peak == 1500.0


class TestCircuitBreaker:
    def test_circuit_blocks_buy(self):
        rm = RiskManager(usd_initial=1000.0, circuit_breaker_n=3)
        wallet = FakeWallet()
        for _ in range(3):
            rm.on_signal_rejected()
        assert rm._circuit_open is True
        reason = rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle())
        assert reason is not None
        assert "circuit_breaker" in reason

    def test_circuit_allows_sell_when_open(self):
        rm = RiskManager(usd_initial=1000.0, circuit_breaker_n=3)
        wallet = FakeWallet()
        for _ in range(3):
            rm.on_signal_rejected()
        assert rm._circuit_open is True
        reason = rm.check(OrderSide.SELL, 50000.0, wallet, FakeCandle())
        assert reason is None

    def test_circuit_resets_on_trade_executed(self):
        """on_trade_executed resetea el contador, pero circuit_open sigue True
        hasta que expira el cooldown. El test verifica que _consecutive_rejections se resetea."""
        rm = RiskManager(usd_initial=1000.0, circuit_breaker_n=3)
        for _ in range(3):
            rm.on_signal_rejected()
        assert rm._circuit_open is True
        rm.on_trade_executed()
        # on_trade_executed resetea _consecutive_rejections a 0
        assert rm._consecutive_rejections == 0
        # circuit_open se cierra solo cuando expira el cooldown
        # en este test no esperamos, así que solo verificamos reset

    def test_circuit_breaker_not_configured(self):
        rm = RiskManager(usd_initial=1000.0, circuit_breaker_n=0)
        wallet = FakeWallet()
        for _ in range(10):
            rm.on_signal_rejected()
        assert rm._circuit_open is False
        reason = rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle())
        assert reason is None


class TestDailyTradeLimit:
    def test_limit_not_configured(self):
        rm = RiskManager(usd_initial=1000.0, max_trades_per_day=0)
        wallet = FakeWallet()
        for _ in range(100):
            assert rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle()) is None
            rm.on_trade_executed()

    def test_limit_exceeded(self):
        rm = RiskManager(usd_initial=1000.0, max_trades_per_day=3)
        wallet = FakeWallet()
        for _ in range(3):
            assert rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle()) is None
            rm.on_trade_executed()
        reason = rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle())
        assert reason is not None
        assert "limite_diario" in reason


class TestCooldown:
    def test_cooldown_active(self):
        rm = RiskManager(usd_initial=1000.0, cooldown_seconds=60)
        wallet = FakeWallet()
        rm.on_trade_executed()
        reason = rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle())
        assert reason is not None
        assert "cooldown" in reason

    def test_cooldown_not_configured(self):
        rm = RiskManager(usd_initial=1000.0, cooldown_seconds=0)
        wallet = FakeWallet()
        rm.on_trade_executed()
        reason = rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle())
        assert reason is None


class TestStopLoss:
    def test_stop_loss_not_configured(self):
        rm = RiskManager(usd_initial=1000.0, stop_loss_pct=0.0)
        wallet = FakeWallet(btc=0.01)
        assert rm.check_stop_loss(wallet, 10000.0) is None

    def test_stop_loss_triggered(self):
        rm = RiskManager(usd_initial=1000.0, stop_loss_pct=10.0)
        wallet = FakeWallet(btc=0.01)
        reason = rm.check_stop_loss(wallet, 40000.0)
        assert reason is not None
        assert "stop_loss_individual" in reason

    def test_stop_loss_not_triggered(self):
        rm = RiskManager(usd_initial=1000.0, stop_loss_pct=10.0)
        wallet = FakeWallet(btc=0.01)
        reason = rm.check_stop_loss(wallet, 48000.0)
        assert reason is None

    def test_stop_loss_no_positions(self):
        rm = RiskManager(usd_initial=1000.0, stop_loss_pct=10.0)
        wallet = FakeWallet(btc=0.0)
        assert rm.check_stop_loss(wallet, 40000.0) is None


class TestStatePersistence:
    def test_get_state_returns_dict(self):
        rm = RiskManager(usd_initial=1000.0, circuit_breaker_n=3)
        rm.on_signal_rejected()
        state = rm.get_state()
        assert isinstance(state, dict)
        assert state["consecutive_rejections"] == 1

    def test_restore_state(self):
        rm = RiskManager(usd_initial=1000.0, circuit_breaker_n=3)
        state = {"trades_today": 2, "today_date": "2026-01-15",
                 "consecutive_rejections": 10, "circuit_open": True,
                 "circuit_open_until": time.monotonic() + 300,
                 "peak_portfolio_value": 1500.0, "last_trade_time": 0.0,
                 "stop_loss_pct": 5.0}
        rm.restore_state(state)
        assert rm._trades_today == 2
        assert rm._consecutive_rejections == 10
        assert rm._circuit_open is True
        assert rm._peak == 1500.0


class TestBuildRiskManager:
    def test_backtest_mode(self):
        rm = build_risk_manager(usd_initial=1000.0, enable_live_controls=False)
        assert rm._max_trades_per_day == 0
        assert rm._cooldown_seconds == 0
        assert rm._circuit_breaker_n == 0

    def test_default_max_drawdown(self):
        rm = build_risk_manager(usd_initial=1000.0)
        assert rm._max_drawdown_pct == 100.0