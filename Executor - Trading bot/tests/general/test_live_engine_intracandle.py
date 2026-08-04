from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from actors.order_book import Order, OrderSide, OrderStatus
from engine.live_engine import LiveEngine
from support.types import Candle


def _candle(ts: int = 1_700_000_000, close: float = 100.0) -> Candle:
    return Candle(
        ts=ts,
        open=100.0,
        high=105.0,
        low=95.0,
        close=close,
        volume=1.0,
    )


def _engine_for_tick() -> LiveEngine:
    engine = object.__new__(LiveEngine)
    engine.feed = MagicMock()
    engine.feed.latest_mid = None
    engine.feed.latest_bid = None
    engine.feed.latest_ask = None
    engine.strategy = MagicMock()
    engine.strategy._compute_dual_bb = MagicMock(
        return_value={
            "upper": 101.0,
            "upper_high": 101.0,
            "lower": 99.0,
            "lower_low": 101.0,
        }
    )
    engine.wallet = MagicMock()
    engine.wallet.positions_count = 1
    engine.wallet.portfolio_value.return_value = 1000.0
    engine.risk = MagicMock()
    engine.risk.check_stop_loss.return_value = None
    engine.risk.update_peak = MagicMock()
    engine.ob = MagicMock()
    engine.telegram = MagicMock()
    engine.environment = "papper"
    engine.saldo_inicial = 1000.0
    engine._is_real = False
    engine._current_candle_ts = 1_700_000_000
    engine._latest_candle = None
    engine._last_intra_signal_ts = 1_700_000_000
    engine._intra_candle_fired = set()
    engine._intra_active_signals = []
    engine._tick_count = 0
    engine._last_log_time = 10**12
    engine._total_ticks_processed = 0
    engine._last_pending_check_ts = 0.0
    engine._pending_check_interval = 15.0
    engine._pending_limit_orders = {}
    engine._baseline_portfolio_value = 1000.0
    engine._daily_buys = 0
    engine._daily_sells = 0
    engine._signal_timestamp = 0.0
    engine._candle_limits = {}
    engine.action_logs = []
    engine._on_new_candle = AsyncMock()
    engine._periodic_flush = AsyncMock()
    engine._check_pending_limit_orders = AsyncMock()
    engine._flush_now = AsyncMock()
    engine._print_event = MagicMock()
    engine._reconcile = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_on_tick_does_not_execute_legacy_intracandle_band_signals():
    engine = _engine_for_tick()

    await engine._on_tick(_candle(close=100.0), is_closed=False)

    engine.strategy._compute_dual_bb.assert_not_called()
    engine.ob.execute_with_guards.assert_not_called()
    assert engine._intra_active_signals == []
    engine._periodic_flush.assert_awaited_once()
    engine._check_pending_limit_orders.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_tick_keeps_stop_loss_as_risk_management():
    engine = _engine_for_tick()
    engine.risk.check_stop_loss.return_value = "stop_loss_individual(5.00% >= 5.0%)"
    order = Order(
        order_id="sl-1",
        side=OrderSide.SELL,
        price=100.0,
        ts=_candle().ts,
        btc_amount=0.01,
        status=OrderStatus.FILLED,
    )
    engine.ob.execute_with_guards.return_value = order

    await engine._on_tick(_candle(close=100.0), is_closed=False)

    engine.ob.execute_with_guards.assert_called_once_with(
        OrderSide.SELL, 100.0, engine.wallet, _candle().ts
    )
    assert engine._daily_sells == 1
    assert engine.action_logs[-1]["type"] == "stop_loss"
    assert engine._intra_active_signals[-1]["type"] == "STOP_LOSS"
    engine.telegram.notify.assert_called_once()
    engine._flush_now.assert_awaited_once_with(100.0)
