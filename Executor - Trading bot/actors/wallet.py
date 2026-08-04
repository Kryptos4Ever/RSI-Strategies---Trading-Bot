"""
wallet.py — Actor: Billetera
═════════════════════════════
Responsabilidad única: custodiar y reportar el estado del capital.

MODELO AGREGADO UNIFICADO (LONG + SHORT):
  - Una sola posición agregada (AggregatePosition) con dirección LONG o SHORT.
  - Nunca hay posición long y short al mismo tiempo.
  - SLOT: pool único para LONG y SHORT. OPEN/ADD siempre USAN 1 slot.
    CLOSE/REDUCE siempre LIBERAN slots.

REGLAS DE DIRECCIÓN:
  - LONG:  OPEN/ADD = BUY  (gasta USD, recibe BTC). REDUCE/CLOSE = SELL (vende BTC, recibe USD).
  - SHORT: OPEN/ADD = SELL (vende BTC prestado, recibe USD). REDUCE/CLOSE = BUY (gasta USD, reduce deuda).

MODELO DE COLATERAL SHORT:
  - _usd: balance total USD
  - _usd_short_collateral: USD bloqueados como garantía de shorts
  - usd_free = _usd - _usd_short_collateral: saldo realmente disponible
  - Al abrir SHORT: _usd = _usd - slot + usd_recibido, _usd_short_collateral += slot
  - Al cerrar SHORT: _usd = _usd - usd_gastado + slot, _usd_short_collateral -= slot

SISTEMA DE SLOT PROGRESIVO (slot_factor):
  - slot_factor=1.0 → slots uniformes (todos iguales, comportamiento clásico).
  - slot_factor>1.0 → slots progresivos (geométricos): factor^0, factor^1, ...
  - _recalcular_slot(): se ejecuta después de CADA operación. Reparte el saldo USD
    libre entre los slots LIBRES usando los pesos geométricos.
  - _recalcular_btc_por_posicion(): se ejecuta después de CADA operación. Redistribuye
    el BTC total entre los slots activos.

MÍNIMOS OPERATIVOS (validados en OrderBook, no aquí):
  - El slot USD debe superar $10.0 (check_buy_guards).
  - El valor del BTC a vender debe superar $10.10 (check_sell_guards).
"""
from __future__ import annotations

import json
import os
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

from support.logger     import get_logger
from support.time_utils import to_iso
from support.types      import PositionDirection, SignalType
from state.results_store import ResultsStore


