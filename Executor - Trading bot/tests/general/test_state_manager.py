"""
test_state_manager.py — Tests unitarios para state/state_manager.py
====================================================================
Cubre: state/state_manager.py
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from state.state_manager import (
    Checkpoint, MemoryStateManager, JSONStateManager, ResultsStateManager,
    build_state_manager,
)


class TestCheckpoint:
    """Tests para la dataclass Checkpoint."""

    def test_from_wallet(self):
        wallet = MagicMock()
        wallet.get_usd_balance.return_value = 500.0
        wallet.get_btc_balance.return_value = 0.05
        wallet.btc_en_posiciones.return_value = 0.03
        wallet.positions_count = 2
        wallet.portfolio_value.return_value = 2000.0

        cp = Checkpoint.from_wallet(wallet, close_price=50000.0, ts=1_700_000_000)
        assert cp.ts == 1_700_000_000
        assert cp.close_price == 50000.0
        assert cp.usd_balance == 500.0
        assert cp.btc_balance == 0.05
        assert cp.btc_en_pos == 0.03
        assert cp.positions_count == 2
        assert cp.portfolio_value == 2000.0
        assert cp.metadata is None
        assert cp.risk_state is None

    def test_from_wallet_with_metadata_and_risk(self):
        wallet = MagicMock()
        wallet.get_usd_balance.return_value = 1000.0
        wallet.get_btc_balance.return_value = 0.0
        wallet.btc_en_posiciones.return_value = 0.0
        wallet.positions_count = 0
        wallet.portfolio_value.return_value = 1000.0

        cp = Checkpoint.from_wallet(
            wallet, close_price=50000.0, ts=1_800_000_000,
            metadata={"estrategia": "test"}, risk_state={"circuit_open": False},
        )
        assert cp.metadata == {"estrategia": "test"}
        assert cp.risk_state == {"circuit_open": False}

    def test_to_dict_and_from_dict_roundtrip(self):
        original = Checkpoint(
            ts=1_700_000_000, close_price=50000.0, usd_balance=500.0,
            btc_balance=0.05, btc_en_pos=0.03, positions_count=2,
            portfolio_value=2000.0, metadata={"version": 1},
            risk_state={"circuit_open": False},
        )
        d = original.to_dict()
        restored = Checkpoint.from_dict(d)
        assert restored.ts == original.ts
        assert restored.close_price == original.close_price
        assert restored.usd_balance == original.usd_balance
        assert restored.risk_state == original.risk_state
        assert restored.metadata == original.metadata


class TestMemoryStateManager:

    @pytest.fixture
    def mgr(self):
        return MemoryStateManager()

    def test_save_and_load_latest(self, mgr):
        cp = Checkpoint(ts=1, close_price=100.0, usd_balance=1000.0,
                        btc_balance=0.0, btc_en_pos=0.0, positions_count=0,
                        portfolio_value=1000.0)
        mgr.save(cp)
        assert mgr.load_latest() == cp

    def test_load_latest_empty(self, mgr):
        assert mgr.load_latest() is None

    def test_history(self, mgr):
        cp1 = Checkpoint(ts=1, close_price=100.0, usd_balance=1000.0,
                         btc_balance=0.0, btc_en_pos=0.0, positions_count=0,
                         portfolio_value=1000.0)
        cp2 = Checkpoint(ts=2, close_price=101.0, usd_balance=900.0,
                         btc_balance=0.01, btc_en_pos=0.01, positions_count=1,
                         portfolio_value=1100.0)
        mgr.save(cp1)
        mgr.save(cp2)
        history = mgr.history()
        assert len(history) == 2
        assert history[0].ts == 1
        assert history[1].ts == 2

    def test_clear(self, mgr):
        cp = Checkpoint(ts=1, close_price=100.0, usd_balance=1000.0,
                        btc_balance=0.0, btc_en_pos=0.0, positions_count=0,
                        portfolio_value=1000.0)
        mgr.save(cp)
        mgr.clear()
        assert mgr.load_latest() is None
        assert len(mgr.history()) == 0

    def test_save_async(self, mgr):
        cp = Checkpoint(ts=1, close_price=100.0, usd_balance=1000.0,
                        btc_balance=0.0, btc_en_pos=0.0, positions_count=0,
                        portfolio_value=1000.0)
        import asyncio
        asyncio.run(mgr.save_async(cp))
        assert mgr.load_latest() == cp


class TestJSONStateManager:

    @pytest.fixture
    def tmp_path(self):
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    def test_save_and_load(self, tmp_path):
        path = tmp_path / "test.jsonl"
        mgr = JSONStateManager(path=path, max_checkpoints=10)
        cp = Checkpoint(ts=1, close_price=100.0, usd_balance=1000.0,
                        btc_balance=0.0, btc_en_pos=0.0, positions_count=0,
                        portfolio_value=1000.0)
        mgr.save(cp)
        assert mgr.checkpoint_count == 1
        mgr.close()

        # Cargar de nuevo desde archivo
        mgr2 = JSONStateManager(path=path, max_checkpoints=10)
        loaded = mgr2.load_latest()
        assert loaded is not None
        assert loaded.ts == 1
        assert loaded.usd_balance == 1000.0
        mgr2.close()

    def test_save_async(self, tmp_path):
        path = tmp_path / "async_test.jsonl"
        mgr = JSONStateManager(path=path, max_checkpoints=10)
        cp = Checkpoint(ts=2, close_price=200.0, usd_balance=500.0,
                        btc_balance=0.02, btc_en_pos=0.02, positions_count=1,
                        portfolio_value=1500.0)
        import asyncio
        asyncio.run(mgr.save_async(cp))
        assert mgr.checkpoint_count == 1
        mgr.close()

    def test_max_checkpoints(self, tmp_path):
        path = tmp_path / "max_test.jsonl"
        mgr = JSONStateManager(path=path, max_checkpoints=3)
        for i in range(5):
            cp = Checkpoint(ts=i, close_price=100.0 + i, usd_balance=1000.0,
                            btc_balance=0.0, btc_en_pos=0.0, positions_count=0,
                            portfolio_value=1000.0)
            mgr.save(cp)
        # Deberían mantenerse solo los últimos 3 en memoria
        assert mgr.checkpoint_count == 3
        assert mgr.load_latest().ts == 4
        mgr.close()

    def test_load_latest_empty(self, tmp_path):
        mgr = JSONStateManager(path=tmp_path / "empty.jsonl")
        assert mgr.load_latest() is None
        mgr.close()

    def test_clear(self, tmp_path):
        path = tmp_path / "clear_test.jsonl"
        mgr = JSONStateManager(path=path)
        cp = Checkpoint(ts=1, close_price=100.0, usd_balance=1000.0,
                        btc_balance=0.0, btc_en_pos=0.0, positions_count=0,
                        portfolio_value=1000.0)
        mgr.save(cp)
        mgr.clear()
        assert mgr.load_latest() is None
        mgr.close()

    def test_compact(self, tmp_path):
        path = tmp_path / "compact_test.jsonl"
        mgr = JSONStateManager(path=path, max_checkpoints=5)
        for i in range(10):
            cp = Checkpoint(ts=i, close_price=100.0, usd_balance=1000.0,
                            btc_balance=0.0, btc_en_pos=0.0, positions_count=0,
                            portfolio_value=1000.0)
            mgr.save(cp)
        assert mgr.checkpoint_count == 5
        mgr.compact()
        mgr.close()

        # Reabrir y verificar que solo hay 5
        mgr2 = JSONStateManager(path=path, max_checkpoints=10)
        assert mgr2.checkpoint_count == 5
        mgr2.close()

    def test_history(self, tmp_path):
        path = tmp_path / "history_test.jsonl"
        mgr = JSONStateManager(path=path)
        for i in range(3):
            cp = Checkpoint(ts=i, close_price=100.0 + i, usd_balance=1000.0,
                            btc_balance=0.0, btc_en_pos=0.0, positions_count=0,
                            portfolio_value=1000.0)
            mgr.save(cp)
        history = mgr.history()
        assert len(history) == 3
        assert [h.ts for h in history] == [0, 1, 2]
        mgr.close()


class TestBuildStateManager:

    def test_memory_mode(self):
        mgr = build_state_manager(mode="memory")
        assert isinstance(mgr, MemoryStateManager)

    def test_json_mode_with_path(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.jsonl"
            mgr = build_state_manager(mode="json", path=path)
            assert isinstance(mgr, JSONStateManager)
            assert mgr.file_path == path
            mgr.close()

    def test_json_mode_default_path(self):
        with tempfile.TemporaryDirectory() as d:
            original_cwd = os.getcwd()
            os.chdir(d)
            try:
                mgr = build_state_manager(mode="json")
                assert isinstance(mgr, JSONStateManager)
                mgr.close()
            finally:
                os.chdir(original_cwd)

    def test_invalid_mode_defaults_to_memory(self):
        """Un mode inválido devuelve MemoryStateManager por defecto."""
        mgr = build_state_manager(mode="invalid")
        assert isinstance(mgr, MemoryStateManager)

    def test_results_mode_restores_latest_checkpoint_from_live_results(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "live_results_papper.json"
            mgr = build_state_manager(mode="results", path=path)
            cp = Checkpoint(
                ts=10,
                close_price=100.0,
                usd_balance=900.0,
                btc_balance=0.0,
                btc_en_pos=1.0,
                positions_count=1,
                portfolio_value=1000.0,
                risk_state={"circuit_open": True, "peak_portfolio_value": 1000.0},
            )
            mgr.save(cp)

            restored = build_state_manager(mode="results", path=path)
            latest = restored.load_latest()

            assert isinstance(restored, ResultsStateManager)
            assert latest is not None
            assert latest.ts == 10
            assert latest.risk_state["circuit_open"] is True
