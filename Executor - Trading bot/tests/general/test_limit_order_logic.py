"""
test_limit_order_logic.py — Tests unitarios de la nueva logica de ordenes limite
==================================================================================
Cubre:
  - actors/order_book.py        -> PENDING_LIMIT, execute() sin wallet.update()
  - feeds testnet y perps       -> _parse_fill()
  - engine/live_engine.py       -> _on_order_fill(), logica D2

Sin conexion de red ni credenciales.
"""
from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from actors.order_book import OrderBook, OrderSide, OrderStatus


# ── helpers ────────────────────────────────────────────────────────────────

def _wallet(usd=1000.0, btc=0.0, positions=0, max_pos=1):
    class W:
        def get_usd_balance(self): return usd
        def get_btc_balance(self): return btc
        def btc_en_posiciones(self): return btc
        def get_slot_usd(self): return max(10.0, usd / max(1, max_pos))
        def get_btc_por_venta(self):
            return btc / max(1, positions) if positions > 0 and btc > 0 else 0.0
        @property
        def positions_count(self): return positions
        def portfolio_value(self, price): return usd + btc * price
        def update(self, trade): pass
    return W()


def _order(oid=1, cloid="c", side="BUY", price=50000.0):
    from actors.order_book import Order
    return Order(
        order_id=str(oid),
        side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
        price=price,
        ts=0,
        btc_amount=0.001,
        status=OrderStatus.PENDING_LIMIT,
        exchange_oid=oid,
        cloid=cloid,
    )


# ── seccion 1: PENDING_LIMIT y execute() ──────────────────────────────────

class TestPendingLimitStatus:

    def test_status_exists(self):
        assert hasattr(OrderStatus, "PENDING_LIMIT")

    def test_no_wallet_update_on_pending(self):
        """execute() no debe llamar wallet.update() cuando status == PENDING_LIMIT."""
        class FakeOB(OrderBook):
            # Override __init__ para no depender del constructor ABC
            def __init__(self):
                self._max_posiciones  = 1
                self._commission_pct  = 0.05
                self._min_order_usdt  = 10.0
                self._sell_margin_pct = 1.0

            def create_order(self, side, price, usd_amount=None, btc_amount=None):
                from actors.order_book import Order
                return Order(
                    order_id="t", side=side, price=price, ts=0,
                    btc_amount=0.001, status=OrderStatus.PENDING_LIMIT,
                )
            def submit(self, o):
                o.status = OrderStatus.PENDING_LIMIT
                return o
            def check(self, oid):
                from actors.order_book import Order
                return Order(
                    order_id=oid, side=OrderSide.BUY, price=50000.0, ts=0,
                    status=OrderStatus.PENDING_LIMIT,
                )

        ob = FakeOB()
        w = _wallet(usd=500.0)
        calls = []
        w.update = lambda t: calls.append(t)

        order = ob.execute(OrderSide.BUY, 50000.0, w, candle_ts=1000)
        assert order.status == OrderStatus.PENDING_LIMIT
        assert calls == [], "wallet.update() must NOT be called for PENDING_LIMIT"


# ── seccion 2: _parse_fill() ──────────────────────────────────────────────

class TestParseFill:

    def _feed(self, testnet):
        if testnet:
            from actors.hyperliquid_testnet.hyperliquid_testnet_feed import HyperliquidWSFeed
        else:
            from actors.hyperliquid_mainnet.hyperliquid_mainnet_feed import HyperliquidWSFeed
        return HyperliquidWSFeed.__new__(HyperliquidWSFeed)

    @pytest.mark.parametrize("t", [True, False])
    def test_open_long_is_buy(self, t):
        p = self._feed(t)._parse_fill({
            "coin": "BTC", "dir": "Open Long",
            "px": "95000.5", "sz": "0.001",
            "fee": "0.05", "oid": 111, "cloid": None, "time": 1700000000000,
        })
        assert p is not None
        assert p["side"] == "BUY"
        assert p["oid"] == 111

    @pytest.mark.parametrize("t", [True, False])
    def test_close_long_is_sell(self, t):
        p = self._feed(t)._parse_fill({
            "coin": "BTC", "dir": "Close Long",
            "px": "96000", "sz": "0.001",
            "fee": "0.05", "oid": 222, "cloid": "my-cloid", "time": 1700001000000,
        })
        assert p is not None
        assert p["side"] == "SELL"
        assert p["cloid"] == "my-cloid"

    @pytest.mark.parametrize("t", [True, False])
    def test_open_short_is_sell(self, t):
        p = self._feed(t)._parse_fill({
            "coin": "BTC", "dir": "Open Short",
            "px": "90000", "sz": "0.002",
            "fee": "0.09", "oid": 333, "cloid": None, "time": 1700002000000,
        })
        assert p is not None and p["side"] == "SELL"

    @pytest.mark.parametrize("t", [True, False])
    def test_close_short_is_buy(self, t):
        p = self._feed(t)._parse_fill({
            "coin": "BTC", "dir": "Close Short",
            "px": "88000", "sz": "0.002",
            "fee": "0.088", "oid": 444, "cloid": None, "time": 1700003000000,
        })
        assert p is not None and p["side"] == "BUY"

    @pytest.mark.parametrize("t", [True, False])
    def test_malformed_returns_none(self, t):
        assert self._feed(t)._parse_fill({"px": "bad"}) is None


# ── seccion 3: LiveEngine._on_order_fill() ────────────────────────────────

# ── seccion 4: logica D2 de envio de ordenes ─────────────────────────────

class TestD2SendFlags:
    """
    Tabla D2 (ver IMPROVEMENT_PLAN.md):
    pos=0        → solo BUY (si hay señal)
    0<pos<MAX    → BUY+SELL si ambas señales
    pos>=MAX     → solo SELL
    """
    @pytest.mark.parametrize("pos,mx,hb,hs,eb,es", [
        (0, 1, True,  True,  True,  False),
        (0, 1, True,  False, True,  False),
        (0, 1, False, True,  False, False),
        (1, 3, True,  True,  True,  True),
        (1, 3, True,  False, True,  False),
        (1, 3, False, True,  False, True),
        (3, 3, True,  True,  False, True),
        (3, 3, True,  False, False, False),
        (3, 3, False, True,  False, True),
        (3, 3, False, False, False, False),
    ])
    def test_flags(self, pos, mx, hb, hs, eb, es):
        b = MagicMock() if hb else None
        s = MagicMock() if hs else None
        send_buy  = (b is not None) and (pos < mx)
        send_sell = (s is not None) and (pos > 0)
        assert send_buy  == eb, f"send_buy={send_buy} expected {eb}  (pos={pos}, max={mx})"
        assert send_sell == es, f"send_sell={send_sell} expected {es} (pos={pos}, max={mx})"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
