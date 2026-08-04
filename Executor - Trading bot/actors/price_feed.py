"""
price_feed.py — Actor 1: Fuente de precios
═══════════════════════════════════════════
Responsabilidad única: entregar velas OHLCV al sistema.
"""
from __future__ import annotations

import csv
import sqlite3
from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Iterator, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

from support.logger     import get_logger
from support.time_utils import to_epoch_s, to_iso
from support.types      import Candle

log = get_logger("price_feed")


class PriceFeed(ABC):
    """Contrato que deben cumplir todas las implementaciones de fuente de precios."""

    @abstractmethod
    def get_candles(self, start: int, end: int, symbol: str = "BTCUSDT") -> List[Candle]:
        """Retorna lista de velas en el rango [start, end] inclusive (timestamps Unix en segundos)."""

    @abstractmethod
    def subscribe(self, callback: Callable[[Candle], None], symbol: str = "BTCUSDT") -> None:
        """Suscribe un callback para velas en tiempo real."""

    def iter_candles(self, start: int, end: int, symbol: str = "BTCUSDT") -> Iterator[Candle]:
        for candle in self.get_candles(start, end, symbol):
            yield candle


class AsyncFeed(ABC):
    """
    Feed asíncrono para datos en tiempo real.
    Contrato que deben implementar los feeds WebSocket de cada entorno.

    La sesión aiohttp es inyectada desde el engine (Opción B, ver plan), no creada internamente.
    """

    @abstractmethod
    def stream(
        self,
        session: "aiohttp.ClientSession",
        symbol: str = "BTCUSDT",
        interval: str = "1h",
    ) -> "AsyncIterator[Tuple[Candle, bool]]":
        """
        AsyncIterator que produce (Candle, is_closed).
        session: inyectada desde el engine (único ClientSession compartido).
        """

    @property
    @abstractmethod
    def latest_bid(self) -> Optional[float]:
        """Último mejor bid recibido vía bookTicker/l2Book."""
        ...

    @property
    @abstractmethod
    def latest_ask(self) -> Optional[float]:
        """Último mejor ask recibido vía bookTicker/l2Book."""
        ...

    @property
    @abstractmethod
    def latest_mid(self) -> Optional[float]:
        """Mid-price calculado de (bid + ask) / 2, o None si no hay datos."""
        ...



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


def build_price_feed(mode: str = "papper") -> PriceFeed:
    """Helper para construir un PriceFeed."""
    if mode in ("hyperliquid", "hyperliquid_mainnet"):
        from actors.hyperliquid_mainnet.hyperliquid_mainnet_feed import HyperliquidWSFeed
        return HyperliquidWSFeed()
    if mode == "hyperliquid_testnet":
        from actors.hyperliquid_testnet.hyperliquid_testnet_feed import HyperliquidWSFeed
        return HyperliquidWSFeed()
    if mode == "papper":
        from actors.papper.papper_feed import PapperWSFeed
        return PapperWSFeed()
    raise ValueError(f"Modo no soportado para PriceFeed: {mode}")
