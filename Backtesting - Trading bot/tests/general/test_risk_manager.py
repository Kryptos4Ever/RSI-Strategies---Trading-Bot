"""
test_risk_manager.py - Tests unitarios para risk/risk_manager.py.

Cubre el contrato actual de backtest: max drawdown, stop loss individual,
actualizacion de peak, hooks no-op y builder.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from actors.order_book import OrderSide
from risk.risk_manager import RiskManager, build_risk_manager


class FakeWallet:
    def __init__(self, usdt=1000.0, btc=0.0, avg_entry_price=50000.0):
        self._usd = usdt
        self._btc = btc
        self._avg_entry_price = avg_entry_price

    def portfolio_value(self, current_price: float = 0.0) -> float:
        return self._usd + self._btc * (current_price or self._avg_entry_price)

    def get_positions(self):
        if self._btc <= 0:
            return []

        class FakePosition:
            total_btc = self._btc
            avg_entry_price = self._avg_entry_price

        return [FakePosition()]


class FakeCandle:
    def __init__(self, close=50000.0):
        self.close = close


class TestRiskManagerInit:
    def test_default_values(self):
        rm = RiskManager(usd_initial=1000.0)

        assert rm._max_drawdown_pct == 100.0
        assert rm._stop_loss_pct == 0.0
        assert rm.peak == 1000.0

    def test_custom_values(self):
        rm = RiskManager(
            usd_initial=5000.0,
            max_drawdown_pct=25.0,
            stop_loss_pct=10.0,
        )

        assert rm._max_drawdown_pct == 25.0
        assert rm._stop_loss_pct == 10.0
        assert rm.peak == 5000.0


class TestDrawdown:
    def test_no_drawdown_returns_none(self):
        rm = RiskManager(usd_initial=1000.0, max_drawdown_pct=25.0)
        wallet = FakeWallet(usdt=900.0)

        reason = rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle())

        assert reason is None

    def test_drawdown_exceeded_rejects(self):
        rm = RiskManager(usd_initial=1000.0, max_drawdown_pct=10.0)
        wallet = FakeWallet(usdt=800.0)

        reason = rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle())

        assert reason is not None
        assert "drawdown_max" in reason

    def test_drawdown_zero_allows_all(self):
        rm = RiskManager(usd_initial=1000.0, max_drawdown_pct=0.0)
        wallet = FakeWallet(usdt=0.1)

        reason = rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle())

        assert reason is None

    def test_update_peak_only_increases(self):
        rm = RiskManager(usd_initial=1000.0)

        rm.update_peak(1500.0)
        rm.update_peak(1200.0)

        assert rm.peak == 1500.0

    def test_drawdown_uses_updated_peak(self):
        rm = RiskManager(usd_initial=1000.0, max_drawdown_pct=10.0)
        wallet = FakeWallet(usdt=1200.0)
        rm.update_peak(wallet.portfolio_value(50000.0))

        wallet = FakeWallet(usdt=1000.0)
        reason = rm.check(OrderSide.BUY, 50000.0, wallet, FakeCandle())

        assert reason is not None
        assert "drawdown_max" in reason


class TestStopLoss:
    def test_stop_loss_not_configured(self):
        rm = RiskManager(usd_initial=1000.0, stop_loss_pct=0.0)
        wallet = FakeWallet(btc=0.01)

        assert rm.check_stop_loss(wallet, 10000.0) is None

    def test_stop_loss_triggered(self):
        rm = RiskManager(usd_initial=1000.0, stop_loss_pct=10.0)
        wallet = FakeWallet(btc=0.01, avg_entry_price=50000.0)

        reason = rm.check_stop_loss(wallet, 40000.0)

        assert reason is not None
        assert "stop_loss_individual" in reason

    def test_stop_loss_not_triggered(self):
        rm = RiskManager(usd_initial=1000.0, stop_loss_pct=10.0)
        wallet = FakeWallet(btc=0.01, avg_entry_price=50000.0)

        reason = rm.check_stop_loss(wallet, 48000.0)

        assert reason is None

    def test_stop_loss_no_positions(self):
        rm = RiskManager(usd_initial=1000.0, stop_loss_pct=10.0)
        wallet = FakeWallet(btc=0.0)

        assert rm.check_stop_loss(wallet, 40000.0) is None


class TestHooks:
    def test_trade_and_rejection_hooks_are_noops_for_backtest_controls(self):
        rm = RiskManager(usd_initial=1000.0, max_drawdown_pct=10.0)

        rm.on_signal_rejected()
        rm.on_trade_executed()

        assert rm.peak == 1000.0


class TestBuildRiskManager:
    def test_default_max_drawdown(self):
        rm = build_risk_manager(usd_initial=1000.0)

        assert isinstance(rm, RiskManager)
        assert rm._max_drawdown_pct == 100.0

    def test_custom_max_drawdown(self):
        rm = build_risk_manager(usd_initial=1000.0, max_drawdown_pct=20.0)

        assert rm._max_drawdown_pct == 20.0


if __name__ == "__main__":
    from tests._direct_runner import run_current_test_file

    raise SystemExit(run_current_test_file(__file__))
