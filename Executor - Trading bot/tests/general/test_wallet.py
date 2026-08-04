"""
test_wallet.py — Tests unitarios para actors/wallet.py
=======================================================
Cubre: actors/wallet.py (MemoryWallet, JSONWallet, TradeRecord)
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from actors.wallet import MemoryWallet, JSONWallet, TradeRecord


class TestMemoryWallet:

    @pytest.fixture
    def wallet(self):
        return MemoryWallet(usd_initial=1000.0, max_posiciones=3, slot_factor=1.0)

    def test_initial_balance(self, wallet):
        assert wallet.get_usd_balance() == 1000.0
        assert wallet.get_btc_balance() == 0.0
        assert wallet.btc_en_posiciones() == 0.0
        assert wallet.positions_count == 0

    def test_update_buy(self, wallet):
        trade = TradeRecord(
            ts=1_700_000_000, side="BUY", price=50000.0,
            usd_spent=500.0, btc_bought=0.01,
        )
        wallet.update(trade)
        # Después de comprar, usd baja y btc sube
        assert wallet.get_usd_balance() < 1000.0
        assert wallet.btc_en_posiciones() > 0

    def test_update_sell(self, wallet):
        buy_trade = TradeRecord(
            ts=1_700_000_000, side="BUY", price=50000.0,
            usd_spent=500.0, btc_bought=0.01,
        )
        wallet.update(buy_trade)
        btc_before = wallet.btc_en_posiciones()
        sell_trade = TradeRecord(
            ts=1_700_000_001, side="SELL", price=51000.0,
            btc_sold=0.01, usd_received=509.0,
        )
        wallet.update(sell_trade)
        assert wallet.btc_en_posiciones() < btc_before
        assert wallet.positions_count == 0

    def test_max_positions_limit(self, wallet):
        slot = wallet.get_slot_usd()
        price = 50000.0
        qty = slot / price
        for i in range(3):
            trade = TradeRecord(
                ts=1_700_000_000 + i, side="BUY", price=price,
                usd_spent=slot, btc_bought=qty,
            )
            wallet.update(trade)
        assert wallet.positions_count == 3

    def test_slot_calculation(self, wallet):
        slot = wallet.get_slot_usd()
        assert slot > 0
        assert slot <= 1000.0

    def test_snapshot(self, wallet):
        snap = wallet.snapshot(50000.0)
        assert isinstance(snap, dict)
        # MemoryWallet.snapshot usa "usd_balance", no "btc_balance"
        assert "usd_balance" in snap
        assert "btc_libre" in snap or "btc_balance" in snap
        assert "btc_en_posiciones" in snap
        assert "positions_count" in snap
        assert "portfolio_value" in snap

    def test_portfolio_value(self, wallet):
        buy_trade = TradeRecord(
            ts=1_700_000_000, side="BUY", price=50000.0,
            usd_spent=500.0, btc_bought=0.01,
        )
        wallet.update(buy_trade)
        pv = wallet.portfolio_value(55000.0)
        expected = wallet.get_usd_balance() + 0.01 * 55000.0
        assert abs(pv - expected) < 1e-8


class TestJSONWallet:

    @pytest.fixture
    def tmp_path(self):
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    def test_initialization(self, tmp_path):
        json_path = tmp_path / "test_wallet.json"
        wallet = JSONWallet(
            usd_initial=2000.0, max_posiciones=5,
            json_path=str(json_path), slot_factor=1.0,
        )
        assert wallet.get_usd_balance() == 2000.0
        assert wallet.positions_count == 0

    def test_persists_state(self, tmp_path):
        json_path = tmp_path / "test_wallet.json"
        wallet = JSONWallet(
            usd_initial=2000.0, max_posiciones=5,
            json_path=str(json_path), slot_factor=1.0,
        )
        buy_trade = TradeRecord(
            ts=1_700_000_000, side="BUY", price=50000.0,
            usd_spent=500.0, btc_bought=0.01,
        )
        wallet.update(buy_trade)
        wallet.flush({}, {})

        wallet2 = JSONWallet(
            usd_initial=2000.0, max_posiciones=5,
            json_path=str(json_path), slot_factor=1.0,
        )
        assert wallet2.get_usd_balance() < 2000.0
        assert wallet2.positions_count == 1

    def test_flush_no_overwrite(self, tmp_path):
        path = tmp_path / "test.json"
        w = JSONWallet(usd_initial=1000.0, max_posiciones=3, json_path=str(path))
        trade = TradeRecord(
            ts=1, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
        )
        w.update(trade)
        balance_after = w.get_usd_balance()
        w.flush({}, {})
        w2 = JSONWallet(usd_initial=1000.0, max_posiciones=3, json_path=str(path))
        assert w2.get_usd_balance() == balance_after


class TestTradeRecord:

    def test_creation(self):
        trade = TradeRecord(
            ts=1_700_000_000, side="BUY", price=50000.0,
            usd_spent=500.0, btc_bought=0.01,
        )
        assert trade.side == "BUY"
        assert trade.price == 50000.0
        assert trade.usd_spent == 500.0
        assert trade.btc_bought == 0.01
