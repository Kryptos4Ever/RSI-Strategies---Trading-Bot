"""
clock.py - Actor: reloj/director de ciclos.

Responsabilidad unica: decidir cuando se ejecuta cada ciclo de la estrategia.
En modo live, el reloj efectivo es el WebSocket feed que produce velas en
tiempo real. LiveClock es un helper minimo para obtener timestamps y saber si
se esta en modo live.
"""
from __future__ import annotations

import time
from typing import Optional

from support.logger import get_logger

log = get_logger("clock")


class LiveClock:
    def __init__(self, interval_seconds: int = 3600, live: bool = True):
        self.interval_seconds = interval_seconds
        self.live = live

    def now(self) -> int:
        return int(time.time())

    def is_live(self) -> bool:
        return self.live

    def next_candle_ts(self, current_ts: Optional[int] = None) -> int:
        current = self.now() if current_ts is None else current_ts
        return ((current // self.interval_seconds) + 1) * self.interval_seconds
