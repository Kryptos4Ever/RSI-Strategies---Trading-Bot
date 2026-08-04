"""
price_feed.py — Actor 1: Fuente de precios
═══════════════════════════════════════════
Responsabilidad única: entregar velas OHLCV al sistema.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from abc import ABC, abstractmethod
from typing import Iterator, List, Optional

from support.logger     import get_logger
from support.time_utils import to_epoch_s, to_iso
from support.types      import Candle

log = get_logger("price_feed")


class PriceFeed(ABC):
    """Contrato que deben cumplir todas las implementaciones de fuente de precios."""

    @abstractmethod
    def get_candles(self, start: int, end: int, symbol: str = "BTCUSDT") -> List[Candle]:
        """Retorna lista de velas en el rango [start, end] inclusive (timestamps Unix en segundos)."""

    def subscribe(self, callback) -> None:
        """Suscripción a streaming de velas (no implementado en feeds batch)."""
        raise NotImplementedError("Streaming no soportado en este feed.")

    def iter_candles(self, start: int, end: int, symbol: str = "BTCUSDT") -> Iterator[Candle]:
        for candle in self.get_candles(start, end, symbol):
            yield candle


def resolve_db_path(timeframe: str) -> str:
    """
    Construye la ruta a la base SQLite para una temporalidad dada.

    La base se busca en la carpeta ``DB/`` relativa al archivo que llama
    (se asume que quien llama está en la raíz del proyecto).  Si la variable
    de entorno ``TRADING_DB_PATH`` está definida se usa esa en su lugar.

    Ejemplos::

        resolve_db_path("1h")   → "DB/btc_1h.db"
        resolve_db_path("5m")   → "DB/btc_5m.db"
    """
    env_key = "TRADING_DB_PATH"
    default = os.path.join("DB", f"btc_{timeframe}.db")
    return os.environ.get(env_key, default)


class SQLiteFeed(PriceFeed):
    """Lee velas desde base SQLite (btc_1h.db)."""

    def __init__(self, db_path: str, table: Optional[str] = None) -> None:
        # Si no se especifica tabla, se deduce del nombre del archivo.
        # Ej: "DB/btc_1h.db" → "btc_1h"
        if table is None:
            table = os.path.splitext(os.path.basename(db_path))[0]
        # Validación de inyección SQL: isidentifier() garantiza que el nombre
        # de la tabla sea un identificador Python válido (solo letras, dígitos, _).
        # Complementariamente, se valida que symbol en get_candles() sea alfanumérico.
        if not table.isidentifier():
            raise ValueError(f"Nombre de tabla inválido: {table}")
        self.db_path = db_path
        self.table   = table
        log.info("SQLiteFeed inicializado", db=db_path, table=table)

    def get_candles(self, start: int, end: int, symbol: str = "BTCUSDT") -> List[Candle]:
        # Validar que symbol solo contenga caracteres alfanuméricos (A-Z, 0-9).
        # Esto previene inyección SQL si el símbolo se usara directamente en la query.
        if not symbol.isalnum():
            log.warning("Símbolo inválido, ignorando consulta", symbol=symbol)
            return []
        start_ms  = to_epoch_s(start) * 1000
        end_epoch = to_epoch_s(end)
        end_ms    = end_epoch * 1000
        rows      = self._query(start_ms, end_ms)
        candles   = [self._row_to_candle(r) for r in rows]
        log.info("velas cargadas", count=len(candles), start=to_iso(start), end=to_iso(end))
        return candles

    def _query(self, start_ms: int, end_ms: int) -> list:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                f"""SELECT timestamp, open, high, low, close, volume,
                           quote_volume, trades_count,
                           taker_buy_base_volume, taker_buy_quote_volume
                    FROM   {self.table}
                    WHERE  timestamp >= ? AND timestamp <= ?
                    ORDER  BY timestamp ASC""",
                (start_ms, end_ms),
            )
            return cur.fetchall()
        finally:
            conn.close()

    @staticmethod
    def _row_to_candle(row: tuple) -> Candle:
        (ts_ms, open_, high, low, close, volume,
         quote_vol, trades, taker_base, taker_quote) = row
        return Candle(
            ts                  = ts_ms // 1000,
            open                = float(open_),
            high                = float(high),
            low                 = float(low),
            close               = float(close),
            volume              = float(volume),
            quote_volume        = float(quote_vol)   if quote_vol  is not None else None,
            trades_count        = int(trades)         if trades     is not None else None,
            taker_buy_base_vol  = float(taker_base)  if taker_base is not None else None,
            taker_buy_quote_vol = float(taker_quote) if taker_quote is not None else None,
        )


class CSVFeed(PriceFeed):
    """Lee velas desde un archivo CSV."""

    _COL_ALIASES = {
        "ts":          ["timestamp", "ts", "time", "open_time"],
        "open":        ["open", "o"],
        "high":        ["high", "h"],
        "low":         ["low", "l"],
        "close":       ["close", "c"],
        "volume":      ["volume", "vol", "base_volume"],
        "trades":      ["trades_count", "trades", "number_of_trades"],
        "taker_base":  ["taker_buy_base_volume", "taker_buy_base_vol", "taker_base"],
        "taker_quote": ["taker_buy_quote_volume", "taker_buy_quote_vol", "taker_quote"],
    }

    def __init__(self, csv_path: str, delimiter: str = ",") -> None:
        self.csv_path  = csv_path
        self.delimiter = delimiter
        self._col_map: dict[str, str] = {}

    def get_candles(self, start: int, end: int, symbol: str = "BTCUSDT") -> List[Candle]:
        start_s = to_epoch_s(start)
        end_s   = to_epoch_s(end)
        candles: List[Candle] = []
        with open(self.csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=self.delimiter)
            if self._col_map == {} and reader.fieldnames:
                self._build_col_map(reader.fieldnames)
            for row in reader:
                ts = self._parse_ts(row)
                if ts < start_s or ts > end_s:
                    continue
                candles.append(self._row_to_candle(row, ts))
        return candles

    def _build_col_map(self, fieldnames: list[str]) -> None:
        lower_fields = {f.lower(): f for f in fieldnames}
        for canonical, aliases in self._COL_ALIASES.items():
            for alias in aliases:
                if alias in lower_fields:
                    self._col_map[canonical] = lower_fields[alias]
                    break

    def _get(self, row: dict, key: str, default=None):
        col = self._col_map.get(key)
        return row.get(col, default) if col else default

    def _parse_ts(self, row: dict) -> int:
        raw = self._get(row, "ts", 0)
        return to_epoch_s(int(float(raw)))

    def _row_to_candle(self, row: dict, ts: int) -> Candle:
        def f(key):  return float(self._get(row, key) or 0)
        def i(key):  v = self._get(row, key); return int(v)   if v else None
        def fo(key): v = self._get(row, key); return float(v) if v else None
        return Candle(
            ts=ts, open=f("open"), high=f("high"), low=f("low"),
            close=f("close"), volume=f("volume"),
            trades_count=i("trades"),
            taker_buy_base_vol=fo("taker_base"),
            taker_buy_quote_vol=fo("taker_quote"),
        )


def build_price_feed(mode: str = "local") -> PriceFeed:
    """Helper para construir un PriceFeed (solo modo local para backtest)."""
    try:
        import config_local as CL
        db_path = getattr(CL, "DB_PATH", "btc_1h.db")
        table   = getattr(CL, "DB_TABLE", "btc_1h")
    except ImportError:
        db_path = "btc_1h.db"
        table   = "btc_1h"
    return SQLiteFeed(db_path=db_path, table=table)