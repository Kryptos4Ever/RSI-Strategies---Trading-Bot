"""
order_book.py — Actor: Libro de órdenes
══════════════════════════════════════════
Responsabilidad única: abrir y cerrar posiciones, validar guards y calcular comisiones.

SOPORTA LONG Y SHORT (dirección unificada):
  - OPEN:   abrir nueva posición en dirección LONG o SHORT
  - ADD:    agregar a posición existente en la misma dirección
  - REDUCE: reducir posición en 1 slot (TP parcial)
  - CLOSE:  cerrar posición completamente

REGLAS DE DIRECCIÓN:
  - LONG:  OPEN/ADD = BUY  (gasta USD, recibe BTC). REDUCE/CLOSE = SELL (vende BTC, recibe USD).
  - SHORT: OPEN/ADD = SELL (vende BTC prestado, recibe USD). REDUCE/CLOSE = BUY (gasta USD, reduce deuda).

MODOS DE EJECUCIÓN:
  1. Post-Only: maker forzado. Rechaza órdenes que ejecutarían como taker.
  2. GTC: permite taker (gap a favor) o maker (espera en libro).

GUARDS:
  - OPEN/ADD: max posiciones, slot USD suficiente, slot > mínimo
  - REDUCE/CLOSE: posiciones existentes, BTC > 0, valor USD > mínimo

MÍNIMOS OPERATIVOS:
  - _min_order_usd = 10.0  → Mínimo de USD para una orden.
  - _sell_margin_pct = 1.0  → Margen extra del 1% para ventas.

CORRECCIÓN SHORT (2026-07-29):
  OPEN/ADD SHORT ahora usa USD (slot) en lugar de BTC, igual que LONG.
  _execute_sell() acepta usd_amount y lo convierte a BTC usando el precio de ejecución.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

from support.types      import Candle, PositionDirection
from actors.wallet      import Wallet, TradeRecord
from support.logger     import get_logger
from support.time_utils import now_epoch_s

log = get_logger("order_book")


class OrderSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    PENDING       = "PENDING"
    PENDING_LIMIT = "PENDING_LIMIT"   # Orden límite colocada en el libro (sin fill aún)
    SUBMITTED     = "SUBMITTED"
    FILLED        = "FILLED"
    REJECTED      = "REJECTED"
    IGNORED       = "IGNORED"


@dataclass
class Order:
    """Ciclo de vida de una orden."""
    order_id:      str
    side:          OrderSide
    price:         float
    ts:            int
    usd_amount:    Optional[float]       = None
    btc_amount:    Optional[float]       = None
    status:        OrderStatus           = OrderStatus.PENDING
    reject_reason: Optional[str]         = None
    trade:         Optional[TradeRecord] = None
    exchange_oid:  Optional[int]         = None   # OID del exchange (para cancelación y tracking)
    cloid:         Optional[str]         = None   # Custom Order ID (para idempotencia en reintentos)
    direction:     PositionDirection     = PositionDirection.NONE   # LONG/SHORT (para reduce_only en HL)
    signal_type:   Optional[str]         = None   # OPEN_LONG, CLOSE_SHORT, etc.

    @property
    def is_filled(self)        -> bool: return self.status == OrderStatus.FILLED
    @property
    def is_rejected(self)      -> bool: return self.status == OrderStatus.REJECTED
    @property
    def is_ignored(self)       -> bool: return self.status == OrderStatus.IGNORED
    @property
    def is_pending_limit(self) -> bool: return self.status in (OrderStatus.PENDING_LIMIT, OrderStatus.PENDING)

    @property
    def qty(self) -> float:
        """Cantidad de BTC realmente operada.
        - BUY  → trade.btc_bought  (o btc_amount si no hay trade)
        - SELL → trade.btc_sold    (o btc_amount si no hay trade)
        """
        if self.trade:
            if self.side in (OrderSide.BUY, "BUY"):
                return self.trade.btc_bought or 0.0
            else:
                return self.trade.btc_sold or 0.0
        return self.btc_amount or 0.0


class OrderBook(ABC):
    """Contrato base para libros de órdenes (síncrono y asíncrono).

    Proporciona 4 métodos de alto nivel:
      - open_position()   → abre posición en dirección especificada
      - add_position()    → agrega a posición existente
      - reduce_position() → reduce 1 slot (TP parcial)
      - close_position()  → cierra posición completamente

    Cada método recibe `direction: PositionDirection` para saber
    si opera LONG o SHORT.
    """
    _max_posiciones: int = 5
    _min_order_usd: float = 10.0       # Mínimo de USD para colocar una orden
    _sell_margin_pct: float = 1.0       # Margen extra (1%) para ventas por volatilidad

    def round_price(self, price: float) -> float:
        """Retorna el precio ajustado a los límites/redondeo del exchange."""
        return price

    @abstractmethod
    def create_order(self, side: OrderSide, price: float,
                     usd_amount: Optional[float] = None,
                     btc_amount: Optional[float] = None) -> Order: ...

    @abstractmethod
    def submit(self, order: Order, initial_candle_open: Optional[float] = None) -> Order: ...

    @abstractmethod
    def check(self, order_id: str) -> Order: ...

    # ── Métodos de alto nivel unificados ─────────────────────────────────

    def _direction_to_side(self, action: str, direction: PositionDirection) -> OrderSide:
        """Convierte acción + dirección al lado de la orden.

        LONG:
          OPEN/ADD  → BUY  (comprar BTC)
          REDUCE/CLOSE → SELL (vender BTC)
        SHORT:
          OPEN/ADD  → SELL (vender BTC prestado)
          REDUCE/CLOSE → BUY  (comprar BTC para devolver)
        """
        if direction == PositionDirection.LONG:
            return OrderSide.SELL if action in ("reduce", "close") else OrderSide.BUY
        else:  # SHORT
            return OrderSide.BUY if action in ("reduce", "close") else OrderSide.SELL

    def open_position(self, direction: PositionDirection, price: float,
                      wallet: Wallet,
                      candle_ts: int = 0,
                      initial_candle_open: Optional[float] = None,
                      signal_type: Optional[str] = None) -> Order:
        """Abre una nueva posición en la dirección especificada."""
        side = self._direction_to_side("open", direction)
        return self._execute_with_guards(
            side, price, wallet, direction,
            candle_ts=candle_ts, initial_candle_open=initial_candle_open,
            guard_check=lambda w: self._check_open_guards(w),
            usd_amount=lambda w: w.get_slot_usd(),
            signal_type=signal_type,
        )

    def add_position(self, direction: PositionDirection, price: float,
                     wallet: Wallet,
                     candle_ts: int = 0,
                     initial_candle_open: Optional[float] = None,
                     signal_type: Optional[str] = None) -> Order:
        """Agrega a una posición existente en la misma dirección."""
        return self.open_position(direction, price, wallet, candle_ts, initial_candle_open, signal_type=signal_type)

    def reduce_position(self, direction: PositionDirection, price: float,
                        wallet: Wallet,
                        candle_ts: int = 0,
                        initial_candle_open: Optional[float] = None,
                        signal_type: Optional[str] = None) -> Order:
        """Reduce 1 slot de la posición (TP parcial)."""
        side = self._direction_to_side("reduce", direction)
        return self._execute_with_guards(
            side, price, wallet, direction,
            candle_ts=candle_ts, initial_candle_open=initial_candle_open,
            guard_check=lambda w: self._check_close_guards(w, current_price=price),
            btc_amount=lambda w: w.get_btc_por_venta(),
            signal_type=signal_type,
        )

    def close_position(self, direction: PositionDirection, price: float,
                       wallet: Wallet,
                       candle_ts: int = 0,
                       initial_candle_open: Optional[float] = None,
                       signal_type: Optional[str] = None) -> Order:
        """Cierra la posición completamente."""
        return self.reduce_position(direction, price, wallet, candle_ts, initial_candle_open, signal_type=signal_type)

    # ── Guards unificados ────────────────────────────────────────────────

    def _check_open_guards(self, wallet: Wallet) -> Optional[str]:
        """Verifica que se pueda abrir/aumentar una posición (LONG o SHORT)."""
        if wallet.positions_count >= self._max_posiciones:
            return f"max_posiciones({self._max_posiciones})"
        slot = wallet.get_slot_usd()
        if slot > wallet.get_usd_balance() + 1e-9:
            return f"usd_insuficiente(slot={slot:.2f}>balance={wallet.get_usd_balance():.2f})"
        if slot < self._min_order_usd:
            return f"slot_menor_a_minimo({self._min_order_usd} USD)"
        return None

    def _check_close_guards(self, wallet: Wallet,
                            current_price: Optional[float] = None) -> Optional[str]:
        """Verifica que se pueda reducir/cerrar una posición."""
        if wallet.positions_count == 0:
            return "sin_posiciones"
        btc_qty = wallet.get_btc_por_venta()
        if btc_qty <= 0:
            return "btc_por_venta_cero"
        if current_price is not None and current_price > 0:
            min_usd_with_margin = self._min_order_usd * (1.0 + self._sell_margin_pct / 100.0)
            valor_venta = btc_qty * current_price
            if valor_venta < min_usd_with_margin:
                return (
                    f"valor_venta_menor_a_minimo("
                    f"btc={btc_qty:.8f}*price=${current_price:,.2f}=${valor_venta:.2f} < "
                    f"${min_usd_with_margin:.2f})"
                )
        return None

    # ── Ejecución con guards (centralizada) ──────────────────────────────

    def _execute_with_guards(
        self,
        side: OrderSide,
        price: float,
        wallet: Wallet,
        direction: PositionDirection,
        candle_ts: int = 0,
        initial_candle_open: Optional[float] = None,
        guard_check=None,
        usd_amount=None,
        btc_amount=None,
        signal_type: Optional[str] = None,
    ) -> Order:
        """Ejecuta una orden con validación de guards.

        Args:
            side: BUY o SELL
            price: precio límite
            wallet: wallet
            direction: LONG o SHORT
            candle_ts: timestamp de la vela
            initial_candle_open: open de la vela 1H (para Post-Only)
            guard_check: callable(wallet) → Optional[str] (motivo rechazo o None)
            usd_amount: callable(wallet) → float (monto USD).
                        Se usa para OPEN/ADD tanto LONG como SHORT.
            btc_amount: callable(wallet) → float (monto BTC).
                        Se usa para REDUCE/CLOSE tanto LONG como SHORT.
            signal_type: str opcional (OPEN_LONG, ADD_SHORT, etc.)

        NOTA: La decisión de usar USD o BTC se basa en qué callable se pasó
        (usd_amount vs btc_amount), NO en el side. Esto permite que
        OPEN_SHORT (side=SELL) use USD correctamente.
        """
        # Validación de precio
        if price <= 0:
            ts = candle_ts if candle_ts else now_epoch_s()
            order = self.create_order(side=side, price=price)
            order.ts = ts
            order.status = OrderStatus.REJECTED
            order.reject_reason = "invalid_price"
            order.trade = TradeRecord(ts=ts, side=side.value, price=price,
                                      ignored=True, ignore_reason="invalid_price",
                                      direction=direction)
            wallet.update(order.trade)
            return order

        # Guards
        if guard_check:
            reason = guard_check(wallet)
            if reason:
                ts = candle_ts if candle_ts else now_epoch_s()
                amt = None
                if usd_amount:
                    amt = usd_amount(wallet)
                elif btc_amount:
                    amt = btc_amount(wallet)
                order = self.create_order(
                    side=side, price=price,
                    usd_amount=amt if usd_amount is not None else None,
                    btc_amount=amt if btc_amount is not None else None,
                )
                order.ts = ts
                order.status = OrderStatus.IGNORED
                order.reject_reason = reason
                order.trade = TradeRecord(ts=ts, side=side.value, price=price,
                                          ignored=True, ignore_reason=reason,
                                          direction=direction)
                wallet.update(order.trade)
                return order

        # ── Crear y ejecutar orden ──────────────────────────────────────
        # La decisión USD vs BTC se basa en qué callable se proporcionó:
        # - usd_amount: OPEN/ADD (LONG o SHORT) → usar USD
        # - btc_amount: REDUCE/CLOSE (LONG o SHORT) → usar BTC de la posición
        if usd_amount is not None:
            # OPEN/ADD: siempre usa USD (slot)
            amt = usd_amount(wallet)
            order = self.create_order(side, price, usd_amount=amt)
        elif btc_amount is not None:
            # REDUCE/CLOSE: siempre usa BTC de la posición
            amt = btc_amount(wallet)
            order = self.create_order(side, price, btc_amount=amt)
        else:
            # Fallback (no debería ocurrir)
            if side == OrderSide.BUY:
                amt = wallet.get_slot_usd()
                order = self.create_order(side, price, usd_amount=amt)
            else:
                amt = wallet.get_btc_por_venta()
                order = self.create_order(side, price, btc_amount=amt)

        if candle_ts:
            order.ts = candle_ts
        # Marcar dirección y signal_type en la orden ANTES de submit()
        # para que los order books reales (Hyperliquid) puedan decidir
        # reduce_only / is_buy correctamente según la operación.
        order.direction = direction
        if signal_type:
            order.signal_type = signal_type
        order = self.submit(order, initial_candle_open=initial_candle_open)
        order = self.check(order.order_id)

        # Marcar dirección y signal_type en el trade
        if order.trade:
            order.trade.direction = direction
            if signal_type:
                order.trade.signal_type = signal_type

        # Solo actualizar wallet si hay un trade real (FILLED).
        # PENDING_LIMIT significa que la orden está en el libro esperando fill —
        # el wallet se actualiza cuando llega el fill real por WebSocket (_on_order_fill).
        if order.trade and order.status == OrderStatus.FILLED:
            wallet.update(order.trade)
        return order

    # ── Compatibilidad: execute / execute_with_guards (legacy) ──────────

    def execute(self, side: OrderSide, price: float, wallet: Wallet,
                candle_ts: int = 0) -> Order:
        if side == OrderSide.BUY:
            order = self.create_order(side, price, usd_amount=wallet.get_slot_usd())
        else:
            order = self.create_order(side, price, btc_amount=wallet.get_btc_por_venta())
        if candle_ts:
            order.ts = candle_ts
        order = self.submit(order)
        order = self.check(order.order_id)
        if order.trade and order.status == OrderStatus.FILLED:
            wallet.update(order.trade)
        return order

    def check_buy_guards(self, wallet: Wallet) -> Optional[str]:
        return self._check_open_guards(wallet)

    def check_sell_guards(self, wallet: Wallet, current_price: Optional[float] = None) -> Optional[str]:
        return self._check_close_guards(wallet, current_price=current_price)

    def execute_with_guards(self, side: OrderSide, price: float, wallet: Wallet,
                            candle_ts: int = 0) -> Order:
        # Validación de precio: previene ZeroDivisionError si price <= 0
        if price <= 0:
            ts    = candle_ts if candle_ts else now_epoch_s()
            order = self.create_order(side=side, price=price)
            order.ts            = ts
            order.status        = OrderStatus.REJECTED
            order.reject_reason = "invalid_price"
            order.trade = TradeRecord(ts=ts, side=side.value, price=price,
                                      ignored=True, ignore_reason="invalid_price")
            wallet.update(order.trade)
            return order
        reason = (self.check_buy_guards(wallet) if side == OrderSide.BUY
                  else self.check_sell_guards(wallet))
        if reason:
            ts    = candle_ts if candle_ts else now_epoch_s()
            order = self.create_order(
                side=side, price=price,
                usd_amount=wallet.get_slot_usd()     if side == OrderSide.BUY  else None,
                btc_amount=wallet.get_btc_por_venta() if side == OrderSide.SELL else None,
            )
            order.ts            = ts
            order.status        = OrderStatus.IGNORED
            order.reject_reason = reason
            order.trade = TradeRecord(ts=ts, side=side.value, price=price,
                                      ignored=True, ignore_reason=reason)
            wallet.update(order.trade)
            return order
        return self.execute(side, price, wallet, candle_ts=candle_ts)


class AsyncOrderBook(OrderBook):
    """
    OrderBook asíncrono para exchanges reales.
    Contrato para los order books concretos de cada entorno.

    La sesión aiohttp es inyectada desde el engine (Opción B, ver plan).
    SimulatedOrderBook (papper) NO hereda de AsyncOrderBook porque no hace I/O real.
    """

    @abstractmethod
    async def submit_async(self, order: Order, session: "aiohttp.ClientSession") -> Order:
        """Versión async de submit(). session inyectada desde el engine."""

    @abstractmethod
    async def check_async(self, order_id: str, session: "aiohttp.ClientSession") -> Order:
        """Versión async de check(). session inyectada desde el engine."""


class SimulatedOrderBook(OrderBook):
    """Ejecución instantánea al precio dado (modo mercado / sin restricciones post-only)."""

    def __init__(self, commission_pct: float, max_posiciones: int) -> None:
        self._commission_pct = commission_pct
        self._max_posiciones = max_posiciones
        self._orders: dict[str, Order] = {}

    def create_order(self, side: OrderSide, price: float,
                     usd_amount: Optional[float] = None,
                     btc_amount: Optional[float] = None) -> Order:
        order = Order(order_id=str(uuid.uuid4())[:8], side=side, price=price,
                      ts=now_epoch_s(), usd_amount=usd_amount, btc_amount=btc_amount)
        self._orders[order.order_id] = order
        return order

    def submit(self, order: Order, initial_candle_open: Optional[float] = None) -> Order:
        if order.side == OrderSide.BUY:
            self._execute_buy(order)
        else:
            self._execute_sell(order)
        if order.trade:
            order.trade.ts = order.ts
        return order

    def check(self, order_id: str) -> Order:
        return self._orders.get(order_id, Order(
            order_id="unknown", side=OrderSide.BUY, price=0, ts=0,
            status=OrderStatus.REJECTED, reject_reason="order_id no encontrado"))

    def _execute_buy(self, order: Order, execution_price: Optional[float] = None) -> None:
        """
        Ejecuta una compra. Soporta dos modos:
        1. Compra con USD (OPEN/ADD LONG):
           order.usd_amount contiene el monto USD a gastar.
        2. Compra con BTC (REDUCE/CLOSE SHORT):
           order.btc_amount contiene la cantidad de BTC a comprar para cubrir deuda.
           Se convierte a USD usando execution_price.
        """
        exec_price = execution_price if execution_price is not None else order.price

        # Determinar USD a gastar: desde usd_amount o desde btc_amount
        if order.btc_amount is not None and order.btc_amount > 0:
            # REDUCE/CLOSE SHORT: btc_amount contiene la cantidad de BTC a comprar
            usd_a_gastar = order.btc_amount * exec_price
        elif order.usd_amount is not None and order.usd_amount > 0:
            # OPEN/ADD LONG: usd_amount contiene el monto USD a gastar
            usd_a_gastar = order.usd_amount
        else:
            self._reject(order, "usd_insuficiente(slot<1)")
            return

        if usd_a_gastar < 1.0:
            self._reject(order, "usd_insuficiente(slot<1)")
            return
        commission   = round(usd_a_gastar * self._commission_pct / 100.0, 8)
        btc_comprado = round((usd_a_gastar - commission) / exec_price, 10)
        order.status = OrderStatus.FILLED
        order.trade  = TradeRecord(ts=order.ts, side="BUY", price=exec_price,
                                   usd_spent=round(usd_a_gastar, 8),
                                   btc_bought=btc_comprado, commission=commission,
                                   slippage_pct=0.0, latency_ms=0.0)

    def _execute_sell(self, order: Order, execution_price: Optional[float] = None) -> None:
        """
        Ejecuta una venta. Soporta dos modos:
        1. Venta con BTC existente (REDUCE/CLOSE LONG, o SHORT con deuda):
           order.btc_amount contiene la cantidad de BTC a vender.
        2. Venta con USD (OPEN/ADD SHORT):
           order.usd_amount contiene el monto USD a usar.
           Se convierte a BTC usando execution_price.
        """
        exec_price = execution_price if execution_price is not None else order.price

        # Determinar BTC a vender: desde btc_amount o desde usd_amount
        if order.btc_amount is not None and order.btc_amount > 0:
            btc_a_vender = order.btc_amount
        elif order.usd_amount is not None and order.usd_amount > 0:
            # OPEN/ADD SHORT: convertir USD → BTC usando el precio de ejecución
            btc_a_vender = order.usd_amount / exec_price
        else:
            btc_a_vender = 0.0

        if btc_a_vender <= 0:
            self._reject(order, "sin_btc")
            return
        usd_bruto   = round(btc_a_vender * exec_price, 8)
        commission   = round(usd_bruto * self._commission_pct / 100.0, 8)
        usd_neto    = round(usd_bruto - commission, 8)
        order.status = OrderStatus.FILLED
        order.trade  = TradeRecord(ts=order.ts, side="SELL", price=exec_price,
                                   btc_sold=round(btc_a_vender, 10),
                                   usd_received=usd_neto, commission=commission,
                                   slippage_pct=0.0, latency_ms=0.0)

    def _reject(self, order: Order, reason: str) -> None:
        order.status        = OrderStatus.REJECTED
        order.reject_reason = reason
        order.trade = TradeRecord(ts=order.ts, side=order.side.value,
                                  price=order.price, ignored=True, ignore_reason=reason)


class SimulatedLimitOrderBook(SimulatedOrderBook):
    """
    Simula órdenes límite Post-Only contra el rango de una vela.

    Reglas:
      1. Post-Only check:
         - BUY permitido solo si limit_price < candle_open (maker, espera que baje)
         - SELL permitido solo si limit_price > candle_open (maker, espera que suba)
      2. Fill check (solo si pasó Post-Only):
         - BUY se llena si candle_low <= limit_price (el precio bajó hasta el límite)
         - SELL se llena si candle_high >= limit_price (el precio subió hasta el límite)

    Uso: llamar a set_candle(candle) ANTES de submit().
    """

    def __init__(self, commission_pct: float, max_posiciones: int) -> None:
        super().__init__(commission_pct, max_posiciones)
        self._candle_open: float = 0.0
        self._candle_low: float = 0.0
        self._candle_high: float = 0.0

    def set_candle(self, candle: Candle) -> None:
        """Establece la vela actual contra la cual validar órdenes."""
        self._candle_open = candle.open
        self._candle_low = candle.low
        self._candle_high = candle.high

    def submit(self, order: Order, initial_candle_open: Optional[float] = None) -> Order:
        ref_open = initial_candle_open if initial_candle_open is not None else self._candle_open
        # ── Post-Only check ─────────────────────────────────────────────
        if order.side == OrderSide.BUY and order.price >= ref_open:
            self._reject(order, "post_only_rejected(buy_limit>=open, sería taker)")
            return order
        if order.side == OrderSide.SELL and order.price <= ref_open:
            self._reject(order, "post_only_rejected(sell_limit<=open, sería taker)")
            return order

        # ── Fill check ──────────────────────────────────────────────────
        if order.side == OrderSide.BUY and self._candle_low <= order.price:
            self._execute_buy(order)
        elif order.side == OrderSide.SELL and self._candle_high >= order.price:
            self._execute_sell(order)
        else:
            # La orden estuvo en libro pero no fue alcanzada → se cancela al cierre
            self._reject(order, "limit_not_reached")
        return order