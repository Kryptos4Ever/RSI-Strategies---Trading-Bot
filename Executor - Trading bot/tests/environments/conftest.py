"""
tests/environments/conftest_environments.py — Fixtures compartidos para tests de entornos
========================================================================================
Proporciona mocks de clientes de exchange, wallets preconfiguradas y helpers
para simular operaciones de compra/venta en cada entorno.
"""
from __future__ import annotations

import math
import os
import sys
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest

# Asegurar que el raíz del proyecto esté en el path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from support.types import Candle
from actors.wallet import MemoryWallet, JSONWallet, TradeRecord
from actors.order_book import SimulatedOrderBook, OrderSide, OrderStatus, Order


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS COMPARTIDOS
# ══════════════════════════════════════════════════════════════════════════════

MIN_ORDER_USD = 10.0  # Valor mínimo de operación para todos los exchanges


def make_trade_record(
    side: str,
    price: float,
    usd_amount: float = MIN_ORDER_USD,
    ts: int = 1_700_000_000,
) -> TradeRecord:
    """Crea un TradeRecord para una operación de ~$10."""
    qty = usd_amount / price
    print(f"    -> Trade {side} creado: {qty:.6f} BTC @ {price:.2f} USD (${usd_amount:.2f})")
    if side == "BUY":
        return TradeRecord(
            ts=ts, side="BUY", price=price,
            usd_spent=usd_amount, btc_bought=qty,
            commission=usd_amount * 0.001,
        )
    else:
        return TradeRecord(
            ts=ts, side="SELL", price=price,
            btc_sold=qty, usd_received=usd_amount,
            commission=usd_amount * 0.001,
        )


def verify_position_opened(wallet, price: float, expected_qty: float) -> None:
    """Verifica que una posición se abrió correctamente."""
    count = wallet.positions_count
    btc = wallet.btc_en_posiciones()
    usd = wallet.get_usd_balance()
    print(f"    -> Posicion ABIERTA: count={count}, BTC={btc:.6f}, USD restante={usd:.2f}")
    assert count > 0, "La posición no se abrió"
    assert btc > 0, "No hay BTC en posiciones"
    assert usd < 1000.0, "El USD debería haber disminuido tras comprar"


def verify_position_closed(wallet) -> None:
    """Verifica que la posición se cerró completamente."""
    count = wallet.positions_count
    btc = wallet.btc_en_posiciones()
    usd = wallet.get_usd_balance()
    print(f"    -> Posicion CERRADA: count={count}, BTC={btc:.8f}, USD={usd:.2f}")
    assert count == 0, f"La posición no se cerró (count={count})"
    assert btc < 1e-10, f"Queda BTC residual en posiciones ({btc:.8f})"


# ══════════════════════════════════════════════════════════════════════════════
# FIXTURES — Wallet
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def wallet_1000usd():
    """Wallet con 1000 USD y 3 posiciones máximas."""
    return MemoryWallet(usd_initial=1000.0, max_posiciones=3, slot_factor=1.0)


@pytest.fixture
def simulated_ob():
    """OrderBook simulado con 0.1% comisión."""
    return SimulatedOrderBook(commission_pct=0.1, max_posiciones=3)


# ══════════════════════════════════════════════════════════════════════════════
# FIXTURES — Mocks de clientes de exchange
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_hl_exchange():
    """Mock del Exchange de Hyperliquid para tests sin red."""
    exchange = MagicMock()
    exchange.market_open.return_value = {
        "status": "ok",
        "response": {"data": {"statuses": [{"filled": {"totalSz": "0.0002", "avgPx": "50000.0", "oid": 1001}}]}},
    }
    exchange.market_close.return_value = {
        "status": "ok",
        "response": {"data": {"statuses": [{"filled": {"totalSz": "0.0002", "avgPx": "50000.0", "oid": 1002}}]}},
    }
    return exchange


@pytest.fixture
def mock_hl_info():
    """Mock del Info de Hyperliquid para tests sin red."""
    info = MagicMock()
    info.all_mids.return_value = {"BTC": "50000.0"}
    info.user_state.return_value = {
        "marginSummary": {"accountValue": "1000.0"},
        "withdrawable": "1000.0",
        "assetPositions": [],
    }
    info.meta.return_value = {
        "universe": [{"name": "BTC", "szDecimals": 5}],
    }
    return info


# ══════════════════════════════════════════════════════════════════════════════
# FIXTURE — Precio de referencia
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def current_price() -> float:
    """Precio de referencia para tests (50k USD)."""
    return 50000.0