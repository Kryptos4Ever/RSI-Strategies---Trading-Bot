from __future__ import annotations

from state.results_store import ResultsStore, collateral_currency_for_environment
from state.state_manager import Checkpoint, build_state_manager


def test_results_store_writes_schema_v2_with_hyperliquid_collateral(tmp_path):
    path = tmp_path / "live_results_hyperliquid_testnet.json"
    store = ResultsStore(path, environment="hyperliquid_testnet", symbol="BTC")

    store.update(
        initial_capital_usd=1000.0,
        summary={"environment": "hyperliquid_testnet", "symbol": "BTC"},
        trade_history=[
            {
                "ts": 1,
                "datetime": "1970-01-01T00:00:01Z",
                "type": "BUY",
                "price": 100.0,
                "usd_spent": 10.0,
                "btc_bought": 0.1,
                "commission_usd": 0.01,
            }
        ],
    )

    data = store.load()
    assert data["schema_version"] == 2
    assert data["account_currency"] == "USD"
    assert data["collateral_currency"] == "USDC"
    assert data["initial_capital_usd"] == 1000.0
    assert data["trade_history"][0]["usd_spent"] == 10.0


def test_collateral_currency_can_be_overridden_for_future_environments():
    assert collateral_currency_for_environment("future_exchange", "USDT") == "USDT"


def test_results_state_manager_stores_checkpoints_inside_live_results(tmp_path):
    path = tmp_path / "live_results_papper.json"
    manager = build_state_manager(mode="results", path=path)
    checkpoint = Checkpoint(
        ts=10,
        close_price=100.0,
        usd_balance=900.0,
        btc_balance=0.0,
        btc_en_pos=1.0,
        positions_count=1,
        portfolio_value=1000.0,
        risk_state={"peak": 1000.0},
    )

    manager.save(checkpoint)

    store = ResultsStore(path)
    data = store.load()
    assert data["checkpoints"][0]["ts"] == 10
    assert data["risk_state"] == {"peak": 1000.0}
    assert data["last_closed_ts"] == 10
    assert manager.load_latest() == checkpoint
