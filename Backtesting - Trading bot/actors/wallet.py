"""
wallet.py — Actor: Billetera
══════════════════════════════
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
"""
from __future__ import annotations

import json, os, shutil, time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from support.logger     import get_logger
from support.time_utils import to_iso
from support.types      import Candle, PositionDirection, SignalType


def _windows_safe_replace(src: Path, dst: Path, max_retries: int = 3, delay: float = 0.2) -> None:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            os.replace(src, dst); return
        except PermissionError as e:
            last_exc = e
            if attempt < max_retries - 1: time.sleep(delay)
    try:
        shutil.copy2(src, dst)
        try: src.unlink()
        except Exception: pass
    except Exception as copy_exc:
        raise (last_exc or copy_exc) from copy_exc

log = get_logger("wallet")


@dataclass(slots=True)
class AggregatePosition:
    total_btc: float = 0.0
    avg_entry_price: float = 0.0
    opened_at: int = 0
    direction: PositionDirection = PositionDirection.NONE


@dataclass(slots=True)
class TradeRecord:
    ts:             int
    side:           str
    price:          float
    usd_spent:      Optional[float] = None
    btc_bought:     Optional[float] = None
    commission:     Optional[float] = None
    btc_sold:       Optional[float] = None
    usd_received:   Optional[float] = None
    realized_pnl:   Optional[float] = None
    ignored:        bool = False
    ignore_reason:  Optional[str] = None
    slippage_pct:   Optional[float] = None
    latency_ms:     Optional[float] = None
    direction:      PositionDirection = PositionDirection.NONE
    signal_type:    Optional[str] = None


class Wallet(ABC):
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
        return sum(p.total_btc for p in self.get_positions())
    def precio_promedio_posiciones(self) -> float:
        ps = self.get_positions()
        if not ps or ps[0].total_btc <= 0: return 0.0
        return ps[0].avg_entry_price
    @property
    def current_direction(self) -> PositionDirection:
        ps = self.get_positions()
        if not ps or ps[0].total_btc <= 0: return PositionDirection.NONE
        return ps[0].direction
    def portfolio_value(self, current_price: float) -> float:
        ps = self.get_positions()
        if not ps or ps[0].direction == PositionDirection.NONE: return self.get_usd_balance()
        p = ps[0]
        if p.direction == PositionDirection.LONG:
            return self.get_usd_balance() + p.total_btc * current_price
        else:
            # SHORT: usd_balance - btc_deuda * price + usd_short_collateral
            # El colateral congelado NO debe restarse del portfolio porque sigue siendo parte del capital.
            # Sin esta corrección, al abrir un short el portfolio cae artificialmente en ~slot.
            return self.get_usd_balance() - p.total_btc * current_price + self.get_usd_short_collateral()


