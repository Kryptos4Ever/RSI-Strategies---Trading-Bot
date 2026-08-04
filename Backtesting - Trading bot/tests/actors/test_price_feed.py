"""
tests/actors/test_price_feed.py — Smoke tests para PriceFeed
=============================================================
Cubre: actors/price_feed.py
"""
from __future__ import annotations

import csv
import os
import sys
import sqlite3
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from support.types import Candle
from actors.price_feed import SQLiteFeed, CSVFeed


class TestSQLiteFeed:
    """Tests básicos de SQLiteFeed."""

    def test_init_rejects_invalid_table_name(self):
        """SQLiteFeed debe rechazar nombres de tabla inválidos."""
        with pytest.raises(ValueError, match="Nombre de tabla inválido"):
            SQLiteFeed("test.db", table="malicious; DROP TABLE")

    def test_init_accepts_valid_table(self):
        """SQLiteFeed debe aceptar nombres de tabla válidos."""
        feed = SQLiteFeed("test.db", table="btc_1h")
        assert feed.db_path == "test.db"
        assert feed.table == "btc_1h"

    def test_subscribe_raises_not_implemented(self):
        """SQLiteFeed.subscribe() debe lanzar NotImplementedError."""
        feed = SQLiteFeed("test.db", table="btc_1h")
        with pytest.raises(NotImplementedError):
            feed.subscribe(lambda c: None)

    def test_get_candles_returns_empty_for_missing_db(self):
        """Con DB vacía, get_candles debe retornar lista vacía (no crash)."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            # Crear DB con tabla con columnas reales que la query necesita
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE btc_1h (
                    timestamp INTEGER, open REAL, high REAL, low REAL,
                    close REAL, volume REAL, quote_volume REAL,
                    trades_count INTEGER, taker_buy_base_volume REAL,
                    taker_buy_quote_volume REAL
                )
            """)
            conn.close()
            feed = SQLiteFeed(db_path, table="btc_1h")
            candles = feed.get_candles(start=1_700_000_000, end=1_800_000_000)
            assert candles == []
        finally:
            os.unlink(db_path)

    def test_get_candles_rejects_invalid_symbol(self):
        """Símbolo no alfanumérico debe retornar lista vacía."""
        feed = SQLiteFeed("test.db", table="btc_1h")
        candles = feed.get_candles(start=1_700_000_000, end=1_800_000_000, symbol="BTC/USDT")
        assert candles == []


class TestCSVFeed:
    """Tests básicos de CSVFeed."""

    def test_init_creates_instance(self):
        """CSVFeed debe construirse correctamente."""
        feed = CSVFeed("test.csv")
        assert feed.csv_path == "test.csv"
        assert feed.delimiter == ","

    def test_subscribe_raises_not_implemented(self):
        """CSVFeed.subscribe() debe lanzar NotImplementedError."""
        feed = CSVFeed("test.csv")
        with pytest.raises(NotImplementedError):
            feed.subscribe(lambda c: None)

    def test_get_candles_from_csv(self):
        """CSVFeed debe leer velas desde un archivo CSV."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("timestamp,open,high,low,close,volume\n")
            f.write("1700000000,100.0,101.0,99.0,100.5,100.0\n")
            f.write("1700003600,101.0,102.0,100.0,101.5,100.0\n")
            csv_path = f.name

        feed = CSVFeed(csv_path)
        candles = feed.get_candles(start=1_700_000_000, end=1_800_000_000)

        assert len(candles) == 2
        assert isinstance(candles[0], Candle)
        assert candles[0].close == 100.5
        assert candles[1].close == 101.5

    def test_get_candles_filters_by_time_range(self):
        """CSVFeed debe filtrar velas fuera del rango."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("timestamp,open,high,low,close,volume\n")
            f.write("1700000000,100.0,101.0,99.0,100.5,100.0\n")
            f.write("1800000000,200.0,201.0,199.0,200.5,100.0\n")
            csv_path = f.name

        feed = CSVFeed(csv_path)
        candles = feed.get_candles(start=1_700_000_001, end=1_800_000_000)

        assert len(candles) == 1
        assert candles[0].close == 200.5

    def test_csv_with_column_aliases(self):
        """CSVFeed debe reconocer columnas con nombres alternativos."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("Time,Open,High,Low,Close,Vol\n")
            f.write("1700000000,100.0,101.0,99.0,100.5,100.0\n")
            csv_path = f.name

        feed = CSVFeed(csv_path)
        candles = feed.get_candles(start=1_700_000_000, end=1_800_000_000)
        assert len(candles) == 1
        assert candles[0].close == 100.5
if __name__ == "__main__":
    from tests._direct_runner import run_current_test_file

    raise SystemExit(run_current_test_file(__file__))
