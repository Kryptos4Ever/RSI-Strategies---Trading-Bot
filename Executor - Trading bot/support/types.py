"""
support/types.py — Tipos compartidos del sistema
═══════════════════════════════════════════════════
Define los tipos canónicos que usan todos los módulos,
sin dependencias de actors/, engine/ ni strategies/.

Candle, Signal, SignalSide y SignalType son los tipos fundamentales
que cruzan todo el sistema. Al estar en support/, cualquier
módulo puede importarlos sin crear dependencias circulares.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ══════════════════════════════════════════════════════════════════════════════
# CANAL — Vela OHLCV
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class Candle:
    """Vela OHLCV canónica del sistema."""
    ts:                    int
    open:                  float
    high:                  float
    low:                   float
    close:                 float
    volume:                float
    taker_buy_base_vol:    Optional[float] = field(default=None)
    taker_buy_quote_vol:   Optional[float] = field(default=None)
    quote_volume:          Optional[float] = field(default=None)
    trades_count:          Optional[int]   = field(default=None)

    @property
    def body(self) -> float:
        return self.close - self.open

    @property
    def total_range(self) -> float:
        return self.high - self.low

    def iso(self) -> str:
        from support.time_utils import to_iso
        return to_iso(self.ts)

    def __repr__(self) -> str:
        return (
            f"Candle({self.iso()}  "
            f"O={self.open:.2f} H={self.high:.2f} "
            f"L={self.low:.2f} C={self.close:.2f}  "
            f"vol={self.volume:.4f})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# SEÑAL — Intención de trading
# ══════════════════════════════════════════════════════════════════════════════

class PositionDirection(str, Enum):
    """Dirección de una posición.
    
    LONG:  Posición larga (compramos BTC, esperamos que suba)
    SHORT: Posición corta (vendimos BTC prestado, esperamos que baje)
    NONE:  Sin posición abierta
    """
    LONG  = "LONG"
    SHORT = "SHORT"
    NONE  = "NONE"


class SignalSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalType(str, Enum):
    """Tipo de operación que la estrategia desea ejecutar.
    
    OPEN_LONG:   Abrir nueva posición long (comprar BTC)
    ADD_LONG:    Agregar a posición long existente (comprar más BTC)
    REDUCE_LONG: Reducir posición long (vender parte del BTC)
    CLOSE_LONG:  Cerrar posición long completamente (vender todo el BTC)
    
    OPEN_SHORT:   Abrir nueva posición short (vender BTC prestado)
    ADD_SHORT:    Agregar a posición short existente (vender más BTC prestado)
    REDUCE_SHORT: Reducir posición short (comprar parte del BTC prestado)
    CLOSE_SHORT:  Cerrar posición short completamente (comprar todo el BTC prestado)
    
    HOLD: Sin operación.
    """
    OPEN_LONG   = "OPEN_LONG"
    ADD_LONG    = "ADD_LONG"
    REDUCE_LONG = "REDUCE_LONG"
    CLOSE_LONG  = "CLOSE_LONG"
    
    OPEN_SHORT   = "OPEN_SHORT"
    ADD_SHORT    = "ADD_SHORT"
    REDUCE_SHORT = "REDUCE_SHORT"
    CLOSE_SHORT  = "CLOSE_SHORT"
    
    HOLD = "HOLD"


@dataclass
class Signal:
    """
    Señal emitida por la estrategia tras procesar una vela.
    
    signal_type: tipo de operación (OPEN_LONG, CLOSE_SHORT, etc.)
    side:        lado de la orden (BUY/SELL) — derivado de signal_type
    price:       precio de ejecución sugerido
    reason:      descripción legible
    ts:          timestamp epoch s de la vela
    """
    signal_type: SignalType
    price:       float
    reason:      str               = ""
    ts:          Optional[int]     = None

    @property
    def side(self) -> SignalSide:
        """Deriva SignalSide desde SignalType."""
        if self.signal_type in (SignalType.OPEN_LONG, SignalType.ADD_LONG,
                                SignalType.REDUCE_SHORT, SignalType.CLOSE_SHORT):
            return SignalSide.BUY
        if self.signal_type in (SignalType.OPEN_SHORT, SignalType.ADD_SHORT,
                                SignalType.REDUCE_LONG, SignalType.CLOSE_LONG):
            return SignalSide.SELL
        return SignalSide.HOLD

    @property
    def is_actionable(self) -> bool:
        return self.signal_type != SignalType.HOLD

    def to_order_side(self) -> Optional[str]:
        """
        Retorna "BUY" | "SELL" | None.
        El engine se encarga de convertir a OrderSide si es necesario.
        """
        s = self.side
        if s == SignalSide.BUY:
            return "BUY"
        if s == SignalSide.SELL:
            return "SELL"
        return None


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTES
# ══════════════════════════════════════════════════════════════════════════════

HOLD = Signal(signal_type=SignalType.HOLD, price=0.0, reason="sin_señal")
HOLD_LIST: tuple[Signal, ...] = (HOLD,)