class MemoryWallet(Wallet):
    def __init__(self, usd_initial: float, max_posiciones: int, slot_factor: float = 1.0) -> None:
        self._usd: float = usd_initial
        self._btc_acumulado_total: float = 0.0
        self._usd_short_collateral: float = 0.0
        self._posicion: AggregatePosition = AggregatePosition()
        self._slots_used: int = 0
        self._max_pos: int = max_posiciones
        self._usd_initial: float = usd_initial
        self._slot_factor = max(1.0, float(slot_factor))
        self._usable_slots: List[float] = []
        self._next_buy_idx: int = 0
        self._btc_por_posicion: List[float] = []
        self._usd_short_collateral_per_slot: List[float] = []
        self._recalcular_slot()

    @property
    def _slot_usd(self) -> float:
        return self._usable_slots[0] if self._usable_slots else 0.0
    @property
    def _btc_por_venta_prop(self) -> float:
        return self._btc_por_posicion[-1] if self._btc_por_posicion else 0.0

    def get_usd_balance(self) -> float:
        return self._usd
    def get_usd_free(self) -> float:
        return self._usd - self._usd_short_collateral
    def get_usd_short_collateral(self) -> float:
        return self._usd_short_collateral
    def get_btc_balance(self) -> float:
        return self.btc_en_posiciones()
    def get_btc_acumulado(self) -> float:
        return self._btc_acumulado_total
    def get_positions(self) -> List[AggregatePosition]:
        return [self._posicion] if self._slots_used > 0 else []
    @property
    def positions_count(self) -> int:
        return self._slots_used
    def get_slot_usd(self) -> float:
        if self._next_buy_idx >= len(self._usable_slots): return 0.0
        return self._usable_slots[self._next_buy_idx]
    def get_btc_por_venta(self) -> float:
        if not self._btc_por_posicion: return 0.0
        return self._btc_por_posicion[-1]

    def update(self, trade: TradeRecord) -> None:
        if trade.ignored: return
        if trade.side == "BUY": self._update_buy(trade)
        elif trade.side == "SELL": self._update_sell(trade)

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
            if self._btc_por_posicion:
                btc_en_slot = self._btc_por_posicion[-1]
                umbral = max(1e-10, btc_en_slot * 0.001)
                diff = btc_en_slot - btc_bought
                if diff > umbral:
                    self._btc_por_posicion[-1] = max(0.0, btc_en_slot - btc_bought)
                    if self._btc_por_posicion[-1] < umbral:
                        self._btc_por_posicion.pop()
                        self._slots_used = max(0, self._slots_used - 1)
                else:
                    self._btc_por_posicion.pop()
                    if self._posicion.total_btc < 1e-10:
                        self._posicion = AggregatePosition()
                        self._slots_used = 0
                    else:
                        self._slots_used = max(0, self._slots_used - 1)
            self._recalcular_slot()
            if self._slots_used > 0: self._recalcular_btc_por_posicion()
            return

        # LONG OPEN / ADD
        if self._slots_used > 0 and self._posicion.total_btc > 0:
            old_total = self._posicion.total_btc
            old_avg = self._posicion.avg_entry_price
            nuevo_total = old_total + btc_bought
            self._posicion.avg_entry_price = ((old_avg * old_total + trade.price * btc_bought) / nuevo_total)
            self._posicion.total_btc = nuevo_total
        else:
            self._posicion = AggregatePosition(total_btc=btc_bought, avg_entry_price=trade.price, opened_at=trade.ts, direction=PositionDirection.LONG)
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
                old_avg = self._posicion.avg_entry_price
                nuevo_total = old_total + btc_vendido
                self._posicion.avg_entry_price = ((old_avg * old_total + trade.price * btc_vendido) / nuevo_total)
                self._posicion.total_btc = nuevo_total
            else:
                self._posicion = AggregatePosition(total_btc=btc_vendido, avg_entry_price=trade.price, opened_at=trade.ts, direction=PositionDirection.SHORT)

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

        if self._btc_por_posicion:
            btc_en_slot = self._btc_por_posicion[-1]
            umbral = max(1e-10, btc_en_slot * 0.001)
            diff = btc_en_slot - btc_vendido
            if diff > umbral:
                self._btc_por_posicion[-1] = max(0.0, btc_en_slot - btc_vendido)
                if self._btc_por_posicion[-1] < umbral:
                    self._btc_por_posicion.pop()
                    self._slots_used = max(0, self._slots_used - 1)
            else:
                self._btc_por_posicion.pop()
                if self._posicion.total_btc < 1e-10:
                    self._posicion = AggregatePosition()
                    self._slots_used = 0
                else:
                    self._slots_used = max(0, self._slots_used - 1)
        self._recalcular_slot()
        if self._slots_used > 0: self._recalcular_btc_por_posicion()

    def snapshot(self, current_price: float) -> dict:
        return {
            "usd_balance":                round(self._usd, 8),
            "usd_free":                   round(self._usd - self._usd_short_collateral, 8),
            "usd_short_collateral":       round(self._usd_short_collateral, 8),
            "btc_acumulado_total":        round(self._btc_acumulado_total, 10),
            "btc_en_posiciones":          round(self.btc_en_posiciones(), 10),
            "positions_count":            self.positions_count,
            "precio_promedio_posiciones": round(self.precio_promedio_posiciones(), 8),
            "current_direction":          self.current_direction.value,
            "slot_usd":                   round(self._slot_usd, 4),
            "btc_por_venta":              round(self._btc_por_venta_prop, 10),
            "usable_slots":               [round(s, 2) for s in self._usable_slots],
            "btc_por_posicion":           [round(b, 10) for b in self._btc_por_posicion],
            "slot_factor":                self._slot_factor,
            "portfolio_value":            round(self.portfolio_value(current_price), 4),
            "pnl_pct":                    round((self.portfolio_value(current_price) - self._usd_initial) / self._usd_initial * 100, 4) if self._usd_initial > 0 else 0.0,
        }

    def reset(self) -> None:
        self._usd = self._usd_initial
        self._btc_acumulado_total = 0.0
        self._usd_short_collateral = 0.0
        self._posicion = AggregatePosition()
        self._slots_used = 0
        self._btc_por_posicion = []
        self._usd_short_collateral_per_slot = []
        self._recalcular_slot()

    def _recalcular_slot(self) -> None:
        slots_libres = self._max_pos - self._slots_used
        saldo = self._usd - self._usd_short_collateral  # usd_free
        if slots_libres <= 0 or saldo <= 0:
            self._usable_slots = []
            self._next_buy_idx = 0
            return
        pesos = [self._slot_factor ** i for i in range(slots_libres)]
        self._usable_slots = [saldo * p / sum(pesos) for p in pesos]
        self._next_buy_idx = 0

    def _recalcular_btc_por_posicion(self) -> None:
        if self._slots_used <= 0 or self._posicion.total_btc <= 0:
            self._btc_por_posicion = []
            return
        pesos = [self._slot_factor ** i for i in range(self._slots_used)]
        self._btc_por_posicion = [self._posicion.total_btc * p / sum(pesos) for p in pesos]


