"""
actors/hyperliquid_testnet/hyperliquid_testnet_order_book.py — Libro de órdenes para Hyperliquid Testnet
══════════════════════════════════════════════════════════════════════════════════════════════════════════
Implementación completa para el entorno Hyperliquid Testnet.
Usa hyperliquid-testnet.xyz y claves HL_TESTNET_* desde .env.

MODELO DE ÓRDENES LÍMITE (D1, D2, D3):
  - Todas las órdenes son límite GTC o ALO (nunca market).
  - submit() puede retornar status PENDING_LIMIT (en libro) o FILLED (fill inmediato).
  - cancel_all_async(): cancela todas las órdenes abiertas del símbolo.
  - submit_bulk_async(): envía BUY + SELL simultáneo como batch (D2 MAX_POSICIONES > 1).
  - set_dead_mans_switch(): programa cancelación automática como seguro ante caídas del proceso.

Configuración en .env:
  HL_TESTNET_ACCOUNT_ADDRESS=0x...   # wallet address (público)
  HL_TESTNET_SECRET_KEY=0x...        # clave privada (API wallet)
  HL_SYMBOL=BTC              # coin en Hyperliquid
  HL_LEVERAGE=1              # apalancamiento
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Optional

from actors.order_book import AsyncOrderBook, Order, OrderSide, OrderStatus
from actors.wallet import TradeRecord
from support.logger import get_logger
from support.time_utils import now_epoch_s
from support.types import PositionDirection

log = get_logger("hyperliquid_testnet_order_book")

TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"
MAX_RETRIES = 3
RETRY_DELAYS = [1.0, 3.0, 10.0]
BUY_MARGIN_FACTOR = 0.99

# Errores de HL que NO deben reintentarse
NON_RETRYABLE_KEYWORDS = [
    "insufficient_margin", "Insufficient margin",       # Margen insuficiente
    "price_band", "tick size", "Price must be divisible",  # Precio inválido
    "Order must have minimum value", "min_value",        # Nocional mínimo
    "reduce_only", "Reduce only",                        # Posición incorrecta
    "already_cancelled",                                 # Ya cancelada
]


class HyperliquidOrderBook(AsyncOrderBook):
    """
    Libro de órdenes real en Hyperliquid Testnet.
    Envía ÚNICAMENTE órdenes límite (GTC o ALO según order_type_mode).
    BUY → LONG position (reduce_only=False)
    SELL → CLOSE LONG position (reduce_only=True)
    """

    def __init__(
        self,
        max_posiciones: int,
        commission_pct: float = 0.05,
        symbol: str = "BTC",
        leverage: int = 1,
        order_type_mode: str = "limit_gtc",
    ) -> None:
        self._max_posiciones  = max_posiciones
        self._commission_pct  = commission_pct
        self._symbol          = symbol.upper().replace("USDT", "").replace("USDC", "")
        self._leverage        = leverage
        self._order_type_mode = order_type_mode   # "limit_gtc" | "limit_post_only"
        self._orders: dict[str, Order] = {}
        self._exchange        = None
        self._info            = None
        self._account_address: Optional[str] = None
        self._leverage_applied = False
        self._sz_decimals: int | None = None
        self._last_dead_mans_switch_ms: int | None = None
        self._dead_mans_switch_initialized = False
        log.info(
            "HyperliquidOrderBook Testnet inicializado",
            symbol=self._symbol,
            leverage=leverage,
            order_type_mode=order_type_mode,
        )

    # ── Conexiones lazy ────────────────────────────────────────────────

    def _get_exchange(self):
        if self._exchange is None:
            from hyperliquid.exchange import Exchange
            from hyperliquid.utils import constants
            from eth_account import Account
            from support.secrets import secrets
            secret_key = secrets("HL_TESTNET_SECRET_KEY")
            self._account_address = self._get_account_address()
            wallet = Account.from_key(secret_key)
            self._exchange = Exchange(
                wallet,
                constants.TESTNET_API_URL,
                account_address=self._account_address,
            )
            log.info("HyperliquidExchange Testnet conectado", address=self._account_address)
        return self._exchange

    def _get_account_address(self) -> str:
        """Account principal para lecturas `Info`; no inicializa `Exchange`."""
        if not self._account_address:
            from support.secrets import secrets
            self._account_address = secrets("HL_TESTNET_ACCOUNT_ADDRESS")
        return self._account_address

    def _get_info(self):
        if self._info is None:
            from hyperliquid.info import Info
            self._info = Info(TESTNET_API_URL, skip_ws=True)
        return self._info

    # ── Precisión de precios y tamaños ──────────────────────────────────

    def _get_sz_decimals(self) -> int:
        if self._sz_decimals is not None:
            return self._sz_decimals
        try:
            meta = self._get_info().meta()
            for asset in meta.get("universe", []):
                if asset.get("name") == self._symbol:
                    self._sz_decimals = int(asset["szDecimals"])
                    log.info("szDecimals obtenido Testnet", symbol=self._symbol, sz_decimals=self._sz_decimals)
                    return self._sz_decimals
            log.warning("Asset no encontrado en meta Testnet — fallback 5", symbol=self._symbol)
        except Exception as e:
            log.warning("Error obteniendo szDecimals Testnet — fallback 5", error=str(e))
        self._sz_decimals = 5
        return self._sz_decimals

    def _round_price(self, price: float) -> float:
        """
        Redondea precio al tick size de Hyperliquid.
        Segun la documentacion del SDK de HL, los precios deben tener
        5 cifras significativas: round(float(f"{price:.5g}"), 6)
        Esto garantiza que el precio sea divisible por el tick size del exchange.
        Referencia: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-sizes
        """
        return round(float(f"{price:.5g}"), 6)

    # ── Tipo de orden ──────────────────────────────────────────────────

    def _resolve_order_type(self) -> dict:
        """Convierte order_type_mode al dict esperado por el SDK de HL."""
        if self._order_type_mode == "limit_post_only":
            return {"limit": {"tif": "Alo"}}   # Add Liquidity Only = Post-Only
        return {"limit": {"tif": "Gtc"}}        # Good Till Cancelled (default)

    # ── CLOID para idempotencia ─────────────────────────────────────────

    def _generate_cloid(self, side: str, candle_ts: int):
        """
        Genera un CLOID único para cada intento de orden.
        Incluye un nonce aleatorio para evitar que reintentos dentro de la
        misma vela (mismo candle_ts) produzcan CLOIDs duplicados, lo que
        causaría que HL rechace la orden como duplicada.

        El candle_ts garantiza que órdenes de diferentes velas tengan
        CLOIDs distintos.
        """
        try:
            import random
            from hyperliquid.utils.types import Cloid
            nonce = random.randint(0, 999999)
            hex_str = uuid.uuid5(
                uuid.NAMESPACE_DNS,
                f"{side}_{candle_ts}_{self._symbol}_{nonce}",
            ).hex[:32]
            cloid = Cloid.from_str(f"0x{hex_str}")
            return cloid
        except Exception:
            return None

    # ── Leverage ───────────────────────────────────────────────────────

    def _ensure_leverage(self, exchange) -> None:
        if not self._leverage_applied:
            try:
                exchange.update_leverage(self._leverage, self._symbol, is_cross=True)
                self._leverage_applied = True
                log.info("Leverage aplicado Testnet", symbol=self._symbol, leverage=self._leverage)
            except Exception as e:
                log.warning("Error aplicando leverage Testnet", error=str(e))

    # ── Ejecución de órdenes límite ────────────────────────────────────

    def _is_reduce_only(self, order: Order) -> bool:
        """
        Determina si la orden es de reducción/cierre (reduce_only=True) o
        de apertura/aumento (reduce_only=False), según la dirección y la acción.

        LONG:
          OPEN/ADD  → reduce_only=False (abrir/aumentar long)
          REDUCE/CLOSE → reduce_only=True  (reducir/cerrar long)
        SHORT:
          OPEN/ADD  → reduce_only=False (abrir/aumentar short)
          REDUCE/CLOSE → reduce_only=True  (reducir/cerrar short)
        """
        st = order.signal_type or ""
        if st in ("REDUCE_LONG", "CLOSE_LONG", "REDUCE_SHORT", "CLOSE_SHORT"):
            return True
        # Fallback por dirección + lado
        if order.direction == PositionDirection.LONG:
            return order.side == OrderSide.SELL
        if order.direction == PositionDirection.SHORT:
            return order.side == OrderSide.BUY
        # Sin dirección: comportamiento legacy (BUY abre, SELL cierra)
        return order.side == OrderSide.SELL

    def _execute_buy_limit(self, exchange, order: Order) -> bool:
        """Coloca orden límite de compra (OPEN/ADD LONG, o REDUCE/CLOSE SHORT)."""
        reduce_only = self._is_reduce_only(order)
        if reduce_only:
            # REDUCE/CLOSE SHORT: comprar BTC para cubrir deuda (btc_amount)
            btc_amount = order.btc_amount or 0.0
            if btc_amount <= 0:
                self._reject(order, "btc_cero")
                return False
            sz_decimals = self._get_sz_decimals()
            qty = round(btc_amount, sz_decimals)
            if qty <= 0:
                self._reject(order, "qty_cero_post_redondeo")
                return False
        else:
            # OPEN/ADD LONG: comprar con USD (slot)
            usd_amount = order.usd_amount or 0.0
            if usd_amount < 1.0:
                self._reject(order, "usd_insuficiente")
                return False
            sz_decimals = self._get_sz_decimals()
            px = self._round_price(order.price)
            order.price = px
            # Aplicar factor de margen del 99% para garantizar buffer de fees.
            qty = round((usd_amount * BUY_MARGIN_FACTOR) / px, sz_decimals)
            if qty <= 0:
                self._reject(order, "qty_cero")
                return False
            if qty * px < 10.0:
                self._reject(order, f"nocional_insuficiente: ${qty * px:.2f} < $10.00")
                log.warning("Orden rechazada: nocional menor al mínimo Testnet", symbol=self._symbol, notional=round(qty * px, 2))
                return False

        self._ensure_leverage(exchange)
        px = self._round_price(order.price)
        order.price = px
        cloid = self._generate_cloid("BUY", order.ts)

        response = exchange.order(
            name=self._symbol,
            is_buy=True,
            sz=qty,
            limit_px=px,
            order_type=self._resolve_order_type(),
            reduce_only=reduce_only,
            cloid=cloid,
        )
        if cloid is not None:
            order.cloid = str(cloid)
        return self._parse_limit_response(order, response, is_buy=True, qty=qty)

    def _execute_sell_limit(self, exchange, order: Order) -> bool:
        """Coloca orden límite de venta (REDUCE/CLOSE LONG, o OPEN/ADD SHORT)."""
        reduce_only = self._is_reduce_only(order)
        if reduce_only:
            # REDUCE/CLOSE LONG: vender BTC existente (btc_amount)
            btc_amount = order.btc_amount or 0.0
            if btc_amount <= 0:
                self._reject(order, "btc_cero")
                return False
            sz_decimals = self._get_sz_decimals()
            qty = round(btc_amount, sz_decimals)
            if qty <= 0:
                self._reject(order, "qty_cero_post_redondeo")
                return False
        else:
            # OPEN/ADD SHORT: vender BTC prestado usando USD (slot)
            usd_amount = order.usd_amount or 0.0
            if usd_amount < 1.0:
                self._reject(order, "usd_insuficiente")
                return False
            sz_decimals = self._get_sz_decimals()
            px = self._round_price(order.price)
            order.price = px
            qty = round((usd_amount * BUY_MARGIN_FACTOR) / px, sz_decimals)
            if qty <= 0:
                self._reject(order, "qty_cero")
                return False
            if qty * px < 10.0:
                self._reject(order, f"nocional_insuficiente: ${qty * px:.2f} < $10.00")
                log.warning("Orden rechazada: nocional menor al mínimo Testnet", symbol=self._symbol, notional=round(qty * px, 2))
                return False

        self._ensure_leverage(exchange)
        px = self._round_price(order.price)
        order.price = px
        cloid = self._generate_cloid("SELL", order.ts)

        response = exchange.order(
            name=self._symbol,
            is_buy=False,
            sz=qty,
            limit_px=px,
            order_type=self._resolve_order_type(),
            reduce_only=reduce_only,
            cloid=cloid,
        )
        if cloid is not None:
            order.cloid = str(cloid)
        return self._parse_limit_response(order, response, is_buy=False, qty=qty)

    def _parse_limit_response(self, order: Order, response: dict, is_buy: bool, qty: float) -> bool:
        """
        Interpreta la respuesta de exchange.order() para órdenes límite.
          'resting' → orden en el libro (PENDING_LIMIT, sin fill aún).
          'filled'  → fill inmediato (el precio cruzó el límite al colocar).
        """
        data = response.get("response", {}).get("data", response)
        statuses = data.get("statuses", [])

        for s in statuses:
            if "error" in s:
                log.warning("Orden HL Testnet rechazada por exchange",
                            error=s["error"], side=order.side.value)
                self._reject(order, f"hl_error: {s['error'][:200]}")
                return False

            elif "resting" in s:
                # Orden colocada en el libro — sin fill todavía
                oid = s["resting"]["oid"]
                order.status = OrderStatus.PENDING_LIMIT
                order.exchange_oid = oid
                log.info(
                    "Orden límite en libro Testnet",
                    side="BUY" if is_buy else "SELL",
                    symbol=self._symbol,
                    price=order.price,
                    qty=qty,
                    oid=oid,
                    order_type=self._order_type_mode,
                )
                return True

            elif "filled" in s:
                # Fill inmediato (precio cruzó el límite)
                fill = s["filled"]
                executed_price = float(fill.get("avgPx", order.price))
                qty_filled     = float(fill.get("totalSz", qty))
                order.exchange_oid = fill.get("oid")
                order.status   = OrderStatus.FILLED
                commission     = round(qty_filled * executed_price * self._commission_pct / 100.0, 8)
                slippage_pct   = abs(executed_price - order.price) / order.price * 100 if order.price > 0 else 0.0
                latency_ms     = (now_epoch_s() - order.ts) * 1000 if order.ts > 0 else 0.0
                order.trade    = TradeRecord(
                    ts=now_epoch_s(),
                    side="BUY" if is_buy else "SELL",
                    price=executed_price,
                    usd_spent=round(qty_filled * executed_price, 8) if is_buy else None,
                    btc_bought=round(qty_filled, 10) if is_buy else None,
                    btc_sold=round(qty_filled, 10) if not is_buy else None,
                    usd_received=round(qty_filled * executed_price - commission, 8) if not is_buy else None,
                    commission=commission,
                    slippage_pct=round(slippage_pct, 4),
                    latency_ms=round(latency_ms, 2),
                )
                log.info(
                    "Orden límite con fill inmediato Testnet",
                    side="BUY" if is_buy else "SELL",
                    symbol=self._symbol,
                    executed_price=executed_price,
                    qty=qty_filled,
                    slippage_pct=round(slippage_pct, 4),
                )
                return True

        # Sin statuses reconocidos
        log.warning("Respuesta Hyperliquid Testnet sin statuses reconocidos", data=str(data)[:200])
        self._reject(order, f"respuesta_inesperada: {str(data)[:200]}")
        return False

    # ── Cancel all abiertos ────────────────────────────────────────────

    async def cancel_all_async(self, session=None) -> int:
        """
        Cancela TODAS las órdenes límite abiertas del símbolo en el exchange.
        Llamar al inicio de cada nueva vela (D1).

        Verifica post-cancelación que las órdenes efectivamente se hayan cerrado,
        reintentando una vez si es necesario.

        Returns:
            int: Número de órdenes canceladas (0 si no había ninguna).
        """
        try:
            exchange = self._get_exchange()
            info     = self._get_info()
            account_address = self._get_account_address()
            open_orders = info.open_orders(account_address)
            oids_to_cancel = [
                {"coin": self._symbol, "oid": o["oid"]}
                for o in open_orders
                if o.get("coin") == self._symbol
            ]
            if not oids_to_cancel:
                log.debug("No había órdenes abiertas para cancelar Testnet")
                return 0

            # Ejecutar cancelación
            exchange.bulk_cancel(oids_to_cancel)
            log.info(
                "Órdenes límite canceladas al cambio de vela Testnet",
                count=len(oids_to_cancel),
            )

            # Verificar que efectivamente se cancelaron
            remaining = info.open_orders(account_address)
            still_open = [o for o in remaining if o.get("coin") == self._symbol]
            if still_open:
                log.warning(
                    "Órdenes no canceladas tras bulk_cancel, reintentando...",
                    n_remaining=len(still_open),
                )
                retry_oids = [{"coin": self._symbol, "oid": o["oid"]} for o in still_open]
                exchange.bulk_cancel(retry_oids)
                # Verificación final (solo log, no bloquea)
                final_check = info.open_orders(account_address)
                final_still = [o for o in final_check if o.get("coin") == self._symbol]
                if final_still:
                    log.warning(
                        "Algunas órdenes no pudieron cancelarse tras reintento",
                        remaining=[o["oid"] for o in final_still],
                    )

            return len(oids_to_cancel)

        except Exception as e:
            log.warning("Error en cancel_all_async Testnet", error=str(e))
            return 0

    async def get_open_order_oids(self, session=None) -> set:
        """
        Retorna un set con los exchange_oids de todas las órdenes abiertas
        del símbolo en el exchange.

        Útil para que live_engine identifique qué órdenes trackeadas
        localmente ya no están abiertas (se llenaron o fueron canceladas
        externamente).
        """
        try:
            info = self._get_info()
            account_address = self._get_account_address()
            orders = info.open_orders(account_address)
            return {o["oid"] for o in orders if o.get("coin") == self._symbol}
        except Exception as e:
            log.warning("Error obteniendo órdenes abiertas Testnet", error=str(e))
            return set()

    # ── Dead Man's Switch ──────────────────────────────────────────────

    def set_dead_mans_switch(self, cancel_at_ms: int | None) -> None:
        """
        Programa la cancelación automática de todas las órdenes abiertas.
        cancel_at_ms: timestamp en ms. None para desactivar.
        Límite HL: máximo 10 activaciones/día (reset a las 00:00 UTC).
        Mínimo: 5 segundos en el futuro.
        """
        try:
            if self._dead_mans_switch_initialized and cancel_at_ms == self._last_dead_mans_switch_ms:
                log.debug("Dead Man's Switch sin cambios Testnet", cancel_at_ms=cancel_at_ms)
                return
            exchange = self._get_exchange()
            exchange.schedule_cancel(time=cancel_at_ms)
            self._last_dead_mans_switch_ms = cancel_at_ms
            self._dead_mans_switch_initialized = True
            if cancel_at_ms:
                log.info("Dead Man's Switch activado Testnet", cancel_at_ms=cancel_at_ms)
            else:
                log.info("Dead Man's Switch desactivado Testnet")
        except Exception as e:
            log.warning("Error configurando Dead Man's Switch Testnet", error=str(e))

    # ── Bulk orders (BUY + SELL simultáneo para MAX_POSICIONES > 1) ────

    async def submit_bulk_async(
        self,
        buy_order: Optional[Order],
        sell_order: Optional[Order],
        session=None,
    ) -> tuple[Optional[Order], Optional[Order]]:
        """
        Envía BUY y SELL como batch atómico usando exchange.bulk_orders() (D2).
        Retorna (buy_order, sell_order) con status actualizado.
        """
        exchange    = self._get_exchange()
        sz_decimals = self._get_sz_decimals()
        orders_payload = []
        targets: list[Order] = []

        if buy_order:
            usd = buy_order.usd_amount or 0.0
            px   = self.round_price(buy_order.price)
            buy_order.price = px
            qty  = round((usd * BUY_MARGIN_FACTOR) / px, sz_decimals) if px > 0 else 0.0
            if qty > 0 and qty * px >= 10.0:
                orders_payload.append({
                    "coin":       self._symbol,
                    "is_buy":     True,
                    "sz":         qty,
                    "limit_px":   px,
                    "order_type": self._resolve_order_type(),
                    "reduce_only": False,
                })
                targets.append(buy_order)
            else:
                self._reject(buy_order, f"nocional_insuficiente_bulk: qty={qty} px={px}")


        if sell_order:
            qty = round(sell_order.btc_amount or 0.0, sz_decimals)
            px  = self.round_price(sell_order.price)
            sell_order.price = px
            if qty > 0:
                orders_payload.append({
                    "coin":       self._symbol,
                    "is_buy":     False,
                    "sz":         qty,
                    "limit_px":   px,
                    "order_type": self._resolve_order_type(),
                    "reduce_only": True,
                })
                targets.append(sell_order)
            else:
                self._reject(sell_order, "btc_cero_bulk")

        if not orders_payload:
            return buy_order, sell_order

        try:
            self._ensure_leverage(exchange)
            result   = await asyncio.to_thread(exchange.bulk_orders, orders_payload, "na")
            statuses = result.get("response", {}).get("data", {}).get("statuses", [])
            for i, s in enumerate(statuses):
                if i >= len(targets):
                    break
                self._parse_limit_response(
                    targets[i],
                    {"status": "ok", "response": {"data": {"statuses": [s]}}},
                    is_buy=(targets[i].side == OrderSide.BUY),
                    qty=orders_payload[i]["sz"],
                )
            log.info(
                "Bulk order enviado Testnet",
                n_orders=len(orders_payload),
                symbol=self._symbol,
            )
        except Exception as e:
            log.error("Error en submit_bulk_async Testnet", error=str(e))
            for ord_ in targets:
                if ord_.status not in (OrderStatus.FILLED, OrderStatus.PENDING_LIMIT):
                    self._reject(ord_, f"bulk_error: {str(e)[:100]}")

        return buy_order, sell_order

    # ── OrderBook contract ─────────────────────────────────────────────

    def create_order(
        self,
        side: OrderSide,
        price: float,
        usd_amount: Optional[float] = None,
        btc_amount: Optional[float] = None,
    ) -> Order:
        order = Order(
            order_id=str(uuid.uuid4())[:8],
            side=side,
            price=price,
            ts=now_epoch_s(),
            usd_amount=usd_amount,
            btc_amount=btc_amount,
        )
        self._orders[order.order_id] = order
        return order

    def submit(self, order: Order) -> Order:
        """
        Coloca la orden límite en el exchange.
        Reintenta ante errores transitorios; aborta si el error no es retriable.
        """
        exchange  = self._get_exchange()
        last_exc  = None
        for attempt, delay in enumerate(RETRY_DELAYS + [None]):
            try:
                if order.side == OrderSide.BUY:
                    result = self._execute_buy_limit(exchange, order)
                else:
                    result = self._execute_sell_limit(exchange, order)
                if result:
                    return order
                # Si el rechazo no es retriable, abortar
                if order.reject_reason and any(
                    kw in order.reject_reason for kw in NON_RETRYABLE_KEYWORDS
                ):
                    log.warning("Error no retriable, abortando reintento Testnet",
                                reason=order.reject_reason)
                    return order
                # Retryable pero sin excepción: reintentar
            except Exception as e:
                last_exc = e
                log.warning(
                    f"Error en orden HL Testnet (intento {attempt + 1})",
                    error=str(e),
                    side=order.side.value,
                )
            if delay is not None:
                time.sleep(delay)
        if last_exc and order.status not in (OrderStatus.FILLED, OrderStatus.PENDING_LIMIT):
            order.status        = OrderStatus.REJECTED
            order.reject_reason = str(last_exc)
        return order

    def _reject(self, order: Order, reason: str) -> None:
        order.status        = OrderStatus.REJECTED
        order.reject_reason = reason
        order.trade = TradeRecord(
            ts=now_epoch_s(), side=order.side.value,
            price=order.price, ignored=True, ignore_reason=reason,
        )

    def check(self, order_id: str) -> Order:
        return self._orders.get(
            order_id,
            Order(
                order_id="unknown", side=OrderSide.BUY,
                price=0, ts=0,
                status=OrderStatus.REJECTED,
                reject_reason="order_id no encontrado",
            ),
        )

    async def submit_async(self, order: Order, session) -> Order:
        return await asyncio.to_thread(self.submit, order)

    async def check_async(self, order_id: str, session) -> Order:
        return await asyncio.to_thread(self.check, order_id)
