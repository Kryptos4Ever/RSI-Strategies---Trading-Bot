"""
actors/papper/papper_order_book.py — OrderBook simulado para entorno Papper
═════════════════════════════════════════════════════════════════════════════
Re-exporta las clases de `actors/order_book.py`, que ya implementan la lógica
completa LONG + SHORT (open_position, add_position, reduce_position, close_position).

SimulatedOrderBook: Ejecución instantánea al precio dado (modo mercado).
SimulatedLimitOrderBook: Simula órdenes límite Post-Only contra el rango de una vela.
"""
from __future__ import annotations

from actors.order_book import (
    OrderBook,
    Order,
    OrderSide,
    OrderStatus,
    SimulatedOrderBook,
    SimulatedLimitOrderBook,
)

__all__ = [
    "OrderBook",
    "Order",
    "OrderSide",
    "OrderStatus",
    "SimulatedOrderBook",
    "SimulatedLimitOrderBook",
]