class JSONWallet(MemoryWallet):
    def __init__(self, usd_initial: float, max_posiciones: int, json_path: str, slot_factor: float = 1.0) -> None:
        super().__init__(usd_initial, max_posiciones, slot_factor=slot_factor)
        self._json_path = Path(json_path)
        self._trade_log: list[dict] = []

    def update(self, trade: TradeRecord) -> None:
        super().update(trade)
        self._trade_log.append(self._trade_to_dict(trade))

    def flush(self, summary: dict, root_extra: dict | None = None) -> None:
        payload = {"usd_initial": self._usd_initial, "summary": summary, "trade_history": self._trade_log}
        if root_extra: payload.update(root_extra)
        self._json_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._json_path.with_suffix(".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
            _windows_safe_replace(temp_path, self._json_path)
        except Exception as e:
            log.error("Error writing wallet JSON", error=str(e))
            if temp_path.exists():
                try: temp_path.unlink()
                except Exception: pass
            raise e

    def get_trade_log(self) -> list[dict]:
        return list(self._trade_log)

    def _trade_to_dict(self, t: TradeRecord) -> dict:
        snap = self.snapshot(t.price)
        return {
            "ts": t.ts, "datetime": to_iso(t.ts), "type": t.side,
            "price": round(t.price, 8),
            "usd_balance": snap["usd_balance"], "usd_free": snap["usd_free"],
            "usd_short_collateral": snap["usd_short_collateral"],
            "btc_en_posiciones": snap["btc_en_posiciones"],
            "positions_count": snap["positions_count"],
            "precio_promedio_posiciones": snap["precio_promedio_posiciones"],
            "current_direction": snap["current_direction"],
            "ignorado": t.ignored, "motivo_ignorado": t.ignore_reason,
            "usd_spent": round(t.usd_spent, 8) if t.usd_spent else None,
            "btc_bought": round(t.btc_bought, 10) if t.btc_bought else None,
            "commission_usd": round(t.commission, 8) if t.commission else None,
            "btc_sold": round(t.btc_sold, 10) if t.btc_sold else None,
            "usd_received": round(t.usd_received, 8) if t.usd_received else None,
            "realized_pnl": round(t.realized_pnl, 8) if t.realized_pnl else None,
            "direction": t.direction.value, "signal_type": t.signal_type,
        }