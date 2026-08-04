"""
state_manager.py — Gestor de estado para backtest
════════════════════════════════════════════════════
Responsabilidad única: guardar y restaurar checkpoints del estado del sistema.

MemoryStateManager: checkpoints en memoria para backtest (rápido, sin I/O).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, List


@dataclass
class Checkpoint:
    """
    Instantánea del estado del sistema en un momento dado.
    """
    ts:              int
    close_price:     float
    usd_balance:    float
    btc_balance:     float
    btc_en_pos:      float
    positions_count: int
    portfolio_value: float
    metadata:        dict | None = None

    @classmethod
    def from_wallet(cls, wallet, close_price: float, ts: int,
                    metadata: dict | None = None) -> "Checkpoint":
        """Crea un checkpoint a partir del estado actual de la wallet."""
        return cls(
            ts=ts,
            close_price=close_price,
            usd_balance=wallet.get_usd_balance(),
            btc_balance=wallet.get_btc_balance(),
            btc_en_pos=wallet.btc_en_posiciones(),
            positions_count=wallet.positions_count,
            portfolio_value=wallet.portfolio_value(close_price),
            metadata=metadata,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Checkpoint":
        return cls(
            ts=d["ts"],
            close_price=d["close_price"],
            usd_balance=d["usd_balance"],
            btc_balance=d["btc_balance"],
            btc_en_pos=d["btc_en_pos"],
            positions_count=d["positions_count"],
            portfolio_value=d["portfolio_value"],
            metadata=d.get("metadata"),
        )


class StateManager:
    """Guarda y recupera checkpoints del estado del sistema."""

    def save(self, checkpoint: Checkpoint) -> None:
        """Persiste un checkpoint."""

    def load_latest(self) -> Optional[Checkpoint]:
        """Retorna el último checkpoint guardado, o None si no existe."""

    def history(self) -> List[Checkpoint]:
        """Retorna el historial completo de checkpoints."""
        return []

    def clear(self) -> None:
        """Limpia todos los checkpoints."""


class MemoryStateManager(StateManager):
    """Checkpoints en memoria — sin persistencia. Rápido para backtest."""

    def __init__(self, max_history: int = 0) -> None:
        self._history: list[Checkpoint] = []
        self._max_history = max_history  # 0 = ilimitado (necesario para Sharpe/MaxDD)

    def save(self, checkpoint: Checkpoint) -> None:
        self._history.append(checkpoint)
        if self._max_history > 0 and len(self._history) > self._max_history:
            # Conservar solo los últimos N checkpoints para acotar memoria
            self._history = self._history[-self._max_history:]

    def load_latest(self) -> Optional[Checkpoint]:
        return self._history[-1] if self._history else None

    def history(self) -> List[Checkpoint]:
        return list(self._history)

    @property
    def checkpoint_count(self) -> int:
        return len(self._history)

    def clear(self) -> None:
        self._history.clear()

