"""
clock.py — Actor: Reloj / Director de ciclos
═══════════════════════════════════════════════
Responsabilidad única: decidir CUÁNDO se ejecuta cada ciclo de la estrategia.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, List, Optional

from actors.price_feed  import Candle, PriceFeed
from support.logger     import get_logger
from support.time_utils import TimeInput, to_epoch_s, to_iso

log = get_logger("clock")


class Clock(ABC):
    """Contrato para todas las implementaciones del reloj."""

    @abstractmethod
    def tick(self) -> Optional[Candle]:
        """Retorna la siguiente vela. None = fin del stream."""

    @abstractmethod
    def reset(self) -> None:
        """Reinicia el clock al estado inicial."""

    def __iter__(self) -> Iterator[Candle]:
        while (candle := self.tick()) is not None:
            yield candle


class LocalClock(Clock):
    """Itera velas desde un PriceFeed local (SQLite o CSV)."""

    def __init__(self, feed: PriceFeed, start: TimeInput, end: TimeInput, symbol: str = "BTCUSDT") -> None:
        self._feed    = feed
        self._start   = start
        self._end     = end
        self._symbol  = symbol
        self._candles: list[Candle] = []
        self._cursor:  int  = 0
        self._loaded:  bool = False
        log.info("LocalClock inicializado", start=to_iso(to_epoch_s(start)),
                 end=to_iso(to_epoch_s(end)), symbol=symbol)

    @property
    def candles(self) -> List[Candle]:
        """Retorna las velas cargadas (para que el engine no duplique consultas)."""
        if not self._loaded:
            self._load()
        return list(self._candles)

    def tick(self) -> Optional[Candle]:
        if not self._loaded:
            self._load()
        if self._cursor >= len(self._candles):
            return None
        candle = self._candles[self._cursor]
        self._cursor += 1
        return candle

    def reset(self) -> None:
        self._cursor = 0

    @property
    def total_candles(self) -> int:
        return len(self._candles)

    @property
    def progress_pct(self) -> float:
        if not self._candles:
            return 0.0
        return self._cursor / len(self._candles) * 100.0

    def _load(self) -> None:
        self._candles = self._feed.get_candles(start=self._start, end=self._end, symbol=self._symbol)
        self._cursor = 0
        self._loaded = True


def build_clock(feed: PriceFeed, start: TimeInput = None,
                end: TimeInput = None, symbol: str = None) -> LocalClock:
    """DEPRECATED: Helper legacy. Backtest_Dual_Bands.py construye el LocalClock
    explícitamente. Mantenido solo por compatibilidad con scripts antiguos."""
    try:
        import config_local as CL
        _start  = start  or getattr(CL, "FECHA_INICIO", None)
        _end    = end    or getattr(CL, "FECHA_FIN",    None)
        _symbol = symbol or getattr(CL, "SYMBOL",       "BTCUSDT")
    except ImportError:
        _start  = start
        _end    = end
        _symbol = symbol or "BTCUSDT"

    if not _start or not _end:
        raise ValueError("start y end son requeridos para LocalClock.")
    return LocalClock(feed=feed, start=_start, end=_end, symbol=_symbol)