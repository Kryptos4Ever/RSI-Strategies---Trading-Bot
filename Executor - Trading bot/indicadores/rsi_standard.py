"""
indicadores/rsi_standard.py — Relative Strength Index (RSI) Estándar (Cutler's RSI)
═══════════════════════════════════════════════════════════════════════════════════
Implementación del RSI estándar (Cutler's RSI) con ventana deslizante simple.

A diferencia del RSI de Wilder's Smoothing (que usa decaimiento exponencial y
depende de TODO el histórico), el RSI estándar solo evalúa las últimas `period`
velas: mantiene una ventana deslizante de los últimos `period` cambios y recalcula
las sumas de ganancias/pérdidas de forma incremental.

Fórmula:
    RSI = 100 - (100 / (1 + RS))
    RS = Suma(Ganancias últimos period) / Suma(Pérdidas últimos period)

Uso:
    rsi = StandardRSIEngine(period=14)
    for candle in candles:
        rsi_value = rsi.update(candle.close)
        if rsi_value is not None:
            print(f"RSI: {rsi_value:.2f}")
"""
from __future__ import annotations

from collections import deque
from typing import List, Optional

from support.types import Candle


class StandardRSIEngine:
    """
    Cálculo de RSI (Relative Strength Index) estándar (Cutler's RSI).

    Usa una ventana deslizante de los últimos `period` cambios. Solo las últimas
    `period` velas influyen en el valor; las anteriores se descartan.

    El RSI necesita `period` + 1 velas para comenzar a emitir valores.
    """

    def __init__(self, period: int = 14):
        if period < 2:
            raise ValueError(f"RSI period debe ser >= 2, got {period}")
        self._period: int = period
        self._prev_close: Optional[float] = None
        # Ventana deslizante de los últimos `period` cambios (gain, loss)
        self._gains: deque = deque(maxlen=period)
        self._losses: deque = deque(maxlen=period)
        self._sum_gain: float = 0.0
        self._sum_loss: float = 0.0
        self._count: int = 0
        self._value: Optional[float] = None

    @property
    def value(self) -> Optional[float]:
        """Último valor calculado de RSI."""
        return self._value

    @property
    def period(self) -> int:
        return self._period

    def reset(self) -> None:
        """Resetea el estado interno para comenzar desde cero."""
        self._prev_close = None
        self._gains.clear()
        self._losses.clear()
        self._sum_gain = 0.0
        self._sum_loss = 0.0
        self._count = 0
        self._value = None

    def update(self, close: float) -> Optional[float]:
        """
        Actualiza el RSI con un nuevo precio de cierre.

        Args:
            close: Precio de cierre de la vela actual.

        Returns:
            float con el valor de RSI (0-100), o None si aún no hay suficientes datos.
        """
        if self._prev_close is None:
            # Primera vela: solo guardar el precio
            self._prev_close = close
            return None

        # Calcular cambio
        change = close - self._prev_close
        gain = max(0.0, change)
        loss = max(0.0, -change)
        self._prev_close = close

        # Si la ventana está llena, restar el cambio más antiguo antes de agregar
        if len(self._gains) == self._period:
            old_gain = self._gains[0]
            old_loss = self._losses[0]
            self._sum_gain -= old_gain
            self._sum_loss -= old_loss

        # Agregar el cambio actual a la ventana
        self._gains.append(gain)
        self._losses.append(loss)
        self._sum_gain += gain
        self._sum_loss += loss
        self._count += 1

        if self._count < self._period:
            # Aún no hay suficientes datos
            return None

        self._value = self._calc_rsi(self._sum_gain, self._sum_loss)
        return self._value

    @staticmethod
    def _calc_rsi(sum_gain: float, sum_loss: float) -> float:
        """Calcula RSI a partir de la suma de ganancias y pérdidas."""
        if sum_loss == 0:
            return 100.0
        rs = sum_gain / sum_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _rs_to_rsi(rs: float) -> float:
        """Convierte RS a RSI."""
        if rs < 0:
            return 0.0
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _rsi_to_rs(rsi: float) -> float:
        """Convierte RSI a RS.

        RS = 100/(100 - RSI) - 1

        Args:
            rsi: Valor de RSI (0-100).

        Returns:
            RS correspondiente.
        """
        if rsi >= 100.0:
            return float('inf')
        if rsi <= 0.0:
            return 0.0
        return 100.0 / (100.0 - rsi) - 1.0

    def price_for_rsi(self, target_rsi: float) -> float:
        """
        Calcula el precio teórico necesario para alcanzar un RSI objetivo
        en la PRÓXIMA actualización, dado el estado interno actual.

        NO modifica el estado interno del StandardRSIEngine.

        Usa la fórmula inversa del RSI estándar (ventana deslizante):

        Sea:
            Sg = suma de ganancias de la ventana actual
            Sl = suma de pérdidas de la ventana actual
            g_old = ganancia más antigua de la ventana (se descarta al desplazar)
            l_old = pérdida más antigua de la ventana (se descarta al desplazar)

        Caso A (precio > prev_close, loss=0):
            Sg_new = Sg - g_old + (precio - prev)
            Sl_new = Sl - l_old
            RS_target = Sg_new / Sl_new
            precio = prev + RS_target * (Sl - l_old) - (Sg - g_old)

        Caso B (precio < prev_close, gain=0):
            Sg_new = Sg - g_old
            Sl_new = Sl - l_old + (prev - precio)
            RS_target = Sg_new / Sl_new
            precio = prev - ((Sg - g_old) / RS_target - (Sl - l_old))

        Args:
            target_rsi: RSI objetivo (0-100) que queremos alcanzar.

        Returns:
            Precio calculado. Puede ser > o < que prev_close dependiendo del caso.
            Retorna prev_close si no se puede calcular (datos insuficientes).
        """
        if self._prev_close is None or self._count < self._period:
            return self._prev_close or 0.0

        target_rs = self._rsi_to_rs(target_rsi)
        prev = self._prev_close
        Sg = self._sum_gain
        Sl = self._sum_loss

        # Cambios más antiguos de la ventana (se descartan al desplazar)
        g_old = self._gains[0] if self._gains else 0.0
        l_old = self._losses[0] if self._losses else 0.0

        # ── Caso A: precio > prev_close, loss = 0 ─────────────────────────
        # Sg_new = Sg - g_old + (precio - prev)
        # Sl_new = Sl - l_old
        # RS_target = Sg_new / Sl_new
        # precio = prev + RS_target * (Sl - l_old) - (Sg - g_old)
        price_a = prev + target_rs * (Sl - l_old) - (Sg - g_old)

        # ── Caso B: precio < prev_close, gain = 0 ─────────────────────────
        # Sg_new = Sg - g_old
        # Sl_new = Sl - l_old + (prev - precio)
        # RS_target = Sg_new / Sl_new
        # precio = prev - ((Sg - g_old) / RS_target - (Sl - l_old))
        price_b = prev - ((Sg - g_old) / target_rs - (Sl - l_old)) if target_rs > 0 else prev

        # ── Guard against negative prices ─────────────────────────────────
        _MIN_PRICE = max(prev * 1e-10, 1e-10)
        price_a = max(price_a, _MIN_PRICE)
        price_b = max(price_b, _MIN_PRICE)

        # ── Validación y selección de caso ────────────────────────────────
        if Sl == 0 and Sg == 0:
            # Sin ganancia ni pérdida histórica
            if target_rsi >= 50.0:
                return prev * 1.001  # subida mínima
            else:
                return prev * 0.999  # bajada mínima

        if Sl == 0 and target_rsi < 100.0:
            # Sin pérdidas históricas → necesitamos Caso B (generar pérdida)
            if price_b < prev:
                return price_b
            return prev * 0.999

        if Sg == 0 and target_rsi > 0.0:
            # Sin ganancias históricas → necesitamos Caso A (generar ganancia)
            if price_a > prev:
                return price_a
            return prev * 1.001

        # Caso normal: probamos ambos y elegimos el que cumple la condición
        if price_a > prev:
            return price_a

        if price_b < prev:
            return price_b

        # Si ninguno funciona, retornar prev_close
        return prev

    def calculate(self, closes: List[float]) -> List[Optional[float]]:
        """
        Calcula RSI para una secuencia completa de precios de cierre.

        Args:
            closes: Lista de precios de cierre en orden cronológico.

        Returns:
            Lista de valores RSI (None para las primeras `period` posiciones).
        """
        self.reset()
        results: List[Optional[float]] = []
        for c in closes:
            results.append(self.update(c))
        return results

    def calculate_from_candles(self, candles: List[Candle]) -> List[Optional[float]]:
        """
        Calcula RSI desde una lista de velas OHLCV.

        Args:
            candles: Lista de velas en orden cronológico.

        Returns:
            Lista de valores RSI (None para las primeras `period` posiciones).
        """
        closes = [c.close for c in candles]
        return self.calculate(closes)