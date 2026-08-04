"""
tests/conftest.py — Fixtures compartidos para toda la suite de tests
═════════════════════════════════════════════════════════════════════
Proporciona:
  - Velas OHLCV sintéticas reproducibles
  - Wallets preconfiguradas
  - OrderBooks simulados
  - Mocks de feeds y clientes externos
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from typing import List
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Asegurar que el raíz del proyecto esté en el path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support.types import Candle
from actors.wallet import MemoryWallet, JSONWallet
from actors.order_book import SimulatedLimitGTCOrderBook, OrderSide
from risk.risk_manager import RiskManager, build_risk_manager
from state.state_manager import MemoryStateManager


# ══════════════════════════════════════════════════════════════════════════════
# MARKERS
# ══════════════════════════════════════════════════════════════════════════════

def pytest_addoption(parser):
    """Añade opción --run-network para ejecutar tests que requieren conexión real."""
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="Ejecutar tests que requieren conexión a red real (Binance Testnet, etc.)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: test unitario (sin red, sin archivos externos)")
    config.addinivalue_line("markers", "integration: test de integración (mocks de red)")
    config.addinivalue_line("markers", "e2e: test end-to-end completo")
    config.addinivalue_line("markers", "requires_network: requiere acceso a red real (skip en CI)")
    config.addinivalue_line("markers", "requires_keys: requiere claves de API reales")
    config._run_network = config.getoption("--run-network", default=False)


def pytest_collection_modifyitems(config, items):
    """Skip automático de tests requires_network si no se pasó --run-network."""
    if config.getoption("--run-network"):
        return
    skip_network = pytest.mark.skip(
        reason="Usa --run-network para activar. Requiere conexión real a internet y claves API."
    )
    for item in items:
        if "requires_network" in item.keywords:
            item.add_marker(skip_network)


# ══════════════════════════════════════════════════════════════════════════════
# FÁBRICA DE VELAS SINTÉTICAS
# ══════════════════════════════════════════════════════════════════════════════

def make_candle(
    ts: int = 1_700_000_000,
    open_: float = 30_000.0,
    high: float = 31_000.0,
    low: float = 29_000.0,
    close: float = 30_500.0,
    volume: float = 100.0,
) -> Candle:
    return Candle(
        ts=ts, open=open_, high=high, low=low, close=close, volume=volume
    )


def make_candle_sequence(
    n: int = 50,
    base_price: float = 30_000.0,
    volatility: float = 500.0,
    seed: int = 42,
    ts_start: int = 1_700_000_000,
    interval_s: int = 3600,
) -> List[Candle]:
    """
    Genera una secuencia de N velas OHLCV sintéticas y reproducibles.
    Usa random walk con semilla fija para reproducibilidad.
    """
    rng = np.random.default_rng(seed)
    candles = []
    price = base_price

    for i in range(n):
        change = rng.normal(0, volatility / 10)
        open_ = price
        close = max(1.0, price + change)
        high  = max(open_, close) + abs(rng.normal(0, volatility / 20))
        low   = min(open_, close) - abs(rng.normal(0, volatility / 20))
        vol   = abs(rng.normal(100, 20))
        ts    = ts_start + i * interval_s

        candles.append(Candle(
            ts=ts, open=round(open_, 2), high=round(high, 2),
            low=round(low, 2), close=round(close, 2),
            volume=round(vol, 4),
        ))
        price = close

    return candles


# ══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def candle():
    """Vela simple para tests unitarios."""
    return make_candle()


@pytest.fixture
def candle_sequence():
    """Secuencia de 50 velas sintéticas."""
    return make_candle_sequence(n=50)


@pytest.fixture
def long_candle_sequence():
    """Secuencia de 200 velas para tests de backtesting."""
    return make_candle_sequence(n=200, base_price=30_000.0, volatility=1000.0)


@pytest.fixture
def memory_wallet():
    """Wallet en memoria con 1000 USDT y 3 posiciones máximas."""
    return MemoryWallet(usd_initial=1000.0, max_posiciones=3)


@pytest.fixture
def json_wallet(tmp_path):
    """Wallet JSON en directorio temporal."""
    json_file = tmp_path / "test_wallet.json"
    return JSONWallet(
        usd_initial=1000.0,
        max_posiciones=3,
        json_path=str(json_file),
    )


@pytest.fixture
def simulated_ob():
    """OrderBook simulado GTC con 0.1% de comisión y 3 posiciones máx."""
    return SimulatedLimitGTCOrderBook(commission_pct=0.1, max_posiciones=3)


@pytest.fixture
def risk_manager():
    """RiskManager básico con drawdown ilimitado."""
    return RiskManager(usd_initial=1000.0, max_drawdown_pct=100.0)


@pytest.fixture
def risk_manager_strict():
    """RiskManager estricto con drawdown al 25%."""
    return RiskManager(usd_initial=1000.0, max_drawdown_pct=25.0)


@pytest.fixture
def state_manager():
    return MemoryStateManager()


@pytest.fixture
def mock_telegram(monkeypatch):
    """Parchea TelegramNotifier para que no envíe nada durante tests."""
    mock = MagicMock()
    mock.notify = MagicMock()
    mock.send_test = MagicMock(return_value=True)
    return mock


@pytest.fixture
def mock_binance_client():
    """Mock del cliente de Binance para tests sin red."""
    client = MagicMock()
    client.get_account.return_value = {
        "balances": [
            {"asset": "USDT", "free": "1000.00", "locked": "0.00"},
            {"asset": "BTC",  "free": "0.01",    "locked": "0.00"},
        ]
    }
    client.order_market_buy.return_value = {
        "status": "FILLED",
        "fills": [
            {"qty": "0.001", "quoteQty": "30.0", "commission": "0.03"}
        ]
    }
    client.order_market_sell.return_value = {
        "status": "FILLED",
        "fills": [
            {"qty": "0.001", "quoteQty": "30.5", "commission": "0.0305"}
        ]
    }
    return client


@pytest.fixture
def mock_hl_info():
    """Mock del cliente Info de Hyperliquid para tests sin red."""
    info = MagicMock()
    info.user_state.return_value = {
        "marginSummary": {"accountValue": "1000.0"},
        "assetPositions": [],
    }
    info.candles_snapshot.return_value = [
        {
            "t": str(1_700_000_000 * 1000 + i * 3_600_000),
            "T": str(1_700_000_000 * 1000 + (i + 1) * 3_600_000 - 1),
            "o": "30000.0", "h": "31000.0", "l": "29000.0",
            "c": "30500.0", "v": "100.0", "n": 500,
        }
        for i in range(10)
    ]
    return info