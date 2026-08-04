"""
test_order_book.py — Tests unitarios para actors/order_book.py
===============================================================
Cubre: actors/order_book.py (SimulatedOrderBook, OrderStatus, OrderSide)
"""
from __future__ import annotations

import pytest

from actors.order_book import SimulatedOrderBook, OrderStatus, OrderSide


class TestCheckBuyGuards:

    @pytest.fixture
    def ob(self):
        return SimulatedOrderBook(commission_pct=0.1, max_posiciones=3)

    def test_buy_ok(self, ob):
        wallet = _make_wallet(usd=1000.0, btc=0.0, positions=0)
        assert ob.check_buy_guards(wallet) is None

    def test_insufficient_balance(self, ob):
        wallet = _make_wallet(usd=5.0, btc=0.0, positions=0)
        assert ob.check_buy_guards(wallet) is not None

    def test_max_positions_reached(self, ob):
        wallet = _make_wallet(usd=1000.0, btc=0.1, positions=3)
        assert ob.check_buy_guards(wallet) is not None


class TestCheckSellGuards:

    @pytest.fixture
    def ob(self):
        return SimulatedOrderBook(commission_pct=0.1, max_posiciones=3)

    def test_sell_ok(self, ob):
        wallet = _make_wallet(usd=500.0, btc=0.01, positions=1)
        assert ob.check_sell_guards(wallet, current_price=50000.0) is None

    def test_no_btc(self, ob):
        wallet = _make_wallet(usd=1000.0, btc=0.0, positions=0)
        assert ob.check_sell_guards(wallet, current_price=50000.0) is not None

    def test_min_operative_not_met(self, ob):
        wallet = _make_wallet(usd=1000.0, btc=0.0001, positions=1)
        reason = ob.check_sell_guards(wallet, current_price=10000.0)
        assert reason is not None




def _make_wallet(usd=1000.0, btc=0.0, positions=0):
    class FakeWallet:
        def get_usd_balance(self):
            return usd
        def get_btc_balance(self):
            return btc
        def btc_en_posiciones(self):
            return btc
        def get_slot_usd(self):
            return max(10.0, usd / max(1, positions + 1))
        def get_btc_por_venta(self):
            return btc / max(1, positions) if positions > 0 and btc > 0 else 0.0
        @property
        def positions_count(self):
            return positions
    return FakeWallet()