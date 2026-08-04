from __future__ import annotations

import os
import time
from unittest.mock import MagicMock

import pytest

from actors.order_book import OrderSide, OrderStatus


@pytest.fixture(params=[
    (
        "actors.hyperliquid_mainnet.hyperliquid_mainnet_order_book",
        "HL_ACCOUNT_ADDRESS",
    ),
    (
        "actors.hyperliquid_testnet.hyperliquid_testnet_order_book",
        "HL_TESTNET_ACCOUNT_ADDRESS",
    ),
])
def hl_order_book_module(request):
    module_name, address_key = request.param
    module = __import__(module_name, fromlist=["HyperliquidOrderBook", "BUY_MARGIN_FACTOR"])
    return module, address_key


def test_get_open_order_oids_uses_info_without_initializing_exchange(monkeypatch, hl_order_book_module):
    module, address_key = hl_order_book_module
    account = "0xabc123"

    monkeypatch.setenv(address_key, account)

    ob = module.HyperliquidOrderBook(max_posiciones=2, symbol="BTC")
    info = MagicMock()
    info.open_orders.return_value = [
        {"coin": "BTC", "oid": 111},
        {"coin": "ETH", "oid": 222},
    ]
    ob._info = info

    async def run():
        return await ob.get_open_order_oids()

    open_oids = pytest.importorskip("asyncio").run(run())

    assert open_oids == {111}
    info.open_orders.assert_called_once_with(account)
    assert ob._exchange is None


def test_cancel_all_resolves_account_address_even_if_exchange_was_preinjected(
    monkeypatch,
    hl_order_book_module,
):
    module, address_key = hl_order_book_module
    account = "0xabc123"

    monkeypatch.setenv(address_key, account)

    ob = module.HyperliquidOrderBook(max_posiciones=2, symbol="BTC")
    exchange = MagicMock()
    info = MagicMock()
    info.open_orders.side_effect = [
        [{"coin": "BTC", "oid": 111}, {"coin": "ETH", "oid": 222}],
        [],
    ]
    ob._exchange = exchange
    ob._info = info

    async def run():
        return await ob.cancel_all_async()

    cancelled = pytest.importorskip("asyncio").run(run())

    assert cancelled == 1
    info.open_orders.assert_any_call(account)
    exchange.bulk_cancel.assert_called_once_with([{"coin": "BTC", "oid": 111}])


@pytest.mark.parametrize("cancel_at_ms", [None, int(time.time() * 1000) + 60_000])
def test_dead_mans_switch_is_idempotent(hl_order_book_module, cancel_at_ms):
    module, _ = hl_order_book_module
    ob = module.HyperliquidOrderBook(max_posiciones=2, symbol="BTC")
    exchange = MagicMock()
    ob._exchange = exchange

    ob.set_dead_mans_switch(cancel_at_ms)
    ob.set_dead_mans_switch(cancel_at_ms)

    exchange.schedule_cancel.assert_called_once_with(time=cancel_at_ms)


def test_bulk_buy_uses_same_margin_buffer_as_single_buy(hl_order_book_module):
    module, _ = hl_order_book_module
    ob = module.HyperliquidOrderBook(max_posiciones=2, symbol="BTC")
    ob._sz_decimals = 5
    exchange = MagicMock()
    captured_payload = {}

    def bulk_orders(payload, grouping):
        captured_payload["payload"] = payload
        captured_payload["grouping"] = grouping
        return {
            "response": {
                "data": {
                    "statuses": [{"resting": {"oid": 123}}],
                }
            }
        }

    exchange.bulk_orders.side_effect = bulk_orders
    ob._exchange = exchange

    buy_order = ob.create_order(OrderSide.BUY, price=100.0, usd_amount=1000.0)

    async def run():
        return await ob.submit_bulk_async(buy_order, None)

    returned_buy, _ = pytest.importorskip("asyncio").run(run())

    assert captured_payload["grouping"] == "na"
    assert captured_payload["payload"][0]["sz"] == pytest.approx(9.9)
    assert returned_buy.status == OrderStatus.PENDING_LIMIT
    assert returned_buy.exchange_oid == 123


def test_hyperliquid_wallet_exposes_account_value_and_mark_to_market():
    from actors.hyperliquid_testnet.hyperliquid_testnet_wallet import HyperliquidWallet

    wallet = HyperliquidWallet(
        usd_initial=1000.0,
        max_posiciones=2,
        json_path=":memory:",
        account_address="0xabc123",
    )
    wallet._last_account_value = 1234.5

    assert wallet.account_value() == 1234.5
    assert wallet.portfolio_value(50_000.0) == 1234.5

    wallet._last_account_value = None
    assert wallet.account_value() is None
    assert wallet.portfolio_value(50_000.0) == wallet.mark_to_market(50_000.0)


@pytest.mark.requires_network
def test_hyperliquid_testnet_readonly_smoke():
    if os.getenv("RUN_HYPERLIQUID_TESTNET_READONLY") != "1":
        pytest.skip("Set RUN_HYPERLIQUID_TESTNET_READONLY=1 and --run-network to enable")

    from hyperliquid.info import Info
    from hyperliquid.utils import constants
    from actors.hyperliquid_testnet.hyperliquid_testnet_feed import HyperliquidRESTFeed

    info = Info(constants.TESTNET_API_URL, skip_ws=True)
    mids = info.all_mids()
    meta = info.meta()
    assert "BTC" in mids
    assert any(asset.get("name") == "BTC" for asset in meta.get("universe", []))

    end = int(time.time()) - 60
    start = end - 6 * 3600
    candles = HyperliquidRESTFeed().get_candles(start, end, "BTCUSDT")
    assert candles
