"""
actors/papper/papper_wallet.py — Wallet simulado para entorno Papper
═════════════════════════════════════════════════════════════════════
Re-exporta las clases de `actors/wallet.py`, que ya implementan el modelo
agregado unificado LONG + SHORT (current_direction, _usd_short_collateral,
TradeRecord con direction/signal_type).

JSONWallet: Billetera con persistencia en archivo JSON (paper trading).
"""
from __future__ import annotations

from actors.wallet import (
    Wallet,
    AsyncWallet,
    MemoryWallet,
    JSONWallet,
    AggregatePosition,
    TradeRecord,
)

__all__ = [
    "Wallet",
    "AsyncWallet",
    "MemoryWallet",
    "JSONWallet",
    "AggregatePosition",
    "TradeRecord",
]