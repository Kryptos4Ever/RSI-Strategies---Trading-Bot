"""
indicadores/rsi.py — Relative Strength Index (RSI)
═══════════════════════════════════════════════════════
Implementación del RSI Wilder's Smoothing.

Uso:
    rsi = RSIEngine(period=14)
    for candle in candles:
        rsi_value = rsi.update(candle.close)
        if rsi_value is not None:
            print(f"RSI: {rsi_value:.2f}")
"""
from __future__ import annotations

from typing import List, Optional

from support.types import Candle


class RSIEngine:
    """
    Cálculo de RSI (Relative Strength Index) con Wilders Smoothing.

    Fórmula:
        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss (Wilder's Smoothing)

    El RSI necesita `period` + 1 velas para comenzar a emitir valores.
    """

    def __init__(self, period: int = 14):
        if period < 2:
            raise ValueError(f"RSI period debe ser >= 2, got {period}")
        self._period: int = period
        self._prev_close: Optional[float] = None
        self._avg_gain: float = 0.0
        self._avg_loss: float = 0.0
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
        self._avg_gain = 0.0
        self._avg_loss = 0.0
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

        if self._count < self._period:
            # Período inicial: acumular sumas
            self._avg_gain += gain
            self._avg_loss += loss
            self._count += 1

            if self._count == self._period:
                # Primer cálculo: promedios simples
                self._avg_gain /= self._period
                self._avg_loss /= self._period
                self._value = self._calc_rsi(self._avg_gain, self._avg_loss)
                return self._value
            return None

        # Wilder's Smoothing: promedios suavizados
        self._avg_gain = (self._avg_gain * (self._period - 1) + gain) / self._period
        self._avg_loss = (self._avg_loss * (self._period - 1) + loss) / self._period
        self._value = self._calc_rsi(self._avg_gain, self._avg_loss)
        return self._value

    @staticmethod
    def _calc_rsi(avg_gain: float, avg_loss: float) -> float:
        """Calcula RSI a partir de ganancia y pérdida promedio."""
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
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
        
        NO modifica el estado interno del RSIEngine.
        
        Usa la fórmula inversa de Wilder's Smoothing:
        
        Caso A (precio > prev_close, loss=0):
            precio = prev_close + (period-1) * (RS_target * avg_loss - avg_gain)
        
        Caso B (precio < prev_close, gain=0):
            precio = prev_close - (period-1) * (avg_gain/RS_target - avg_loss)
        
        Args:
            target_rsi: RSI objetivo (0-100) que queremos alcanzar.
        
        Returns:
            Precio calculado. Puede ser > o < que prev_close dependiendo del caso.
            Retorna prev_close si no se puede calcular (datos insuficientes).
        """
        if self._prev_close is None or self._count < self._period:
            return self._prev_close or 0.0
        
        target_rs = self._rsi_to_rs(target_rsi)
        p = self._period
        prev = self._prev_close
        G = self._avg_gain
        L = self._avg_loss
        
        # ── Caso A: precio > prev_close, loss = 0 ─────────────────────────
        # avg_gain_new = (G * (p-1) + (precio - prev)) / p
        # avg_loss_new = L * (p-1) / p
        # RS_target = avg_gain_new / avg_loss_new
        # precio = prev + (p-1) * (RS_target * L - G)
        
        price_a = prev + (p - 1) * (target_rs * L - G)
        
        # ── Caso B: precio < prev_close, gain = 0 ─────────────────────────
        # avg_gain_new = G * (p-1) / p
        # avg_loss_new = (L * (p-1) + (prev - precio)) / p
        # RS_target = avg_gain_new / avg_loss_new
        # precio = prev - (p-1) * (G / RS_target - L)
        
        price_b = prev - (p - 1) * (G / target_rs - L) if target_rs > 0 else prev

        # ── Guard against negative prices ─────────────────────────────────
        # En mercados extremadamente volátiles, las fórmulas pueden producir
        # precios negativos, lo cual no tiene sentido en trading real.
        # Se clampa a un mínimo de 1e-10 * prev para evitar valores absurdos.
        _MIN_PRICE = max(prev * 1e-10, 1e-10)
        price_a = max(price_a, _MIN_PRICE)
        price_b = max(price_b, _MIN_PRICE)
        
        # ── Validación y selección de caso ────────────────────────────────
        
        # Casos borde
        if L == 0 and G == 0:
            # Sin ganancia ni pérdida histórica
            if target_rsi >= 50.0:
                return prev * 1.001  # subida mínima
            else:
                return prev * 0.999  # bajada mínima
        
        if L == 0 and target_rsi < 100.0:
            # Sin pérdidas históricas → necesitamos Caso B (generar pérdida)
            if price_b < prev:
                return price_b
            return prev * 0.999
        
        if G == 0 and target_rsi > 0.0:
            # Sin ganancias históricas → necesitamos Caso A (generar ganancia)
            if price_a > prev:
                return price_a
            return prev * 1.001
        
        # Caso normal: probamos ambos y elegimos el que cumple la condición
        if price_a > prev:
            # Verificar que efectivamente da el RSI target
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