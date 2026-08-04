"""
actors/array_clock.py — Clock que itera sobre una lista de velas en memoria
═══════════════════════════════════════════════════════════════════════════════
No necesita PriceFeed. Recibe directamente una lista de Candle.

USO EXCLUSIVO para el optimizador. NO usar en backtest normales.
"""
from __future__ import annotations

from typing import Iterator, List, Optional

from actors.clock import Clock
from support.types import Candle


class ArrayClock(Clock):
    """Itera sobre una lista de velas ya cargadas en memoria."""

    def __init__(self, candles: List[Candle], start: str = "", end: str = "", symbol: str = "BTCUSDT") -> None:
        self._candles = candles
        self._cursor: int = 0
        self._start = start
        self._end = end
        self._symbol = symbol

    @property
    def candles(self) -> List[Candle]:
        return list(self._candles)

    @property
    def start(self) -> str:
        return self._start

    @property
    def end(self) -> str:
        return self._end

    @property
    def symbol(self) -> str:
        return self._symbol

    def tick(self) -> Optional[Candle]:
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