def _windows_safe_replace(src: Path, dst: Path, max_retries: int = 3, delay: float = 0.2) -> None:
    """
    Reemplaza `dst` con `src` de forma segura en Windows.

    En Windows, SimpleHTTPRequestHandler (el servidor del dashboard) puede
    mantener `dst` abierto para leer durante una petición GET, lo que hace
    que os.replace() falle con PermissionError [WinError 5]. Este helper
    reintenta hasta `max_retries` veces con `delay` segundos entre intentos,
    usando shutil.copy2 + unlink como fallback cuando os.replace() falla.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            os.replace(src, dst)
            return  # éxito
        except PermissionError as e:
            last_exc = e
            if attempt < max_retries - 1:
                time.sleep(delay)

    # Fallback: copy + delete (elude el bloqueo de Windows)
    try:
        shutil.copy2(src, dst)
        try:
            src.unlink()
        except Exception:
            pass
    except Exception as copy_exc:
        # Si el fallback también falla, relanzar el error original
        raise (last_exc or copy_exc) from copy_exc

log = get_logger("wallet")


@dataclass(slots=True)
class AggregatePosition:
    """Una única posición agregada estilo Binance Futures.

    En lugar de tener N posiciones individuales (FIFO), todo el BTC
    se consolida en una sola posición con precio de entrada promedio ponderado.
    Soporta dirección LONG o SHORT (nunca ambas simultáneamente).
    """
    total_btc: float = 0.0
    avg_entry_price: float = 0.0
    opened_at: int = 0
    direction: PositionDirection = PositionDirection.NONE


@dataclass(slots=True)
class TradeRecord:
    """Resultado de una operación ejecutada."""
    ts:             int
    side:           str       # "BUY" | "SELL"
    price:          float
    usd_spent:      Optional[float] = None
    btc_bought:     Optional[float] = None
    commission:     Optional[float] = None
    btc_sold:       Optional[float] = None
    usd_received:   Optional[float] = None
    profit_usd:     Optional[float] = None
    realized_pnl:   Optional[float] = None   # PnL realizado en esta operación
    ignored:        bool            = False
    ignore_reason:  Optional[str]   = None
    slippage_pct:   Optional[float] = None   # Diferencia % entre precio señal y precio ejecutado
    latency_ms:     Optional[float] = None   # Milisegundos entre señal y ejecucion
    reason:         Optional[str]   = None   # Razón de la señal
    filled_pct:     Optional[float] = None   # Porcentaje de fill (100% = completo)
    direction:      PositionDirection = PositionDirection.NONE
    signal_type:    Optional[str] = None


class Wallet(ABC):
    """Contrato para todas las implementaciones de billetera."""

    @abstractmethod
    def get_usd_balance(self) -> float: ...

    @abstractmethod
    def get_btc_balance(self) -> float: ...

    @abstractmethod
    def get_btc_acumulado(self) -> float: ...

    @abstractmethod
    def get_positions(self) -> List[AggregatePosition]: ...

    @abstractmethod
    def get_slot_usd(self) -> float: ...

    @abstractmethod
    def get_btc_por_venta(self) -> float: ...

    @abstractmethod
    def update(self, trade: TradeRecord) -> None: ...

    @abstractmethod
    def snapshot(self, current_price: float) -> dict: ...

    @property
    def positions_count(self) -> int:
        return len(self.get_positions())

    def btc_en_posiciones(self) -> float:
        positions = self.get_positions()
        return sum(p.total_btc for p in positions)

    def precio_promedio_posiciones(self) -> float:
        positions = self.get_positions()
        if not positions or positions[0].total_btc <= 0:
            return 0.0
        return positions[0].avg_entry_price

    @property
    def current_direction(self) -> PositionDirection:
        positions = self.get_positions()
        if not positions or positions[0].total_btc <= 0:
            return PositionDirection.NONE
        return positions[0].direction

    def get_usd_free(self) -> float:
        return self.get_usd_balance() - self.get_usd_short_collateral()

    def get_usd_short_collateral(self) -> float:
        return 0.0

    def mark_to_market(self, current_price: float) -> float:
        """Valuacion local usando precio explicito."""
        ps = self.get_positions()
        if not ps or ps[0].direction == PositionDirection.NONE:
            return self.get_usd_balance()
        p = ps[0]
        if p.direction == PositionDirection.LONG:
            return self.get_usd_balance() + p.total_btc * current_price
        # SHORT: usd_balance - btc_deuda * price + usd_short_collateral
        # El colateral congelado NO debe restarse del portfolio porque sigue siendo parte del capital.
        return self.get_usd_balance() - p.total_btc * current_price + self.get_usd_short_collateral()

    def account_value(self) -> float | None:
        """Valor nativo reportado por exchange, si existe."""
        return None

    def portfolio_value(self, current_price: float) -> float:
        native_value = self.account_value()
        if native_value is not None:
            return native_value
        return self.mark_to_market(current_price)


class AsyncWallet(Wallet):
    """
    Wallet que puede sincronizar balances desde un exchange.
    Contrato para BinanceWallet y HyperliquidWallet.

    La sesión aiohttp es inyectada desde el engine (Opción B, ver plan).
    JSONWallet (papper) NO hereda de AsyncWallet porque no tiene API que consultar.
    """

    @abstractmethod
    async def sync_with_api(self, session: "aiohttp.ClientSession") -> dict | None:
        """
        Sincroniza balances reales desde el exchange.
        Retorna dict con cambios detectados (new_position / position_closed) o None.
        session: inyectada desde el engine (único ClientSession compartido).
        """


class MemoryWallet(Wallet):
    """Billetera en memoria — modelo agregado unificado (LONG + SHORT).

    Soporta slot progresivo mediante slot_factor.
    """

    def __init__(self, usd_initial: float, max_posiciones: int,
                 slot_factor: float = 1.0) -> None:
        self._usd:                 float              = usd_initial
        self._btc_libre:           float              = 0.0
        self._btc_acumulado_total: float              = 0.0
        self._usd_short_collateral: float             = 0.0
        self._posicion:            AggregatePosition  = AggregatePosition()
        self._slots_used:          int                = 0
        self._max_pos:             int                = max_posiciones
        self._usd_initial:         float              = usd_initial

        # ── Slot progresivo ────────────────────────────────────────────────
        self._slot_factor = max(1.0, float(slot_factor))  # mínimo 1.0

        # Lista de slots disponibles para comprar (se recalcula tras cada operación)
        self._usable_slots: List[float] = []

        # Índice del próximo slot a usar (0 = más barato)
        self._next_buy_idx: int = 0

        # BTC asignado a cada posición activa (orden de compra, pop() para LIFO)
        self._btc_por_posicion: List[float] = []

        # Colateral USD congelado por cada slot short (LIFO)
        self._usd_short_collateral_per_slot: List[float] = []

        # Slot "virtual" para compatibilidad con OrderBook (lee de _usable_slots)
        self._slot_usd: float = 0.0
        self._btc_por_venta: float = 0.0

        # Calcular slots iniciales
        self._recalcular_slot()

    def get_usd_balance(self)   -> float:           return self._usd
    def get_usd_free(self)      -> float:           return self._usd - self._usd_short_collateral
    def get_usd_short_collateral(self) -> float:    return self._usd_short_collateral
    def get_btc_balance(self)   -> float:           return self.btc_en_posiciones()
    def get_btc_acumulado(self) -> float:           return self._btc_acumulado_total
    def get_positions(self)     -> List[AggregatePosition]:
        return [self._posicion] if self._slots_used > 0 else []

    @property
    def positions_count(self) -> int:
        return self._slots_used

    def get_slot_usd(self) -> float:
        """
        Retorna el slot para la PRÓXIMA operación.
        Lee el siguiente slot disponible sin consumirlo (el OrderBook
        lo usará para calcular la cantidad a operar).
        """
        if self._next_buy_idx >= len(self._usable_slots):
            return 0.0
        return self._usable_slots[self._next_buy_idx]

    def get_btc_por_venta(self) -> float:
        """
        Retorna los BTC a vender de la última posición (LIFO).
        Solo lectura, no consume el valor.

        Si no hay slots activos (_btc_por_posicion vacío) pero existe
        posición real (fill parcial sin slot asignado), retorna el BTC
        total de la posición como fallback.
        """
        if self._btc_por_posicion:
            return self._btc_por_posicion[-1]
        # Fallback: posición parcial sin slot (fill parcial)
        if self._posicion and self._posicion.total_btc > 1e-10:
            return self._posicion.total_btc
        return 0.0

    def update(self, trade: TradeRecord) -> None:
        if trade.ignored:
            return
        if trade.side == "BUY":
            self._update_buy(trade)
        elif trade.side == "SELL":
            self._update_sell(trade)

    def _update_buy(self, trade: TradeRecord) -> None:
        usd_gastado = trade.usd_spent or 0.0
        btc_bought = trade.btc_bought or 0.0
        self._usd -= usd_gastado

        if trade.direction == PositionDirection.SHORT:
            # SHORT REDUCE / CLOSE
            if self._posicion.total_btc > 0 and self._posicion.direction == PositionDirection.SHORT:
                realized_pnl = (self._posicion.avg_entry_price - trade.price) * btc_bought
                trade.realized_pnl = round(realized_pnl, 8)
            self._posicion.total_btc = max(0.0, self._posicion.total_btc - btc_bought)

            # Liberar colateral LIFO
            if self._usd_short_collateral_per_slot:
                slot_liberado = self._usd_short_collateral_per_slot.pop()
                self._usd_short_collateral = max(0.0, self._usd_short_collateral - slot_liberado)
                self._usd += slot_liberado  # Devolver colateral al balance

            # Consumir slot LIFO (BTC)
            self._consume_slot_lifo(btc_bought)
            self._recalcular_slot()
            if self._slots_used > 0:
                self._recalcular_btc_por_posicion()
            return

        # LONG OPEN / ADD
        if self._slots_used > 0 and self._posicion.total_btc > 0:
            old_total = self._posicion.total_btc
            old_avg   = self._posicion.avg_entry_price
            nuevo_total = old_total + btc_bought
            self._posicion.avg_entry_price = (
                (old_avg * old_total + trade.price * btc_bought) / nuevo_total
            )
            self._posicion.total_btc = nuevo_total
        else:
            self._posicion = AggregatePosition(
                total_btc=btc_bought,
                avg_entry_price=trade.price,
                opened_at=trade.ts,
                direction=PositionDirection.LONG,
            )
        self._slots_used += 1
        self._next_buy_idx += 1
        self._recalcular_btc_por_posicion()
        self._recalcular_slot()

    def _update_sell(self, trade: TradeRecord) -> None:
        usd_recibido = trade.usd_received or 0.0
        btc_vendido = trade.btc_sold or 0.0
        self._usd += usd_recibido

        if trade.direction == PositionDirection.SHORT:
            # SHORT OPEN / ADD
            slot_original = usd_recibido + (trade.commission or 0.0)
            self._usd -= slot_original  # Congelar colateral
            # _usd ya se incrementó con usd_recibido arriba
            # Neto: _usd = _usd + usd_recibido - slot = _usd - comision

            if self._slots_used > 0 and self._posicion.total_btc > 0 and self._posicion.direction == PositionDirection.SHORT:
                old_total = self._posicion.total_btc
                old_avg   = self._posicion.avg_entry_price
                nuevo_total = old_total + btc_vendido
                self._posicion.avg_entry_price = (
                    (old_avg * old_total + trade.price * btc_vendido) / nuevo_total
                )
                self._posicion.total_btc = nuevo_total
            else:
                self._posicion = AggregatePosition(
                    total_btc=btc_vendido,
                    avg_entry_price=trade.price,
                    opened_at=trade.ts,
                    direction=PositionDirection.SHORT,
                )

            self._slots_used += 1
            self._btc_acumulado_total += btc_vendido
            self._usd_short_collateral += slot_original
            self._usd_short_collateral_per_slot.append(slot_original)
            self._recalcular_btc_por_posicion()
            self._recalcular_slot()
            return

        # LONG REDUCE / CLOSE
        if self._posicion.total_btc > 0:
            realized_pnl = (trade.price - self._posicion.avg_entry_price) * btc_vendido
            trade.realized_pnl = round(realized_pnl, 8)
        self._posicion.total_btc = max(0.0, self._posicion.total_btc - btc_vendido)
        self._btc_acumulado_total += btc_vendido

        self._consume_slot_lifo(btc_vendido)
        self._recalcular_slot()
        if self._slots_used > 0:
            self._recalcular_btc_por_posicion()

    def _consume_slot_lifo(self, btc_operado: float) -> None:
        """Consume un slot LIFO tras una reducción/cierre de posición."""
        if not self._btc_por_posicion:
            return
        btc_en_slot = self._btc_por_posicion[-1]
        umbral = max(1e-10, btc_en_slot * 0.001)
        diff = btc_en_slot - btc_operado
        if diff > umbral:
            # Reducción parcial del último slot
            self._btc_por_posicion[-1] = max(0.0, btc_en_slot - btc_operado)
            if self._btc_por_posicion[-1] < umbral:
                self._btc_por_posicion.pop()
                self._slots_used = max(0, self._slots_used - 1)
        else:
            # Cierre completo del último slot
            self._btc_por_posicion.pop()
            if self._posicion.total_btc < 1e-10:
                self._posicion = AggregatePosition()
                self._slots_used = 0
            else:
                self._slots_used = max(0, self._slots_used - 1)

    def snapshot(self, current_price: float) -> dict:
        return {
            "usd_balance":                round(self._usd, 8),
            "usd_free":                   round(self._usd - self._usd_short_collateral, 8),
            "usd_short_collateral":       round(self._usd_short_collateral, 8),
            "btc_libre":                  round(self._btc_libre, 10),
            "btc_acumulado_total":        round(self._btc_acumulado_total, 10),
            "btc_en_posiciones":          round(self.btc_en_posiciones(), 10),
            "positions_count":            self.positions_count,
            "precio_promedio_posiciones": round(self.precio_promedio_posiciones(), 8),
            "current_direction":          self.current_direction.value,
            "slot_usd":                   round(self._slot_usd, 4),
            "btc_por_venta":              round(self._btc_por_venta, 10),
            "usable_slots":               [round(s, 2) for s in self._usable_slots],
            "btc_por_posicion":           [round(b, 10) for b in self._btc_por_posicion],
            "slot_factor":                self._slot_factor,
            "portfolio_value":            round(self.portfolio_value(current_price), 4),
            "pnl_pct":                    round((self.portfolio_value(current_price) - self._usd_initial) / self._usd_initial * 100, 4) if self._usd_initial > 0 else 0.0,
        }

    def reset(self) -> None:
        self._usd                 = self._usd_initial
        self._btc_libre           = 0.0
        self._btc_acumulado_total = 0.0
        self._usd_short_collateral = 0.0
        self._posicion            = AggregatePosition()
        self._slots_used          = 0
        self._btc_por_posicion    = []
        self._usd_short_collateral_per_slot = []
        self._recalcular_slot()

    # ══════════════════════════════════════════════════════════════════════
    # SISTEMA DE SLOT PROGRESIVO
    # ══════════════════════════════════════════════════════════════════════

    def _recalcular_slot(self) -> None:
        """
        Reparte el saldo USD LIBRE entre los slots LIBRES usando factor progresivo.
        Se ejecuta después de CADA operación (el saldo pudo cambiar por ganancias/pérdidas).

        Pesos geométricos: factor^0, factor^1, ..., factor^(slots_libres-1)

        Con factor=1.0: todos los slots son iguales (comportamiento clásico).
        Con factor=2.0: los slots crecen exponencialmente.
        """
        slots_libres = self._max_pos - self._slots_used
        saldo = self._usd - self._usd_short_collateral  # usd_free

        if slots_libres <= 0 or saldo <= 0:
            self._usable_slots = []
            self._next_buy_idx = 0
            self._slot_usd = 0.0
            self._btc_por_venta = 0.0
            return

        # Calcular pesos geométricos
        pesos = [self._slot_factor ** i for i in range(slots_libres)]
        suma = sum(pesos)

        # Asignar USD a cada slot libre
        self._usable_slots = [saldo * p / suma for p in pesos]
        self._next_buy_idx = 0

        # Compatibilidad: el primer slot disponible es el que ve el OrderBook
        self._slot_usd = self._usable_slots[0] if self._usable_slots else 0.0

        # BTC por venta (compatibilidad): si hay posiciones, el BTC de la última
        if self._btc_por_posicion:
            self._btc_por_venta = self._btc_por_posicion[-1]
        else:
            self._btc_por_venta = 0.0

    def _recalcular_btc_por_posicion(self) -> None:
        """
        Reparte el BTC total acumulado entre las posiciones activas
        usando el mismo factor progresivo que las operaciones.

        Se ejecuta después de CADA operación.

        """
        if self._slots_used <= 0 or self._posicion.total_btc <= 0:
            self._btc_por_posicion = []
            self._btc_por_venta = 0.0
            return

        # Pesos progresivos para las posiciones activas
        pesos = [self._slot_factor ** i for i in range(self._slots_used)]
        suma = sum(pesos)
        btc_total = self._posicion.total_btc

        # Cada slot de venta tendrá su propia cantidad de BTC
        # posición[0] = primera operación (la más chica si factor>1)
        # posición[-1] = última operación (la más grande si factor>1) → se opera primero
        self._btc_por_posicion = [btc_total * p / suma for p in pesos]

        # Compatibilidad: BTC de la última posición para get_btc_por_venta()
        self._btc_por_venta = self._btc_por_posicion[-1]


class JSONWallet(MemoryWallet):
    """Extiende MemoryWallet agregando persistencia en archivo JSON."""

    def __init__(self, usd_initial: float, max_posiciones: int, json_path: str,
                 slot_factor: float = 1.0,
                 environment: str | None = None,
                 symbol: str | None = None,
                 collateral_currency: str | None = None) -> None:
        super().__init__(usd_initial, max_posiciones, slot_factor=slot_factor)
        self._json_path  = Path(json_path)
        self.results_store = ResultsStore(
            self._json_path,
            environment=environment,
            symbol=symbol,
            collateral_currency=collateral_currency,
        )
        self._trade_log: list[dict] = []
        self._load_state_from_json()

    def _load_state_from_json(self) -> None:
        """Carga el archivo JSON si existe y reconstruye el estado reejecutando los trades."""
        if not self._json_path.exists():
            return

        try:
            data = self.results_store.load()

            # Autodescubrimiento del capital inicial
            if "initial_capital_usd" in data and float(data.get("initial_capital_usd") or 0) > 0:
                self._usd_initial = float(data["initial_capital_usd"])

            history = data.get("trade_history", [])
            for entry in history:
                if not isinstance(entry, dict):
                    continue
                try:
                    side = entry.get("type") or entry.get("side")
                    ts = int(entry.get("ts", 0))
                    price = float(entry.get("price", 0.0))

                    if side and price > 0:
                        direction = PositionDirection(entry.get("direction", "NONE")) if entry.get("direction") else PositionDirection.NONE
                        t = TradeRecord(
                            ts=ts,
                            side=side,
                            price=price,
                            usd_spent=float(entry["usd_spent"]) if entry.get("usd_spent") is not None else None,
                            btc_bought=float(entry["btc_bought"]) if entry.get("btc_bought") is not None else None,
                            commission=float(entry["commission_usd"]) if entry.get("commission_usd") is not None else (float(entry["commission"]) if entry.get("commission") is not None else None),
                            btc_sold=float(entry["btc_sold"]) if entry.get("btc_sold") is not None else None,
                            usd_received=float(entry["usd_received"]) if entry.get("usd_received") is not None else None,
                            profit_usd=float(entry["profit_usd"]) if entry.get("profit_usd") is not None else None,
                            realized_pnl=float(entry["realized_pnl"]) if entry.get("realized_pnl") is not None else None,
                            ignored=entry.get("ignorado", False),
                            ignore_reason=entry.get("motivo_ignorado"),
                            reason=entry.get("reason"),
                            filled_pct=float(entry["filled_pct"]) if entry.get("filled_pct") is not None else None,
                            direction=direction,
                            signal_type=entry.get("signal_type"),
                        )
                        super().update(t)
                    self._trade_log.append(entry)
                except Exception as item_err:
                    log.warning("Error procesando trade individual de JSON", entry=entry, error=str(item_err))
                    self._trade_log.append(entry)

            log.info("Estado de wallet restaurado desde archivo",
                     path=str(self._json_path), trades_reloaded=len(self._trade_log),
                     slots_used=self._slots_used, price_avg=self.precio_promedio_posiciones())
        except Exception as e:
            log.warning("No se pudo restaurar el estado de la wallet desde JSON", error=str(e))

    def update(self, trade: TradeRecord) -> None:
        super().update(trade)
        self._trade_log.append(self._trade_to_dict(trade))

    def flush(self, summary: dict, root_extra: dict | None = None) -> None:
        payload = {
            "initial_capital_usd": self._usd_initial,
            "summary": summary,
            "trade_history": self._trade_log,
        }
        if root_extra:
            payload.update(root_extra)
        try:
            self.results_store.update(**payload)
        except Exception as e:
            log.error("Error writing wallet JSON", error=str(e))
            raise e

    def get_trade_log(self) -> list[dict]:
        return list(self._trade_log)

    def _trade_to_dict(self, t: TradeRecord) -> dict:
        # Calcular balance acumulativo desde trades previos en _trade_log
        # (NO usar self._usd que puede estar corrompido en Hyperliquid por doble resta)
        prev_spent = sum(
            tr.get("usd_spent", 0) or 0
            for tr in self._trade_log
            if tr.get("type") == "BUY"
        )
        prev_received = sum(
            tr.get("usd_received", 0) or 0
            for tr in self._trade_log
            if tr.get("type") == "SELL"
        )
        current_usd = self._usd_initial - prev_spent + prev_received
        if t.side == "BUY" and t.usd_spent:
            current_usd -= t.usd_spent
        elif t.side == "SELL" and t.usd_received:
            current_usd += t.usd_received

        port_val = self.portfolio_value(t.price)
        pnl_pct = ((port_val / self._usd_initial) - 1) * 100 if self._usd_initial > 0 else 0.0

        return {
            "ts":                         t.ts,
            "datetime":                   to_iso(t.ts),
            "type":                       t.side,
            "price":                      round(t.price, 8),
            # usd_* como únicos campos de balance
            "usd_balance":                round(current_usd, 8),
            "usd_spent":                  round(t.usd_spent,    8) if t.usd_spent    else None,
            "usd_received":               round(t.usd_received, 8) if t.usd_received else None,
            "commission_usd":             round(t.commission,    8) if t.commission    else None,
            "profit_usd":                 round(t.profit_usd,   8) if t.profit_usd    else None,
            # Datos de estado
            "btc_balance":                0.0,
            "btc_en_posiciones":          round(self.btc_en_posiciones(), 10),
            "positions_count":            self.positions_count,
            "precio_promedio_posiciones": round(self.precio_promedio_posiciones(), 8),
            "current_direction":          self.current_direction.value,
            # Nuevos campos enriquecidos
            "portfolio_value":            round(port_val, 4),
            "pnl_pct":                    round(pnl_pct, 4),
            "reason":                     t.reason,
            "filled_pct":                 t.filled_pct,
            "realized_pnl":               round(t.realized_pnl,  8) if t.realized_pnl  else None,
            "btc_bought":                 round(t.btc_bought,   10) if t.btc_bought    else None,
            "btc_sold":                   round(t.btc_sold,     10) if t.btc_sold      else None,
            "btc_accumulated":            round(self._btc_acumulado_total, 10),
            "direction":                  t.direction.value,
            "signal_type":                t.signal_type,
            "ignorado":                   t.ignored,
            "motivo_ignorado":            t.ignore_reason,
        }