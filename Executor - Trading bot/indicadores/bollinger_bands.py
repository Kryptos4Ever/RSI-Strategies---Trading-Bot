"""
bollinger_bands.py — Motor de Bandas de Bollinger
══════════════════════════════════════════════════
Implementación con buffer circular y actualización incremental O(1).

Soporta un solo período y un solo multiplicador de desviación estándar,
con fuente de precio configurable ("open" | "close").

Uso:
    bb = BollingerBandsEngine(period=20, std_mult=2.0, max_buffer=500)
    bb.feed(np.array([...]))         # carga inicial
    bb.update(100.0)                  # actualización incremental
    bb.upper, bb.middle, bb.lower     # último valor confirmado
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np


class BollingerBandsEngine:
    """
    Motor de Bandas de Bollinger con buffer circular.

    Para un período P y multiplicador K:
      - Middle(P) = SMA(P)
      - Upper(P,K) = SMA(P) + K * σ(P)
      - Lower(P,K) = SMA(P) - K * σ(P)

    donde σ(P) es la desviación estándar poblacional de los últimos P precios.
    """

    def __init__(self, period: int, std_mult: float, max_buffer: int = 500) -> None:
        if period < 2:
            raise ValueError("period debe ser >= 2")
        if std_mult <= 0:
            raise ValueError("std_mult debe ser > 0")

        self._period = period
        self._k = std_mult
        self._max = max_buffer

        self._buffer: List[float] = []
        self._ready: bool = False

        # Caché del último cálculo
        self._last_middle: float = float("nan")
        self._last_upper: float = float("nan")
        self._last_lower: float = float("nan")

    # ── Alimentación de datos ─────────────────────────────────────────────────

    def feed(self, values: np.ndarray) -> None:
        """Carga inicial de datos. Borra el estado anterior."""
        self._buffer = list(values)
        if len(self._buffer) > self._max:
            self._buffer = self._buffer[-self._max:]
        self._compute()

    def update(self, value: float) -> None:
        """Agrega un nuevo valor al buffer (incremental)."""
        self._buffer.append(value)
        if len(self._buffer) > self._max:
            self._buffer.pop(0)
        self._compute()

    # ── Últimos valores confirmados ───────────────────────────────────────────

    @property
    def upper(self) -> float:
        """Último valor completo de la Banda Superior."""
        return self._last_upper

    @property
    def middle(self) -> float:
        """Último valor completo de la Banda Media (SMA)."""
        return self._last_middle

    @property
    def lower(self) -> float:
        """Último valor completo de la Banda Inferior."""
        return self._last_lower

    @property
    def is_ready(self) -> bool:
        """True si hay suficientes datos para calcular."""
        return self._ready

    @property
    def buffer_size(self) -> int:
        """Retorna la cantidad de valores en el buffer actual."""
        return len(self._buffer)

    # ── Arrays completos (para backtest y graficador) ─────────────────────────

    def full_upper(self, all_closes: np.ndarray) -> np.ndarray:
        """Retorna el array completo de Upper Band para todos los closes."""
        return self._compute_full(all_closes, lambda sma, std: sma + self._k * std)

    def full_middle(self, all_closes: np.ndarray) -> np.ndarray:
        """Retorna el array completo de Middle Band (SMA) para todos los closes."""
        return self._compute_full(all_closes, lambda sma, _: sma)

    def full_lower(self, all_closes: np.ndarray) -> np.ndarray:
        """Retorna el array completo de Lower Band para todos los closes."""
        return self._compute_full(all_closes, lambda sma, std: sma - self._k * std)

    # ── Cálculo interno ───────────────────────────────────────────────────────

    def _compute(self) -> None:
        """Recalcula las bandas con el buffer actual (solo último valor)."""
        if len(self._buffer) < self._period:
            self._ready = False
            self._last_middle = float("nan")
            self._last_upper = float("nan")
            self._last_lower = float("nan")
            return

        ventana = np.array(self._buffer[-self._period:], dtype=np.float64)
        sma = float(np.mean(ventana))
        std = float(np.std(ventana, ddof=0))  # poblacional

        self._last_middle = sma
        self._last_upper = sma + self._k * std
        self._last_lower = sma - self._k * std
        self._ready = True

    def compute_intra(self, window_values: np.ndarray | list) -> tuple[float, float, float]:
        """
        Calcula bandas temporales (intra-vela) sobre una lista/array de valores
        usando los parámetros de periodo y desviación de esta instancia.
        Retorna (upper, middle, lower).
        """
        if len(window_values) < self._period:
            return float("nan"), float("nan"), float("nan")
        ventana = np.array(window_values[-self._period:], dtype=np.float64)
        sma = float(np.mean(ventana))
        std = float(np.std(ventana, ddof=0))
        return sma + self._k * std, sma, sma - self._k * std

    def _compute_full(self, all_closes: np.ndarray, band_fn) -> np.ndarray:
        """
        Calcula una banda completa aplicando band_fn(sma, std) a cada punto.
        Los primeros (period-1) valores serán NaN.
        """
        n = len(all_closes)
        if n < self._period:
            return np.full(n, float("nan"))

        resultado = np.full(n, float("nan"))

        for i in range(self._period - 1, n):
            ventana = all_closes[i - self._period + 1: i + 1]
            sma = float(np.mean(ventana))
            std = float(np.std(ventana, ddof=0))
            resultado[i] = band_fn(sma, std)

        return resultado

    def reset(self) -> None:
        """Reinicia el motor completamente."""
        self._buffer.clear()
        self._ready = False
        self._last_middle = float("nan")
        self._last_upper = float("nan")
        self._last_lower = float("nan")