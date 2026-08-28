"""
engine/live_engine.py — Motor de ejecución en vivo
════════════════════════════════════════════════════
Orquesta el loop principal asíncrono de trading.

Construye los actores según el modo de ejecución, inicializa la
estrategia con datos históricos, ejecuta el loop de velas en tiempo
real, gestiona riesgo/fills intra-vela y realiza shutdown graceful.

Decisiones de diseño (D5, D6, D7):
  D5 — Eventos separados _on_tick() (intra-vela) / _on_candle_close() (cierre)
  D6 — Conciliación cada vela cerrada (solo modos reales)
  D7 — Cancelar todas las órdenes abiertas al shutdown

FLUJO DE ÓRDENES LÍMITE:
  - Al detectar NUEVA vela en _on_tick(): se cancelan órdenes previas,
    se calculan señales con candle.open y se envían órdenes límite al exchange.
  - Durante la vela (_on_tick()): stop loss, fills reales y observabilidad.
  - Al cierre de vela (_on_candle_close()): solo reconciliación + persistencia.
"""
from __future__ import annotations

import asyncio
import copy
import os
import signal
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta, date
from typing import Iterable, Optional

import aiohttp

from support.types import Candle, Signal, SignalType, PositionDirection
from actors.order_book import OrderSide
from support.logger import get_logger, set_status_line_active
from support.time_utils import to_iso
from support.secrets import secrets, timeframe_seconds
from strategies.base_strategy import BaseStrategy
from state.state_manager import Checkpoint
from state.results_store import ResultsStore, collateral_currency_for_environment
from notifications.telegram_notifier import TelegramEvent

log = get_logger("live_engine")

# ── Constantes por defecto ───────────────────────────────────────────────
DEFAULT_RSI_PERIOD = 14
DEFAULT_DRAWDOWN_WARN_LEVELS = [50]
DEFAULT_TICK_LOG_INTERVAL = 30


@contextmanager
def _preserve_strategy_state(strategy: BaseStrategy):
    """Restaurar el estado de estrategia luego de una previsualizacion."""
    snapshot = copy.deepcopy(strategy.__dict__)
    try:
        yield
    finally:
        strategy.__dict__.clear()
        strategy.__dict__.update(snapshot)


def _run_strategy_tick(strategy: BaseStrategy, candle: Candle, wallet) -> list[Signal]:
    """Ejecutar el contrato canonico de backtesting para una vela."""
    if hasattr(strategy, "tick"):
        return strategy.tick(candle, wallet)
    return strategy.on_candle(candle, wallet)


def _clear_strategy_fired_markers(strategy: BaseStrategy) -> None:
    """Limpiar marcadores de senales dentro de snapshots historicos."""
    for attr in ("_fired_buys", "_fired_sells"):
        value = getattr(strategy, attr, None)
        if hasattr(value, "clear"):
            value.clear()


def _compute_overlay_row(strategy: BaseStrategy, candle: Candle) -> dict:
    """
    Calcula los overlays (niveles RSI) de la vela SIN mutar la estrategia.
    Usa el RSI vigente ANTES de que el tick avance los buffers.
    """
    if hasattr(strategy, "compute_overlay_row"):
        try:
            return strategy.compute_overlay_row(candle) or {}
        except Exception:
            return {}
    return {}


def _preview_open_signals(
    strategy: BaseStrategy,
    candle: Candle,
    wallet,
    history_before: Iterable[Candle] | None = None,
) -> tuple[list[Signal], dict]:
    """
    Calcular senales de apertura sin mutar la estrategia real.

    `BacktestEngine` procesa cada vela con `strategy.tick(candle, wallet)`: la
    estrategia calcula senales con estado previo y luego alimenta sus buffers
    con la vela procesada. Live necesita colocar ordenes al inicio de vela,
    antes de conocer el OHLC final. Por eso ejecuta `tick()` dentro de un
    snapshot, usa sus senales para colocar limites y descarta las mutaciones.

    Retorna (signals, overlays_row): el overlay se calcula con el RSI vigente
    ANTES de avanzar el tick de la vela (mismo timing que las senales).
    """
    if history_before is None:
        with _preserve_strategy_state(strategy):
            row = _compute_overlay_row(strategy, candle)
            signals = _run_strategy_tick(strategy, candle, wallet)
            return signals, row

    with _preserve_strategy_state(strategy):
        history = list(history_before)
        strategy.load_warmup(history)
        strategy.on_start(wallet)
        _clear_strategy_fired_markers(strategy)
        row = _compute_overlay_row(strategy, candle)
        signals = _run_strategy_tick(strategy, candle, wallet)
        return signals, row


def _commit_closed_candle(strategy: BaseStrategy, candle: Candle, wallet) -> list[Signal]:
    """
    Confirmar el tick real de una vela cerrada.

    Las senales retornadas se ignoran para ejecucion porque las ordenes de esa
    vela ya se colocaron al inicio. Este paso existe para avanzar los buffers
    de estrategia exactamente una vez por `candle.ts`.
    """
    return _run_strategy_tick(strategy, candle, wallet)


class LiveEngine:
    """
    Motor de trading en vivo para estrategias portátiles.

    Modos soportados:
      - papper
      - hyperliquid_mainnet
      - hyperliquid_testnet
    """

    def __init__(
        self,
        feed,
        wallet,
        ob,
        risk,
        state,
        strategy: BaseStrategy,
        environment: str = "papper",
        symbol: str = "BTCUSDT",
        saldo_inicial: float = 1000.0,
        commission_pct: float = 0.1,
        order_type: str = "market",
        max_posiciones: int = 3,
        slot_factor: float = 1.0,
        dashboard_port: int | None = None,
        telegram=None,
        candle_interval: str | None = None,
    ) -> None:
        self.environment = environment.lower()
        self.feed = feed
        self.wallet = wallet
        self.ob = ob
        self.risk = risk
        self.state = state
        self.strategy = strategy
        # Usar singleton global si no se inyecta una instancia
        if telegram is None:
            from notifications.telegram_notifier import get_notifier
            self.telegram = get_notifier()
        else:
            self.telegram = telegram
        self.session = None

        self.order_type = order_type
        self.max_posiciones = max_posiciones
        self.slot_factor = slot_factor
        self.dashboard_port = dashboard_port
        self.symbol = symbol
        self.saldo_inicial = saldo_inicial
        self.commission_pct = commission_pct

        # ── Temporalidad de las velas (desde .env o inyectada) ────────
        # Precedencia: parámetro explícito > .env > "1h"
        self.candle_interval = (candle_interval or secrets("TIMEFRAME", "1h")).strip().lower()
        self.candle_seconds = timeframe_seconds(self.candle_interval)
        explicit_collateral = secrets(
            f"{self.environment.upper()}_COLLATERAL_CURRENCY",
            secrets("COLLATERAL_CURRENCY", ""),
        ) or None
        self.account_currency = "USD"
        self.collateral_currency = collateral_currency_for_environment(self.environment, explicit_collateral)

        # Parámetros RSI leídos directamente de los atributos de la estrategia
        # (única fuente de verdad: los valores con que se construyó la estrategia)
        self._rsi_period        = getattr(strategy, '_rsi_period', DEFAULT_RSI_PERIOD)
        self._oversold          = getattr(strategy, '_oversold', 30.0)
        self._overbought        = getattr(strategy, '_overbought', 70.0)
        self._reduce_long       = getattr(strategy, '_reduce_long', 50.0)
        self._reduce_short      = getattr(strategy, '_reduce_short', 50.0)

        # ── Identificar entorno ─────────────────────────────────────────
        self._is_hyperliquid = self.environment in ("hyperliquid_mainnet", "hyperliquid_testnet")
        self._hl_testnet = (self.environment == "hyperliquid_testnet")
        self._is_papper = (self.environment == "papper")
        self._is_real = not self._is_papper

        # ── Zona horaria (desde .env, por defecto UTC-3) ──────────────
        self._timezone_offset = int(secrets("TIMEZONE", "-3"))

        # ── Estado del engine ───────────────────────────────────────────
        self._running = False
        self._shutdown_done = False
        self._candle_count = 0
        self._last_closed_ts = 0
        self._tick_count = 0
        self._last_log_time = 0.0
        self._last_flush_time = 0.0
        self._last_flush_price = 0.0
        self._total_ticks_received = 0
        self._total_ticks_processed = 0
        self._signal_timestamp: float = 0.0
        self._dd_warned_levels = set()
        self._daily_buys = 0
        self._daily_sells = 0
        self._last_daily_summary_date = date.today().isoformat()
        self._sync_failures = 0
        self._sync_failure_warned = False
        self.action_logs = deque(maxlen=500)
        self.history_candles: deque = deque(maxlen=400)
        # Diccionario ts -> Candle para evitar duplicados en history_candles
        self._history_candles_by_ts: dict[int, Candle] = {}

        # ── Estado intra-vela de riesgo/observabilidad ─────────────────
        self._last_intra_signal_ts = 0
        self._intra_candle_fired: set = set()
        self._max_fired_entries = 200
        self._intra_active_signals: list = []
        self._prev_closes: list[float] = []

        # ── Órdenes pendientes (secuenciales) ──────────────────────────
        self._pending_buy_price: Optional[float] = None
        self._pending_sell_price: Optional[float] = None
        self._pending_buy_reason: Optional[str] = None
        self._pending_sell_reason: Optional[str] = None
        self._pending_buy_ts: int = 0
        self._pending_sell_ts: int = 0
        self._current_candle_ts: int = 0
        self._latest_candle: Optional[Candle] = None

        # Límites de órdenes por vela (para dashboard)
        self._candle_limits: dict[int, dict] = {}
        # Overlays (niveles RSI) por vela, capturados al INICIO de cada vela.
        # dict ts -> {oversold, overbought, reduce_long, reduce_short}
        self._candle_overlays: dict[int, dict] = {}
        # Flag para evitar que _on_new_candle se ejecute múltiples veces
        # para la misma vela (importante en REST Fallback que inyecta varias velas)
        self._new_candle_processed: set = set()

        # ── Tracking de órdenes límite pendientes (para detectar fills) ──
        # Dict: exchange_oid -> {order, side, price, reason, candle_ts, ts_placed}
        self._pending_limit_orders: dict[int, dict] = {}
        self._last_pending_check_ts: float = 0.0
        self._pending_check_interval: float = 15.0  # segundos entre verificaciones

        # ── Baseline de PnL ────────────────────────────────────────────
        # Valor real del portfolio al inicio (después del primer sync con exchange).
        # Si el bot arranca con una posición abierta, esto evita PnL% artificial.
        self._baseline_portfolio_value: float = 0.0

    # ═════════════════════════════════════════════════════════════════════
    # MÉTODO PRINCIPAL
    # ═════════════════════════════════════════════════════════════════════

    async def run(self) -> None:
        """Punto de entrada: crea sesión, construye actores, ejecuta loop."""
        self._running = True
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        self._start_dashboard_server()
        self.start_timestamp = int(time.time())
        self._print_banner()

        async with aiohttp.ClientSession() as session:
            self.session = session
            await self._warm_up()
            # Sincronizar estado real desde el exchange (modos reales)
            if self._is_real:
                await self._sync_state_from_exchange()
            await self._main_loop()

        await self._shutdown()

    # ═════════════════════════════════════════════════════════════════════
    # WARM-UP
    # ═════════════════════════════════════════════════════════════════════

    async def _warm_up(self) -> None:
        """Carga velas históricas, inicializa estrategia y estado."""
        # Si ya se pasaron history_candles inyectadas, saltar carga REST
        stored_history = self._load_history_from_results()
        if not self.history_candles:
            print("  → Cargando velas históricas vía REST para warm-up...")
            try:
                if self._is_hyperliquid:
                    if self._hl_testnet:
                        from actors.hyperliquid_testnet.hyperliquid_testnet_feed import HyperliquidRESTFeed
                    else:
                        from actors.hyperliquid_mainnet.hyperliquid_mainnet_feed import HyperliquidRESTFeed
                    rest_feed = HyperliquidRESTFeed()
                else:
                    from actors.papper.papper_feed import PapperRESTFeed
                    rest_feed = PapperRESTFeed()

                warm_up_candles = max(
                    getattr(self.strategy, '_rsi_period', DEFAULT_RSI_PERIOD) + 1,
                    30,
                )
                if warm_up_candles < 24:
                    warm_up_candles = 24

                # Rango histórico calculado según la temporalidad configurada.
                # Se pide el doble del mínimo para que la API (máx 1000 velas/request)
                # siempre devuelva suficientes velas del intervalo correcto.
                warm_back_seconds = max(warm_up_candles * 2, 30) * self.candle_seconds
                candles_list = rest_feed.get_candles(
                    int(time.time()) - warm_back_seconds,
                    int(time.time()),
                    self.symbol,
                )

                # ── Excluir la última vela si está viva (misma vela actual) ──
                current_candle_ts = (int(time.time()) // self.candle_seconds) * self.candle_seconds
                if candles_list and candles_list[-1].ts == current_candle_ts:
                    last_candle = candles_list.pop()
                    log.info("Última vela excluida de history_candles (vela viva)",
                             ts=last_candle.ts, open=last_candle.open)

                candles_list = self._merge_candle_lists(stored_history, candles_list)
                self._set_history_candles(candles_list)
                print(f"  ✓ Cargadas {len(self.history_candles)} velas históricas.")
            except Exception as e:
                print(f"  ❌ Error al cargar históricos: {e}")
                self._set_history_candles(stored_history)
        else:
            if stored_history:
                merged = self._merge_candle_lists(stored_history, list(self.history_candles))
                self._set_history_candles(merged)
            print(f"  ✓ Usando {len(self.history_candles)} velas precargadas (inyectadas).")

        self.strategy.load_warmup(list(self.history_candles))
        self.strategy.on_start(self.wallet)

        if self.history_candles:
            self._prev_closes = [c.close for c in self.history_candles]
            print(f"  ✓ {len(self._prev_closes)} closes históricos cargados.")
        else:
            await self._fallback_current_price()
            self._prev_closes = []

        current_price = self.history_candles[-1].close if self.history_candles else getattr(self, '_fallback_price', 20000.0)
        snap = self.wallet.snapshot(current_price)
        self._print_config(snap)

        self.telegram.notify(
            TelegramEvent.BOT_STARTED,
            mode=self.environment, symbol=self.symbol,
            capital=self.saldo_inicial,
            portfolio=snap.get("portfolio_value", 0.0),
            usd_balance=snap.get("usd_balance", 0.0),
            positions=snap.get("positions_count", 0),
            btc_en_posiciones=snap.get("btc_en_posiciones", 0.0),
            rsi_period=self._rsi_period,
            oversold=self._oversold,
            overbought=self._overbought,
            reduce_long=self._reduce_long,
            reduce_short=self._reduce_short,
            max_posiciones=self.max_posiciones,
        )

        # ── Calcular límites retroactivos para velas históricas ────────
        # Usamos un set SEPARADO (_warmup_processed) para no contaminar
        # _new_candle_processed que controla _on_new_candle en el loop principal.
        # NO llamamos a strategy.on_candle() aquí porque load_warmup() ya alimentó
        # los motores BB, y on_candle() modificaría _fired_buys/_fired_sells
        # bloqueando las señales de la primera vela real.
        #
        # IMPORTANTE: Para cada vela histórica, debemos pasar SOLO los closes de
        # velas cronológicamente ANTERIORES como prev_closes, no closes futuros.
        # Esto evita que el cálculo use precios de velas que aún no existían
        # en ese momento histórico ("forward-looking bias").
        if self.history_candles:
            hist_list = list(self.history_candles)
            log.info("Calculando límites retroactivos para velas históricas...")
            _warmup_processed = set()
            for i, candle in enumerate(hist_list):
                if candle.ts in _warmup_processed:
                    continue
                _warmup_processed.add(candle.ts)
                # Previsualizar el tick con la historia disponible antes de
                # esta vela, sin mutar los buffers reales de la estrategia.
                raw_signals, overlay_row = _preview_open_signals(
                    self.strategy, candle, self.wallet, history_before=hist_list[:i]
                )
                limits_inner = {}
                for sig in raw_signals:
                    if not sig.is_actionable:
                        continue
                    s = sig.to_order_side()
                    if s == "BUY":
                        limits_inner["buy_limit"] = sig.price
                        limits_inner["buy_reason"] = sig.reason
                    elif s == "SELL":
                        limits_inner["sell_limit"] = sig.price
                        limits_inner["sell_reason"] = sig.reason
                if limits_inner:
                    self._candle_limits[candle.ts] = limits_inner
                if overlay_row:
                    self._candle_overlays[candle.ts] = overlay_row

            log.info("Límites retroactivos calculados", candles=len(hist_list))

        # ── Flush inicial para que el JSON exista desde el inicio ──────
        if hasattr(self.wallet, 'flush'):
            try:
                initial_summary = self._build_summary(current_price, {
                    "timestamp": int(time.time()), "price": current_price,
                    "status": "WARMUP_COMPLETE",
                    "signals": [],
                })
                self.wallet.flush(initial_summary, self._build_results_extra(self._latest_candle, current_price))
                log.info("Flush inicial completado — JSON disponible para dashboard")
            except Exception as e:
                log.warning("Flush inicial falló", error=str(e))

    async def _fallback_current_price(self) -> None:
        """Obtiene precio actual desde API cuando no hay velas históricas."""
        print("  ⚠ Sin velas. Obteniendo precio actual...")
        try:
            if self._is_hyperliquid:
                from hyperliquid.info import Info
                from hyperliquid.utils import constants
                api = constants.TESTNET_API_URL if self._hl_testnet else constants.MAINNET_API_URL
                hl_info = Info(api, skip_ws=True)
                hl_sym = secrets("HL_SYMBOL", "BTC")
                self._fallback_price = float(hl_info.all_mids().get(hl_sym, 0.0))
            else:
                from actors.papper.papper_feed import PapperRESTFeed
                rest = PapperRESTFeed()
                ticker = rest.get_ticker(self.symbol)
                self._fallback_price = float(ticker.get("price", 0.0))
            if self._fallback_price <= 0:
                self._fallback_price = 20000.0
            print(f"  ✓ Precio actual: ${self._fallback_price:,.1f}")
        except Exception:
            self._fallback_price = 20000.0
            print(f"  ⚠ Usando precio por defecto: ${self._fallback_price:,.1f}")

    # ═════════════════════════════════════════════════════════════════════
    # SINCRONIZACIÓN AL INICIO
    # ═════════════════════════════════════════════════════════════════════

    async def _sync_state_from_exchange(self) -> None:
        """
        Sincroniza el estado real desde el exchange al iniciar el bot.
        Se ejecuta después de _warm_up() y antes de _main_loop().
        """
        print("  → Sincronizando estado desde el exchange...")
        try:
            current_price = self.history_candles[-1].close if self.history_candles else 0.0
            if current_price <= 0:
                await self._fallback_current_price()
                current_price = getattr(self, '_fallback_price', 0.0)

            # Sincronizar wallet con el exchange
            if hasattr(self.wallet, 'sync_with_api_async'):
                try:
                    sync_result = await self.wallet.sync_with_api_async(self.session)
                    if sync_result:
                        log.info("Estado sincronizado desde exchange al inicio", result=sync_result)
                except Exception as e:
                    log.warning("sync_with_api_async falló al inicio", error=str(e))

            snap = self.wallet.snapshot(current_price)
            usd_bal = snap.get("usd_balance", 0.0)
            btc_pos = snap.get("btc_en_posiciones", 0.0)
            pos_count = snap.get("positions_count", 0)

            self.action_logs.append({
                "ts": int(time.time()), "type": "startup",
                "side": "NONE", "price": round(current_price, 2),
                "reason": "Inicio del bot",
                "message": (f"Estado sincronizado desde exchange: "
                            f"USD={usd_bal:.2f}, BTC={btc_pos:.8f}, "
                            f"posiciones={pos_count}")
            })

            # Cancelar órdenes abiertas de sesiones anteriores
            if hasattr(self.ob, 'cancel_all_async'):
                try:
                    cancelled = await self.ob.cancel_all_async(self.session)
                    if cancelled > 0:
                        self.action_logs.append({
                            "ts": int(time.time()), "type": "cancelled",
                            "side": "ALL", "price": 0,
                            "reason": "Inicio del bot",
                            "message": f"Canceladas {cancelled} órdenes de sesión anterior"
                        })
                        log.info("Órdenes de sesión anterior canceladas al inicio", count=cancelled)
                except Exception as e:
                    log.warning("Error cancelando órdenes al inicio", error=str(e))

            # Flush inicial con estado real
            if hasattr(self.wallet, 'flush'):
                try:
                    initial_summary = self._build_summary(current_price, {
                        "timestamp": int(time.time()), "price": current_price,
                        "status": "SYNCED_FROM_EXCHANGE",
                        "signals": [],
                    })
                    self.wallet.flush(initial_summary, self._build_results_extra(self._latest_candle, current_price))
                    log.info("Flush post-sync completado — JSON actualizado con estado real")
                except Exception as e:
                    log.warning("Flush post-sync falló", error=str(e))

            # Guardar el valor real del portfolio como baseline para cálculos de PnL
            # Esto captura el accountValue real del exchange (incluyendo posiciones abiertas)
            # para que el PnL% sea contra el valor real al inicio, no solo contra saldo_inicial.
            self._baseline_portfolio_value = self.wallet.portfolio_value(current_price)
            log.info("Baseline de portfolio establecido", baseline=self._baseline_portfolio_value)

            print(f"  ✓ Estado sincronizado: USD={usd_bal:.2f} BTC={btc_pos:.8f} Pos={pos_count}")

        except Exception as e:
            log.warning("Error en _sync_state_from_exchange", error=str(e))
            print(f"  ⚠ Sincronización falló: {e} — continuando con estado actual")

    # ═════════════════════════════════════════════════════════════════════
    # LOOP PRINCIPAL
    # ═════════════════════════════════════════════════════════════════════

    async def _main_loop(self) -> None:
        """Loop principal: consume el stream del feed."""
        dash_port = self._get_dashboard_port()
        print(f"\n  → Dashboard: http://localhost:{dash_port}/live_dashboard.html\n")

        try:
            async for candle, is_closed in self.feed.stream(self.session, self.symbol, self.candle_interval):
                if not self._running:
                    break

                self._total_ticks_received += 1
                await self._on_tick(candle, is_closed)

                if not is_closed:
                    continue
                if candle.ts <= self._last_closed_ts:
                    continue

                self._last_closed_ts = candle.ts
                self._candle_count += 1

                # Agregar a history_candles (sin duplicados)
                self._add_history_candle(candle)

                await self._on_candle_close(candle)

        except Exception as e:
            log.critical("FALLO CRÍTICO EN EL LOOP", error=str(e))
            import traceback
            traceback.print_exc()
            self.telegram.notify(TelegramEvent.ERROR_CRITICAL, mode=self.environment, error=str(e))
            await self._shutdown()
            raise

    def _load_history_from_results(self) -> list[Candle]:
        store = getattr(self.wallet, "results_store", None)
        if store is None:
            store = ResultsStore(
                f"live_results_{self.environment}.json",
                environment=self.environment,
                symbol=self.symbol,
                collateral_currency=self.collateral_currency,
            )
        try:
            payload = store.load()
        except Exception as exc:
            log.warning("No se pudo cargar history desde live_results", error=str(exc))
            return []

        candles: list[Candle] = []
        for entry in payload.get("history_candles", []):
            try:
                candle = Candle(
                    ts=int(entry["ts"]),
                    open=float(entry["open"]),
                    high=float(entry["high"]),
                    low=float(entry["low"]),
                    close=float(entry["close"]),
                    volume=float(entry.get("volume", 0.0)),
                )
            except (KeyError, TypeError, ValueError):
                continue
            candles.append(candle)
            limits = {}
            if entry.get("buy_limit") is not None:
                limits["buy_limit"] = entry.get("buy_limit")
            if entry.get("sell_limit") is not None:
                limits["sell_limit"] = entry.get("sell_limit")
            if entry.get("buy_reason"):
                limits["buy_reason"] = entry.get("buy_reason")
            if entry.get("sell_reason"):
                limits["sell_reason"] = entry.get("sell_reason")
            if limits:
                self._candle_limits[candle.ts] = limits
        return sorted(candles, key=lambda candle: candle.ts)

    def _merge_candle_lists(self, existing: list[Candle], incoming: list[Candle]) -> list[Candle]:
        by_ts = {candle.ts: candle for candle in existing}
        for candle in incoming:
            by_ts[candle.ts] = candle
        return [by_ts[ts] for ts in sorted(by_ts)][-400:]

    def _set_history_candles(self, candles: list[Candle]) -> None:
        ordered = sorted(candles, key=lambda candle: candle.ts)[-400:]
        self.history_candles = deque(ordered, maxlen=400)
        self._history_candles_by_ts = {candle.ts: candle for candle in ordered}

    def _add_history_candle(self, candle: Candle) -> None:
        """Agrega una vela a history_candles evitando duplicados por ts."""
        if candle.ts not in self._history_candles_by_ts:
            self._history_candles_by_ts[candle.ts] = candle
            self.history_candles.append(candle)
            # Mantener el dict limpio (max 400 entries)
            if len(self._history_candles_by_ts) > 400:
                oldest_ts = min(self._history_candles_by_ts.keys())
                del self._history_candles_by_ts[oldest_ts]

    # ═════════════════════════════════════════════════════════════════════
    # NUEVA VELA
    # ═════════════════════════════════════════════════════════════════════

    async def _on_new_candle(self, candle: Candle) -> None:
        """
        Se ejecuta al detectar el primer tick de una NUEVA vela.
        Calcula señales de la estrategia con candle.open y envía órdenes límite
        al exchange inmediatamente (no espera al cierre de vela).

        Esto permite que las órdenes límite estén en el libro del exchange
        desde el inicio de la vela, no 60 minutos después.
        """
        # Evitar procesar la misma vela múltiples veces
        if candle.ts in self._new_candle_processed:
            return
        self._new_candle_processed.add(candle.ts)

        self._print_event(f"\n  [NUEVA VELA] ts={candle.ts} O={candle.open:.1f}")

        # 0. Sincronizar wallet con exchange antes de cualquier acción
        #    Esto garantiza que positions_count, _usd y btc_en_posiciones()
        #    reflejen la realidad antes de cancelar y recalcular señales.
        if self._is_real and hasattr(self.wallet, 'sync_with_api_async'):
            try:
                await self.wallet.sync_with_api_async(self.session)
            except Exception as e:
                log.warning("Error en sync pre-cancel en nueva vela", error=str(e))

        # 1. Cancelar órdenes abiertas del exchange (modos reales)
        if self._is_real and hasattr(self.ob, 'cancel_all_async'):
            try:
                cancelled = await self.ob.cancel_all_async(self.session)
                if cancelled > 0:
                    self.action_logs.append({
                        "ts": int(time.time()), "type": "cancelled",
                        "side": "ALL", "price": 0,
                        "reason": "Nueva vela",
                        "message": f"Canceladas {cancelled} órdenes de vela anterior"
                    })
                    log.info("Órdenes de vela anterior canceladas", count=cancelled)
            except Exception as e:
                log.warning("Error cancelando órdenes en nueva vela", error=str(e))

        # 1b. Programar Dead Man's Switch (modos reales): si el proceso se cae
        #     antes del próximo evento, las órdenes límite se cancelan solas.
        #     Se programa para el cierre de la vela actual (timestamp del próximo
        #     candle), garantizando que las órdenes de esta vela queden cubiertas.
        if self._is_real and hasattr(self.ob, 'set_dead_mans_switch'):
            try:
                dms_at_ms = (candle.ts + self.candle_seconds) * 1000
                self.ob.set_dead_mans_switch(dms_at_ms)
                log.info("Dead Man's Switch programado", cancel_at_ms=dms_at_ms)
            except Exception as e:
                log.warning("Error programando Dead Man's Switch", error=str(e))

        # 2. Resetear estado de órdenes pendientes
        self._pending_buy_price = None
        self._pending_sell_price = None
        self._pending_buy_reason = None
        self._pending_sell_reason = None
        self._pending_buy_ts = 0
        self._pending_sell_ts = 0
        self._pending_limit_orders.clear()
        self._current_candle_ts = candle.ts

        # 3. Reset intra-vela de riesgo/observabilidad
        self._intra_active_signals = []
        self._intra_candle_fired.clear()

        # 4. Obtener señales previsualizando el tick de backtesting.
        #    Esto calcula los límites con estado previo + candle.open sin
        #    alimentar buffers hasta el cierre de vela. También captura los
        #    overlays (niveles RSI) al INICIO de la vela, antes de que se
        #    desarrolle.
        from actors.order_book import OrderStatus
        raw_signals, overlay_row = _preview_open_signals(self.strategy, candle, self.wallet)
        if overlay_row:
            self._candle_overlays[candle.ts] = overlay_row

        # 4b. Establecer la vela actual en el order book (para simulaciones con
        #     validación de rango, como SimulatedLimitPostOnlyOrderBook/GTC en papper).
        #     Es el equivalente a set_candle() que hace BacktestEngine en cada vela.
        if hasattr(self.ob, 'set_candle'):
            try:
                self.ob.set_candle(candle)
            except Exception as e:
                log.warning("Error al llamar set_candle en order book", error=str(e))

        # 5. Despachar cada señal por SignalType (OPEN/ADD/REDUCE/CLOSE de LONG/SHORT)
        #    usando los métodos de alto nivel del order_book.
        limits = {}

        # ── Clasificar señales para decidir batch vs individual ─────────────
        #  - entry_signals: OPEN/ADD de una dirección (necesitan un slot libre)
        #  - exit_signals:  REDUCE/CLOSE de la misma dirección que la posición
        #  Reglas:
        #    * MAX_POSICIONES == 1: NUNCA batch. Las órdenes se despachan
        #      individualmente y secuencialmente (los guards de max_posiciones
        #      y la lógica de pendientes aseguran que una orden de apertura
        #      espere a que un cierre libere el slot).
        #    * MAX_POSICIONES > 1: se puede hacer batch SOLO entre un OPEN/ADD
        #      y un REDUCE/CLOSE de la MISMA dirección (ej: ADD_LONG + REDUCE_LONG).
        #      NUNCA se agrupan apertura y cierre de direcciones opuestas.
        actionable = [s for s in raw_signals if s.is_actionable]
        pos_count = self.wallet.positions_count

        # Detectar si hay un par batch candidato (1 entrada + 1 salida, misma dir)
        batch_buy = None
        batch_sell = None
        use_batch = False
        if self.max_posiciones > 1 and hasattr(self.ob, 'submit_bulk_async') and len(actionable) == 2:
            a, b = actionable
            st_a, st_b = a.signal_type, b.signal_type
            dir_a = PositionDirection.LONG if st_a in (
                SignalType.OPEN_LONG, SignalType.ADD_LONG,
                SignalType.REDUCE_LONG, SignalType.CLOSE_LONG,
            ) else PositionDirection.SHORT
            dir_b = PositionDirection.LONG if st_b in (
                SignalType.OPEN_LONG, SignalType.ADD_LONG,
                SignalType.REDUCE_LONG, SignalType.CLOSE_LONG,
            ) else PositionDirection.SHORT
            es_a = st_a in (SignalType.OPEN_LONG, SignalType.ADD_LONG,
                            SignalType.OPEN_SHORT, SignalType.ADD_SHORT)
            es_b = st_b in (SignalType.OPEN_LONG, SignalType.ADD_LONG,
                            SignalType.OPEN_SHORT, SignalType.ADD_SHORT)
            # Un par (entrada, salida) de la MISMA dirección → batch
            if dir_a == dir_b and es_a != es_b:
                if es_a:
                    batch_buy, batch_sell = a, b
                else:
                    batch_buy, batch_sell = b, a
                use_batch = True

        if use_batch:
            # ── Modo batch (solo MAX_POSICIONES > 1 y par misma dirección) ──
            for sig in (batch_buy, batch_sell):
                sig.price = self.ob.round_price(sig.price)
                s = sig.to_order_side()
                if s == "BUY":
                    limits["buy_limit"] = sig.price
                    limits["buy_reason"] = sig.reason
                elif s == "SELL":
                    limits["sell_limit"] = sig.price
                    limits["sell_reason"] = sig.reason
                self._print_event(f"    → {s} Limit: ${sig.price:,.2f} ({sig.reason})")

            # Construir órdenes de alto nivel para el batch
            buy_order = None
            sell_order = None
            if batch_buy.signal_type in (SignalType.OPEN_LONG, SignalType.ADD_LONG):
                buy_order = await asyncio.to_thread(
                    self.ob.open_position, PositionDirection.LONG, batch_buy.price,
                    self.wallet, candle.ts, None, batch_buy.signal_type.value,
                )
            elif batch_buy.signal_type in (SignalType.OPEN_SHORT, SignalType.ADD_SHORT):
                buy_order = await asyncio.to_thread(
                    self.ob.add_position, PositionDirection.SHORT, batch_buy.price,
                    self.wallet, candle.ts, None, batch_buy.signal_type.value,
                )

            if batch_sell.signal_type in (SignalType.REDUCE_LONG, SignalType.CLOSE_LONG):
                sell_order = await asyncio.to_thread(
                    self.ob.reduce_position, PositionDirection.LONG, batch_sell.price,
                    self.wallet, candle.ts, None, batch_sell.signal_type.value,
                )
            elif batch_sell.signal_type in (SignalType.REDUCE_SHORT, SignalType.CLOSE_SHORT):
                sell_order = await asyncio.to_thread(
                    self.ob.close_position, PositionDirection.SHORT, batch_sell.price,
                    self.wallet, candle.ts, None, batch_sell.signal_type.value,
                )

            # Enviar batch atómico (solo modos reales con submit_bulk_async)
            await self.ob.submit_bulk_async(buy_order, sell_order, self.session)

            # Actualizar wallet si alguno se llenó inmediatamente
            for order in (buy_order, sell_order):
                if order and order.is_filled and order.trade:
                    self.wallet.update(order.trade)
                    self.risk.on_trade_executed()
                    if order.side == OrderSide.BUY:
                        self._daily_buys += 1
                    else:
                        self._daily_sells += 1
                elif order and order.is_pending_limit and order.exchange_oid is not None:
                    self._pending_limit_orders[order.exchange_oid] = {
                        "order": order,
                        "side": order.side.value,
                        "price": order.price,
                        "reason": order.signal_type,
                        "candle_ts": candle.ts,
                        "ts_placed": int(time.time()),
                    }
        else:
            # ── Modo individual (por defecto y siempre en MAX_POSICIONES == 1) ──
            for sig in actionable:
                sig.price = self.ob.round_price(sig.price)
                st = sig.signal_type
                direction = PositionDirection.LONG if st in (
                    SignalType.OPEN_LONG, SignalType.ADD_LONG,
                    SignalType.REDUCE_LONG, SignalType.CLOSE_LONG,
                ) else PositionDirection.SHORT

                # Guardar límites para dashboard (buy/sell según lado)
                s = sig.to_order_side()
                if s == "BUY":
                    limits["buy_limit"] = sig.price
                    limits["buy_reason"] = sig.reason
                    self._print_event(f"    → Buy Limit: ${sig.price:,.2f} ({sig.reason})")
                elif s == "SELL":
                    limits["sell_limit"] = sig.price
                    limits["sell_reason"] = sig.reason
                    self._print_event(f"    → Sell Limit: ${sig.price:,.2f} ({sig.reason})")

                # Ejecutar la operación según el tipo de señal
                if st in (SignalType.OPEN_LONG, SignalType.OPEN_SHORT):
                    await asyncio.to_thread(
                        self.ob.open_position, direction, sig.price, self.wallet,
                        candle.ts, None, st.value,
                    )
                elif st in (SignalType.ADD_LONG, SignalType.ADD_SHORT):
                    await asyncio.to_thread(
                        self.ob.add_position, direction, sig.price, self.wallet,
                        candle.ts, None, st.value,
                    )
                elif st in (SignalType.REDUCE_LONG, SignalType.REDUCE_SHORT):
                    await asyncio.to_thread(
                        self.ob.reduce_position, direction, sig.price, self.wallet,
                        candle.ts, None, st.value,
                    )
                elif st in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
                    await asyncio.to_thread(
                        self.ob.close_position, direction, sig.price, self.wallet,
                        candle.ts, None, st.value,
                    )

        self._candle_limits[candle.ts] = limits

        port_val = self.wallet.portfolio_value(candle.close)
        pnl_pct = ((port_val / self._baseline_portfolio_value) - 1) * 100 if self._baseline_portfolio_value > 0 else 0.0
        self._print_event(f"    Portfolio: ${port_val:,.2f} ({pnl_pct:+.2f}%)  "
                          f"Posiciones: {self.wallet.positions_count}\n")

    # ═════════════════════════════════════════════════════════════════════
    # EVENTO: TICK (INTRA-VELA)
    # ═════════════════════════════════════════════════════════════════════

    async def _check_pending_limit_orders(self, candle: Candle) -> None:
        """
        Verifica periódicamente si las órdenes límite pendientes se llenaron.
        Usa wallet.sync_with_api_async() para detectar cambios de posición,
        y consulta get_open_order_oids() al exchange para identificar
        qué orden específica se llenó (en lugar de asumir la primera del dict).
        Solo se ejecuta en modos reales y si hay órdenes pendientes trackeadas.
        """
        if not self._is_real:
            return
        now = time.time()
        if now - self._last_pending_check_ts < self._pending_check_interval:
            return
        if not self._pending_limit_orders:
            return
        self._last_pending_check_ts = now

        prev_positions = self.wallet.positions_count
        try:
            result = await self.wallet.sync_with_api_async(self.session)
        except Exception as e:
            log.warning("Error en verificación de órdenes límite pendientes", error=str(e))
            return

        if result is None:
            return

        # Si la wallet detectó un cambio (new_position / position_closed)
        new_positions = self.wallet.positions_count
        change_type = result.get("type")
        qty = result.get("qty", 0.0)
        real_szi = result.get("szi", qty)  # tamaño real desde exchange

        # ── Identificar qué orden se llenó ──────────────────────────────
        # Consultar al exchange qué órdenes aún están abiertas.
        # La orden llenada será aquella que estábamos trackeando pero ya
        # no aparece en la lista de órdenes abiertas del exchange.
        open_oids = set()
        if hasattr(self.ob, 'get_open_order_oids'):
            try:
                open_oids = await self.ob.get_open_order_oids(self.session)
            except Exception:
                pass

        filled_oid = None
        filled_order_info = None
        for oid, info in list(self._pending_limit_orders.items()):
            if oid not in open_oids:
                # Esta orden ya no está abierta en el exchange
                # (se llenó, o fue cancelada externamente)
                filled_oid = oid
                filled_order_info = info
                break

        # Si no se pudo identificar por exchange_oid, usar el método anterior
        # como fallback (primera orden del dict)
        if filled_order_info is None and self._pending_limit_orders:
            first_oid = next(iter(self._pending_limit_orders))
            filled_order_info = self._pending_limit_orders[first_oid]

        # Precio de ejecución: priorizar el precio límite trackeado (fuente de verdad).
        # Este es el precio exacto al que se colocó la orden en el exchange.
        # El precio del exchange (entryPx) es usado solo como fallback.
        if filled_order_info:
            exec_price = filled_order_info.get("price", 0.0)
        else:
            exec_price = result.get("price", 0.0)

        # ── Calcular expected_qty y filled_pct para cualquier tipo de cambio ──
        expected_qty = 0.0
        if filled_order_info:
            order_obj = filled_order_info.get("order")
            if order_obj and hasattr(order_obj, 'btc_amount') and order_obj.btc_amount:
                expected_qty = order_obj.btc_amount
            elif order_obj and hasattr(order_obj, 'usd_amount') and order_obj.usd_amount and exec_price > 0:
                expected_qty = order_obj.usd_amount / exec_price
        filled_pct = (qty / expected_qty * 100) if expected_qty > 0 else 100.0
        is_partial = (expected_qty > 0 and qty < expected_qty * 0.999)

        if change_type == "new_position" and new_positions > prev_positions:
            # Se llenó una orden de COMPRA (completa o parcial)
            # Si la orden ya no está en el exchange (filled_oid), está completa
            is_completed = (filled_oid is not None)
            if is_partial and not is_completed:
                self._print_event(f"\n  [PARTIAL FILL] BUY {qty:.8f} BTC @ ${exec_price:,.2f} ({filled_pct:.1f}%)")
                self.action_logs.append({
                    "ts": int(time.time()), "type": "partial_fill",
                    "side": "BUY", "price": exec_price,
                    "reason": "fill parcial detectado por sync",
                    "message": f"Fill parcial de {qty:.8f} BTC ({filled_pct:.1f}%) - "
                               f"Restante: {max(0.0, expected_qty - qty):.8f} BTC"
                })
                self.telegram.notify(
                    TelegramEvent.ORDER_PARTIALLY_FILLED, mode=self.environment,
                    side="BUY", price=exec_price,
                    qty=qty, total_qty=expected_qty,
                    filled_pct=filled_pct,
                    portfolio=self.wallet.portfolio_value(exec_price),
                )
                # NO limpiar _pending_limit_orders — la orden sigue activa
                # NO ejecutar SELL pendiente — la posición no está completa
            else:
                self._print_event(f"\n  [LIMIT FILL DETECTED] BUY {qty:.8f} BTC @ ${exec_price:,.2f}")
                self.action_logs.append({
                    "ts": int(time.time()), "type": "limit_fill",
                    "side": "BUY", "price": exec_price,
                    "reason": "orden límite llenada detectada por sync",
                    "message": f"Compra de {qty:.8f} BTC @ ${exec_price:,.2f}"
                })
                self._daily_buys += 1
                self.risk.on_trade_executed()
                # Si hay sell pendiente, ejecutarlo ahora
                if self._pending_sell_price is not None:
                    from actors.order_book import OrderStatus
                    self._print_event(f"  Ejecutando SELL pendiente post-fill")
                    await self._execute_candle_order(
                        OrderSide.SELL, self._pending_sell_price, self._pending_sell_reason, candle, OrderStatus
                    )
                    self._pending_sell_price = None
                    self._pending_sell_reason = None
                    self._pending_sell_ts = 0
                port_val = self.wallet.portfolio_value(exec_price)
                pnl_pct = ((port_val / self._baseline_portfolio_value) - 1) * 100 if self._baseline_portfolio_value > 0 else 0.0
                self.telegram.notify(
                    TelegramEvent.TRADE_EXECUTED, mode=self.environment, side="BUY",
                    price=exec_price, qty=qty, pnl_pct=pnl_pct,
                    portfolio=port_val,
                    positions=self.wallet.positions_count,
                    reason="limit_fill",
                    filled_pct=filled_pct,
                )
                await self._flush_now(exec_price)
                # Limpiar solo la orden llenada
                if filled_oid and filled_oid in self._pending_limit_orders:
                    del self._pending_limit_orders[filled_oid]
                else:
                    self._pending_limit_orders.clear()

        elif change_type == "position_closed" and new_positions < prev_positions:
            # Se llenó o cerró una orden de VENTA
            # Si la orden ya no está en el exchange (filled_oid), está completa
            is_completed = (filled_oid is not None)
            if is_partial and not is_completed:
                self._print_event(f"\n  [PARTIAL FILL] SELL {qty:.8f} BTC @ ${exec_price:,.2f} ({filled_pct:.1f}%)")
                self.action_logs.append({
                    "ts": int(time.time()), "type": "partial_fill",
                    "side": "SELL", "price": exec_price,
                    "reason": "fill parcial detectado por sync",
                    "message": f"Fill parcial de {qty:.8f} BTC ({filled_pct:.1f}%) - "
                               f"Restante: {max(0.0, expected_qty - qty):.8f} BTC"
                })
                self.telegram.notify(
                    TelegramEvent.ORDER_PARTIALLY_FILLED, mode=self.environment,
                    side="SELL", price=exec_price,
                    qty=qty, total_qty=expected_qty,
                    filled_pct=filled_pct,
                    portfolio=self.wallet.portfolio_value(exec_price),
                )
                # NO limpiar _pending_limit_orders
                # NO ejecutar BUY pendiente
            else:
                self._print_event(f"\n  [LIMIT FILL DETECTED] SELL {qty:.8f} BTC @ ${exec_price:,.2f}")
                self.action_logs.append({
                    "ts": int(time.time()), "type": "limit_fill",
                    "side": "SELL", "price": exec_price,
                    "reason": "orden límite llenada detectada por sync",
                    "message": f"Venta de {qty:.8f} BTC @ ${exec_price:,.2f}"
                })
                self._daily_sells += 1
                self.risk.on_trade_executed()
                if self._pending_buy_price is not None:
                    from actors.order_book import OrderStatus
                    self._print_event(f"  Ejecutando BUY pendiente post-fill")
                    await self._execute_candle_order(
                        OrderSide.BUY, self._pending_buy_price, self._pending_buy_reason, candle, OrderStatus
                    )
                    self._pending_buy_price = None
                    self._pending_buy_reason = None
                    self._pending_buy_ts = 0
                port_val = self.wallet.portfolio_value(exec_price)
                pnl_pct = ((port_val / self._baseline_portfolio_value) - 1) * 100 if self._baseline_portfolio_value > 0 else 0.0
                self.telegram.notify(
                    TelegramEvent.TRADE_EXECUTED, mode=self.environment, side="SELL",
                    price=exec_price, qty=qty, pnl_pct=pnl_pct,
                    portfolio=port_val,
                    positions=self.wallet.positions_count,
                    reason="limit_fill",
                    filled_pct=filled_pct,
                )
                await self.state.save_async(Checkpoint.from_wallet(
                    self.wallet, close_price=exec_price, ts=int(time.time()),
                    metadata={"estrategia": self.strategy.name},
                    risk_state=self.risk.get_state(),
                ))
                await self._flush_now(exec_price)
                # Limpiar solo la orden llenada
                if filled_oid and filled_oid in self._pending_limit_orders:
                    del self._pending_limit_orders[filled_oid]
                else:
                    self._pending_limit_orders.clear()

        elif change_type == "position_changed":
            # Fill parcial detectado (primer fill o fill adicional)
            side = result.get("side", "BUY")
            prev_qty = result.get("prev_qty", 0.0)
            current_total = result.get("szi", qty)
            delta_qty = abs(qty)  # cantidad de este cambio parcial

            # Calcular filled_pct contra la orden original
            if expected_qty > 0:
                pct = (current_total / expected_qty * 100)
            else:
                pct = 100.0

            # La orden se considera completa si:
            # 1) El exchange ya no la reporta como abierta (filled_oid encontrado), o
            # 2) El porcentaje calculado supera 99.9%
            # El cálculo de expected_qty puede ser impreciso para órdenes BUY de
            # Hyperliquid (usan BUY_MARGIN_FACTOR=0.99), por eso priorizamos filled_oid.
            is_complete = (filled_oid is not None) or (pct >= 99.9)

            if is_complete:
                # ── Fill completado al 100% ──────────────────────────────
                # Incrementar _slots_used en la wallet simulando un TradeRecord
                from actors.wallet import TradeRecord
                fake_trade = TradeRecord(
                    ts=int(time.time()),
                    side="BUY" if side == "BUY" else "SELL",
                    price=exec_price,
                    usd_spent=current_total * exec_price if side == "BUY" else None,
                    btc_bought=current_total if side == "BUY" else None,
                    btc_sold=current_total if side == "SELL" else None,
                    usd_received=current_total * exec_price if side == "SELL" else None,
                    commission=0.0,
                    reason="limit_fill_completed",
                    filled_pct=100.0,
                )
                self.wallet.update(fake_trade)

                self._print_event(f"\n  [LIMIT FILL COMPLETED] {side} {current_total:.8f} BTC @ ${exec_price:,.2f}")
                self.action_logs.append({
                    "ts": int(time.time()), "type": "limit_fill",
                    "side": side, "price": exec_price,
                    "reason": "orden límite completada (fills parciales acumulados)",
                    "message": f"{'Compra' if side == 'BUY' else 'Venta'} de {current_total:.8f} BTC @ ${exec_price:,.2f}"
                })
                if side == "BUY":
                    self._daily_buys += 1
                else:
                    self._daily_sells += 1
                self.risk.on_trade_executed()

                # Si hay orden pendiente opuesta, ejecutarla ahora
                if side == "BUY" and self._pending_sell_price is not None:
                    from actors.order_book import OrderStatus
                    self._print_event(f"  Ejecutando SELL pendiente post-fill")
                    await self._execute_candle_order(
                        OrderSide.SELL, self._pending_sell_price, self._pending_sell_reason, candle, OrderStatus
                    )
                    self._pending_sell_price = None
                    self._pending_sell_reason = None
                    self._pending_sell_ts = 0
                elif side == "SELL" and self._pending_buy_price is not None:
                    from actors.order_book import OrderStatus
                    self._print_event(f"  Ejecutando BUY pendiente post-fill")
                    await self._execute_candle_order(
                        OrderSide.BUY, self._pending_buy_price, self._pending_buy_reason, candle, OrderStatus
                    )
                    self._pending_buy_price = None
                    self._pending_buy_reason = None
                    self._pending_buy_ts = 0

                port_val = self.wallet.portfolio_value(exec_price)
                pnl_pct = ((port_val / self._baseline_portfolio_value) - 1) * 100 if self._baseline_portfolio_value > 0 else 0.0
                self.telegram.notify(
                    TelegramEvent.TRADE_EXECUTED, mode=self.environment, side=side,
                    price=exec_price, qty=current_total, pnl_pct=pnl_pct,
                    portfolio=port_val,
                    positions=self.wallet.positions_count,
                    reason="limit_fill_completed",
                    filled_pct=100.0,
                )
                if side == "SELL":
                    await self.state.save_async(Checkpoint.from_wallet(
                        self.wallet, close_price=exec_price, ts=int(time.time()),
                        metadata={"estrategia": self.strategy.name},
                        risk_state=self.risk.get_state(),
                    ))
                await self._flush_now(exec_price)
                # Limpiar la orden completada
                if filled_oid and filled_oid in self._pending_limit_orders:
                    del self._pending_limit_orders[filled_oid]
                else:
                    self._pending_limit_orders.clear()

            else:
                # ── Fill parcial (aún no completo) ───────────────────────
                self._print_event(f"\n  [PARTIAL FILL] {side} +{delta_qty:.8f} BTC @ ${exec_price:,.2f} "
                                  f"({pct:.1f}% de la orden)")
                self.action_logs.append({
                    "ts": int(time.time()), "type": "partial_fill",
                    "side": side, "price": exec_price,
                    "reason": "fill parcial detectado por sync",
                    "message": f"Fill parcial de {delta_qty:.8f} BTC ({pct:.1f}%) - "
                               f"Total acumulado: {current_total:.8f} BTC"
                })

                self.telegram.notify(
                    TelegramEvent.ORDER_PARTIALLY_FILLED, mode=self.environment,
                    side=side, price=exec_price,
                    qty=current_total,
                    total_qty=expected_qty if expected_qty > 0 else current_total,
                    filled_pct=pct,
                    portfolio=self.wallet.portfolio_value(exec_price),
                )

                # NO limpiar _pending_limit_orders — la orden sigue activa
                # NO ejecutar orden pendiente opuesta
                # NO incrementar _daily_buys/_daily_sells (se cuenta al completar)

    async def _on_tick(self, candle: Candle, is_closed: bool) -> None:
        """Procesa cada tick live sin ejecutar estrategia intra-vela.

        La estrategia matemática opera en apertura de vela mediante órdenes
        límite previsualizadas con `strategy.tick()`. Durante la vela solo se
        mantienen gestión de riesgo, observabilidad, flush periódico y
        monitoreo de fills reales.
        """
        # Validar integridad de la vela
        if candle.high < candle.low or candle.open <= 0 or candle.close <= 0:
            log.warning("Vela inválida ignorada en _on_tick", ts=candle.ts,
                        high=candle.high, low=candle.low, open=candle.open, close=candle.close)
            return

        self._latest_candle = candle

        # ── DETECTAR NUEVA VELA ─────────────────────────────────────────
        # Si el ts de la vela cambió o es el primer tick (current_candle_ts == 0)
        if candle.ts > self._current_candle_ts or self._current_candle_ts == 0:
            await self._on_new_candle(candle)

        current_price = self._get_mid_price(candle)

        # Reset de señales de riesgo si cambió la vela (seguridad)
        if candle.ts > self._last_intra_signal_ts:
            self._last_intra_signal_ts = candle.ts
            self._intra_candle_fired.clear()
        self._intra_active_signals = []

        # Stop Loss
        sl_reason = self.risk.check_stop_loss(self.wallet, current_price)
        if sl_reason and (candle.ts, "SELL") not in self._intra_candle_fired:
            sig = {
                "type": "STOP_LOSS",
                "label": f"Stop Loss ({getattr(self.risk, '_stop_loss_pct', 0)}%)",
                "side": "VENTA", "price": round(current_price, 2),
                "reason": sl_reason,
            }
            self._intra_active_signals.append(sig)
            await self._execute_stop_loss(sig, candle)
            self._intra_candle_fired.add((candle.ts, "SELL"))

        # Log + flush periódico
        self._tick_count += 1
        now = time.time()
        if now - self._last_log_time >= DEFAULT_TICK_LOG_INTERVAL:
            self._last_log_time = now
            self._total_ticks_processed = self._tick_count
            self._print_status_line(current_price)

        await self._periodic_flush(candle, current_price, now)

        # ── Verificar si órdenes límite pendientes se llenaron ──
        await self._check_pending_limit_orders(candle)

    def _get_mid_price(self, candle: Candle) -> float:
        """Retorna el precio medio (mid) desde el feed, o cae en candle.close como respaldo."""
        if hasattr(self.feed, 'latest_mid') and self.feed.latest_mid is not None:
            return self.feed.latest_mid
        return candle.close

    async def _execute_stop_loss(self, sig: dict, candle: Candle) -> None:
        """Ejecuta una venta de emergencia por stop loss sin pasar por estrategia."""
        from actors.order_book import OrderStatus

        price = sig["price"]
        reason = sig.get("reason") or sig.get("label") or "stop_loss"
        self._signal_timestamp = time.monotonic()
        self._print_event(f"\n[RISK] Stop loss SELL @ ${price:,.2f} ({reason})")

        order = await asyncio.to_thread(
            self.ob.execute_with_guards, OrderSide.SELL, price, self.wallet, candle.ts
        )

        if order.status == OrderStatus.FILLED:
            self.risk.on_trade_executed()
            self._daily_sells += 1
            port_val = self.wallet.portfolio_value(price)
            pnl_pct = ((port_val / self._baseline_portfolio_value) - 1) * 100 if self._baseline_portfolio_value > 0 else 0.0
            self.action_logs.append({
                "ts": int(time.time()), "type": "stop_loss", "side": "SELL",
                "price": price, "reason": reason, "message": "Stop loss ejecutado",
            })
            self.telegram.notify(
                TelegramEvent.TRADE_EXECUTED, mode=self.environment, side="SELL",
                price=price, qty=order.qty, pnl_pct=pnl_pct, portfolio=port_val,
                positions=self.wallet.positions_count, reason=reason,
            )
            self._print_event(f"  STOP LOSS FILLED @ ${price:,.2f} | Qty: {order.qty:.8f} BTC")
            await self._flush_now(price)
            if self._is_real:
                try:
                    await self._reconcile()
                except Exception as e:
                    log.warning("Reconciliación post-stop-loss fallida", error=str(e))
        elif order.status == OrderStatus.PENDING_LIMIT:
            self._print_event(f"  STOP LOSS LIMIT PLACED @ ${price:,.2f}")
            self.action_logs.append({
                "ts": int(time.time()), "type": "stop_loss_order", "side": "SELL",
                "price": price, "reason": reason,
                "message": "Orden de stop loss colocada en libro",
            })
            if order.exchange_oid is not None:
                self._pending_limit_orders[order.exchange_oid] = {
                    "order": order,
                    "side": "SELL",
                    "price": price,
                    "reason": reason,
                    "candle_ts": candle.ts,
                    "ts_placed": int(time.time()),
                }
            await self._flush_now(price)
        else:
            self._print_event(f"  STOP LOSS no ejecutado: {order.reject_reason}")
            self.action_logs.append({
                "ts": int(time.time()), "type": "error", "side": "SELL",
                "price": price, "reason": reason,
                "message": f"Stop loss rechazado por OrderBook: {order.reject_reason}",
            })

        self.risk.update_peak(self.wallet.portfolio_value(price))

    # ═════════════════════════════════════════════════════════════════════
    # EVENTO: CIERRE DE VELA
    # ═════════════════════════════════════════════════════════════════════

    async def _on_candle_close(self, candle: Candle) -> None:
        """
        Procesa cierre de vela.
        Ya NO calcula señales ni envía órdenes (eso se hace en _on_new_candle).
        Solo: reconciliación post-cierre, flush, checkpoint, alertas.
        """
        # Confirmar una sola vez el tick de backtesting para la vela cerrada.
        if candle.ts != getattr(self, '_last_prev_close_ts', 0):
            _commit_closed_candle(self.strategy, candle, self.wallet)
            self._prev_closes.append(candle.close)
            self._last_prev_close_ts = candle.ts

        ts_str = to_iso(candle.ts)
        self._print_event(f"\n[{ts_str}] Vela #{self._candle_count} — O={candle.open:.1f} "
                          f"H={candle.high:.1f} L={candle.low:.1f} C={candle.close:.1f} "
                          f"V={candle.volume:.4f}")

        # Sincronización post-cierre (solo modos reales)
        if self._is_real:
            try:
                await self.wallet.sync_with_api_async(self.session)
                self._sync_failures = 0
                self._sync_failure_warned = False
            except Exception as e:
                self._sync_failures += 1
                log.warning("sync_with_api falló", error=str(e),
                            sync_failures=self._sync_failures)
                if self._sync_failures >= 3 and not self._sync_failure_warned:
                    self._sync_failure_warned = True
                    self.telegram.notify(
                        TelegramEvent.ERROR_CRITICAL, mode=self.environment,
                        error=f"sync_with_api falló {self._sync_failures} veces consecutivas"
                    )

        # Flush + checkpoint
        await self._flush_state(candle, is_closed=True)
        port_val = self.wallet.portfolio_value(candle.close)
        await self.state.save_async(Checkpoint.from_wallet(
            self.wallet, close_price=candle.close, ts=candle.ts,
            metadata={"estrategia": self.strategy.name},
            risk_state=self.risk.get_state(),
        ))

        self._check_drawdown_alerts(port_val)
        pnl = ((port_val / self.saldo_inicial) - 1) * 100 if self.saldo_inicial > 0 else 0.0
        self._maybe_send_daily_summary(port_val, pnl)

    # ═════════════════════════════════════════════════════════════════════
    # EJECUCIÓN DE ÓRDENES (usado por _on_new_candle y pendientes)
    # ═════════════════════════════════════════════════════════════════════

    async def _execute_candle_order(
        self, order_side: OrderSide, price: float, reason: str, candle: Candle, OrderStatus
    ) -> None:
        """Ejecuta una orden (desde nueva vela o pendiente)."""
        self._signal_timestamp = time.monotonic()
        risk_reason = self.risk.check(order_side, price, self.wallet, candle)
        if risk_reason:
            self._print_event(f"  {order_side} @ ${price:,.2f} rechazado: {risk_reason}")
            self.risk.on_signal_rejected()
            self.action_logs.append({"ts": int(time.time()), "type": "ignored", "side": order_side.value,
                                     "price": price, "reason": reason,
                                     "message": f"Rechazado: {risk_reason}"})
            self.telegram.notify(TelegramEvent.TRADE_REJECTED, mode=self.environment, side=order_side.value,
                                 price=price, reason=risk_reason)
            return

        order = await asyncio.to_thread(self.ob.execute_with_guards, order_side, price, self.wallet, candle.ts)

        # Tratar FILLED o PENDING_LIMIT como éxito
        if order.status == OrderStatus.FILLED:
            exec_price = order.trade.price if (order.trade and order.trade.price > 0) else price
            self.risk.on_trade_executed()
            is_buy = (order_side == OrderSide.BUY)
            if is_buy:
                self._daily_buys += 1
            else:
                self._daily_sells += 1
            port_val = self.wallet.portfolio_value(exec_price)
            pnl_pct = ((port_val / self._baseline_portfolio_value) - 1) * 100 if self._baseline_portfolio_value > 0 else 0.0
            self.action_logs.append({"ts": int(time.time()), "type": "trade", "side": order_side.value,
                                     "price": exec_price, "reason": reason,
                                     "message": "Ejecutado"})
            self.telegram.notify(TelegramEvent.TRADE_EXECUTED, mode=self.environment, side=order_side.value,
                                 price=exec_price, qty=order.qty, pnl_pct=pnl_pct, portfolio=port_val,
                                 positions=self.wallet.positions_count, reason=reason)
            self._print_event(f"  {order_side} FILLED @ ${exec_price:,.2f} | {order.qty:.8f} BTC")

            # ── Si se llenó, ejecutar inmediatamente la orden pendiente opuesta ──
            if is_buy and self._pending_sell_price is not None:
                await self._execute_candle_order(
                    OrderSide.SELL, self._pending_sell_price, self._pending_sell_reason, candle, OrderStatus
                )
                self._pending_sell_price = None
                self._pending_sell_reason = None
                self._pending_sell_ts = 0
            elif not is_buy and self._pending_buy_price is not None:
                await self._execute_candle_order(
                    OrderSide.BUY, self._pending_buy_price, self._pending_buy_reason, candle, OrderStatus
                )
                self._pending_buy_price = None
                self._pending_buy_reason = None
                self._pending_buy_ts = 0

            if self._is_real:
                try:
                    await self._reconcile()
                except Exception as e:
                    log.warning("Reconciliación post-trade fallida", error=str(e))

        elif order.status == OrderStatus.PENDING_LIMIT:
            # Orden límite colocada en el libro del exchange (éxito)
            self._print_event(f"  {order_side} LIMIT PLACED @ ${price:,.2f} (en libro de órdenes)")
            self.action_logs.append({"ts": int(time.time()), "type": "limit_order", "side": order_side.value,
                                     "price": price, "reason": reason,
                                     "message": "Orden límite colocada en libro"})
            log.info("Orden límite colocada en exchange", side=order_side.value, price=price, reason=reason)
            # Trackear la orden para detectar fill posterior
            if order.exchange_oid is not None:
                self._pending_limit_orders[order.exchange_oid] = {
                    "order": order,
                    "side": order_side.value,
                    "price": price,
                    "reason": reason,
                    "candle_ts": candle.ts if candle else 0,
                    "ts_placed": int(time.time()),
                }
        else:
            self._print_event(f"  {order_side} no ejecutado: {order.reject_reason}")
        self.risk.update_peak(self.wallet.portfolio_value(candle.close))

    async def _try_execute_pending(self, candle: Candle, OrderStatus) -> None:
        """Intenta ejecutar órdenes pendientes si la posición lo permite."""
        pos = self.wallet.positions_count
        if pos == 0 and self._pending_buy_price is not None and self._pending_buy_ts == candle.ts:
            await self._execute_candle_order(
                OrderSide.BUY, self._pending_buy_price, self._pending_buy_reason, candle, OrderStatus
            )
            self._pending_buy_price = None
            self._pending_buy_reason = None
            self._pending_buy_ts = 0
        elif pos >= self.max_posiciones and self._pending_sell_price is not None and self._pending_sell_ts == candle.ts:
            await self._execute_candle_order(
                OrderSide.SELL, self._pending_sell_price, self._pending_sell_reason, candle, OrderStatus
            )
            self._pending_sell_price = None
            self._pending_sell_reason = None
            self._pending_sell_ts = 0

    # ═════════════════════════════════════════════════════════════════════
    # CONCILIACIÓN
    # ═════════════════════════════════════════════════════════════════════

    async def _reconcile(self) -> None:
        """Sincroniza balances reales con el exchange (solo modos reales)."""
        if self._is_papper or not hasattr(self.wallet, 'sync_with_api_async'):
            return
        try:
            await self.wallet.sync_with_api_async(self.session)
        except Exception as e:
            log.warning("No se pudo sincronizar saldo con API", error=str(e))

    # ═════════════════════════════════════════════════════════════════════
    # FLUSH A JSON
    # ═════════════════════════════════════════════════════════════════════

    def _build_formatted_candles(self) -> list:
        """Construye lista de velas formateadas con límites, sin duplicados."""
        seen_ts = set()
        formatted = []
        for c in list(self.history_candles)[-100:]:
            if c.ts in seen_ts:
                continue
            seen_ts.add(c.ts)
            entry = {"ts": c.ts, "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
            limits = self._candle_limits.get(c.ts, {})
            if limits.get("buy_limit"):
                entry["buy_limit"] = limits["buy_limit"]
            if limits.get("sell_limit"):
                entry["sell_limit"] = limits["sell_limit"]
            if limits.get("buy_reason"):
                entry["buy_reason"] = limits["buy_reason"]
            if limits.get("sell_reason"):
                entry["sell_reason"] = limits["sell_reason"]
            # Fusionar overlays (niveles RSI) inline usando el id como clave.
            overlays = self._candle_overlays.get(c.ts, {})
            if overlays:
                entry.update(overlays)
            formatted.append(entry)
        return formatted

    def _build_pending_orders_payload(self) -> list:
        """Construye estado observable de ordenes pendientes para live_results."""
        pending = []
        for oid, info in self._pending_limit_orders.items():
            pending.append({
                "exchange_oid": oid,
                "side": info.get("side"),
                "price": info.get("price"),
                "reason": info.get("reason"),
                "candle_ts": info.get("candle_ts"),
                "ts_placed": info.get("ts_placed"),
                "status": "open",
            })
        if self._pending_buy_price is not None:
            pending.append({
                "exchange_oid": None,
                "side": "BUY",
                "price": self._pending_buy_price,
                "reason": self._pending_buy_reason,
                "candle_ts": self._pending_buy_ts,
                "ts_placed": None,
                "status": "waiting_for_position_slot",
            })
        if self._pending_sell_price is not None:
            pending.append({
                "exchange_oid": None,
                "side": "SELL",
                "price": self._pending_sell_price,
                "reason": self._pending_sell_reason,
                "candle_ts": self._pending_sell_ts,
                "ts_placed": None,
                "status": "waiting_for_position_slot",
            })
        return pending

    def _build_results_extra(self, candle: Candle | None, current_price: float) -> dict:
        """Datos accesorios para dashboard/logs; no participan en la estrategia."""
        current_candle = None
        if candle is not None:
            current_candle = {
                "ts": candle.ts,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "current_price": current_price,
                "is_closed": candle.ts <= self._last_closed_ts,
            }
        # Overlays definidos por la estrategia (líneas/bandas/indicadores).
        # chart_data.overlays guarda SOLO la metadata (id, title, color);
        # los valores por vela se fusionan inline en cada candle usando el id.
        chart_overlay_config = []
        if hasattr(self.strategy, "get_chart_overlay_config"):
            try:
                chart_overlay_config = self.strategy.get_chart_overlay_config() or []
            except Exception:
                chart_overlay_config = []

        return {
            "action_logs": list(self.action_logs)[-100:],
            "history_candles": self._build_formatted_candles(),
            "pending_orders": self._build_pending_orders_payload(),
            "chart_data": {
                "candles": self._build_formatted_candles(),
                "overlays": chart_overlay_config,
            },
            "metadata": {
                "current_candle": current_candle,
                "current_candle_ts": self._current_candle_ts,
                "last_closed_ts": self._last_closed_ts,
                "strategy_contract": "backtest_tick_preview",
            },
        }

    async def _flush_state(self, candle: Candle, is_closed: bool = False) -> None:
        """Persiste estado actual a JSON."""
        if not hasattr(self.wallet, 'flush'):
            return

        summary = self._build_summary(candle.close, {
            "timestamp": candle.ts, "price": candle.close,
            "open": candle.open, "high": candle.high, "low": candle.low,
            "signals": list(self._intra_active_signals),
        })
        extra = self._build_results_extra(candle, candle.close)
        await asyncio.to_thread(self.wallet.flush, summary, extra)

    async def _flush_now(self, price: float) -> None:
        """Flush inmediato post-trade."""
        if not hasattr(self.wallet, 'flush'):
            return
        try:
            lm_data = {"timestamp": self._current_candle_ts, "price": price}
            if self._latest_candle:
                lm_data.update({
                    "open": self._latest_candle.open,
                    "high": max(self._latest_candle.high, price),
                    "low": min(self._latest_candle.low, price),
                    "signals": list(self._intra_active_signals),
                })
            summary = self._build_summary(price, lm_data)
            await asyncio.to_thread(
                self.wallet.flush,
                summary,
                self._build_results_extra(self._latest_candle, price),
            )
        except Exception as e:
            log.warning("Flush post-trade fallido", error=str(e))

    async def _periodic_flush(self, candle: Candle, current_price: float, now: float) -> None:
        """Flush periódico cada 5s o si el precio cambió >= 0.05%."""
        if not hasattr(self.wallet, 'flush'):
            return
        changed = False
        if self._last_flush_price > 0:
            changed = abs(current_price - self._last_flush_price) / self._last_flush_price >= 0.0005
        if now - self._last_flush_time >= 5.0 or changed:
            self._last_flush_time = now
            self._last_flush_price = current_price
            await self._flush_state(candle, is_closed=False)

    # ═════════════════════════════════════════════════════════════════════
    # DRAWDOWN Y RESUMEN DIARIO
    # ═════════════════════════════════════════════════════════════════════

    def _check_drawdown_alerts(self, port_val: float) -> None:
        peak = self.risk.peak
        if peak <= 0:
            return
        dd_pct = (peak - port_val) / peak * 100
        for level in DEFAULT_DRAWDOWN_WARN_LEVELS:
            if dd_pct >= level and level not in self._dd_warned_levels:
                self._dd_warned_levels.add(level)
                self.telegram.notify(TelegramEvent.DRAWDOWN_WARNING, mode=self.environment,
                                     drawdown_pct=dd_pct, portfolio=port_val, peak=peak)
            elif dd_pct < level and level in self._dd_warned_levels:
                self._dd_warned_levels.discard(level)

    def _maybe_send_daily_summary(self, port_val: float, pnl_pct: float) -> None:
        today = date.today().isoformat()
        if today != self._last_daily_summary_date:
            self.telegram.notify(TelegramEvent.DAILY_SUMMARY, mode=self.environment, symbol=self.symbol,
                                 pnl_pct=pnl_pct, portfolio=port_val, buys=self._daily_buys,
                                 sells=self._daily_sells, positions=self.wallet.positions_count)
            self._daily_buys = 0
            self._daily_sells = 0
            try:
                asyncio.create_task(self.state.compact_async())
            except Exception as e:
                log.warning("compact_async falló", error=str(e))
            self._last_daily_summary_date = today

    # ═════════════════════════════════════════════════════════════════════
    # SHUTDOWN GRACEFUL
    # ═════════════════════════════════════════════════════════════════════

    def _on_signal(self, signum, frame) -> None:
        print("\n[!] Señal de detención recibida. Cerrando...")
        self._running = False
        if self._shutdown_done:
            os._exit(0)

    async def _shutdown(self) -> None:
        """Apagado ordenado asíncrono."""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._running = False

        print(f"\n{'='*60}")
        print("  APAGANDO LIVE ENGINE...")

        if self.feed:
            try:
                self.feed.stop()
            except Exception:
                pass

        # Cancelar órdenes abiertas (modos reales)
        if self._is_real and hasattr(self.ob, 'cancel_all_async'):
            try:
                await self.ob.cancel_all_async(self.session)
                log.info("Órdenes abiertas canceladas")
            except Exception as e:
                log.warning("Error cancelando órdenes", error=str(e))

        # Desactivar Dead Man's Switch (modos reales) para no dejar una
        # cancelación programada residual después de un apagado ordenado.
        if self._is_real and hasattr(self.ob, 'set_dead_mans_switch'):
            try:
                self.ob.set_dead_mans_switch(None)
                log.info("Dead Man's Switch desactivado en shutdown")
            except Exception as e:
                log.warning("Error desactivando Dead Man's Switch", error=str(e))

        # Reporte final
        last_price = self.history_candles[-1].close if self.history_candles else 0.0
        port_val = self.wallet.portfolio_value(last_price) if self.wallet else 0.0
        pnl_pct = ((port_val / self.saldo_inicial) - 1) * 100 if self.saldo_inicial > 0 else 0.0
        usd_bal = self.wallet.get_usd_balance() if self.wallet else 0.0
        btc_bal = self.wallet.btc_en_posiciones() if self.wallet else 0.0

        print(f"  📅 {datetime.now(timezone(timedelta(hours=self._timezone_offset))).strftime('%d/%m %H:%M:%S')}")
        print(f"  📉 PnL: {pnl_pct:+.2f}%")
        print(f"  💼 Portfolio: ${usd_bal:,.2f} USD + {btc_bal:.8f} BTC")
        print(f"  🟢 Compras: {self._daily_buys} | 🔴 Ventas: {self._daily_sells}")
        print(f"  📌 Posiciones: {self.wallet.positions_count if self.wallet else 0}")
        print(f"  🕯️ Velas: {self._candle_count}")
        print(f"{'='*60}\n")

        if self.telegram:
            self.telegram.notify(TelegramEvent.BOT_STOPPED, mode=self.environment, pnl_pct=pnl_pct,
                                 portfolio=port_val, candles=self._candle_count)

        if hasattr(self.wallet, 'flush'):
            try:
                s = self._build_summary(0.0, {"timestamp": int(time.time()), "price": 0.0,
                                               "status": "STOPPED",
                                               "signals": [], "candles": []})
                self.wallet.flush(s, self._build_results_extra(self._latest_candle, last_price))
            except Exception:
                pass

        if self.state and hasattr(self.state, 'close'):
            try:
                self.state.close()
            except Exception:
                pass

    # ═════════════════════════════════════════════════════════════════════
    # DASHBOARD
    # ═════════════════════════════════════════════════════════════════════

    def _get_dashboard_port(self) -> int:
        """Resuelve el puerto del dashboard: usa el puerto explícito o lo deduce del entorno via secrets."""
        if self.dashboard_port is not None:
            return self.dashboard_port
        port_map = {
            "papper":              int(secrets("PAPPER_DASHBOARD_PORT", "8001")),
            "hyperliquid_mainnet":   int(secrets("HYPERLIQUID_PERPS_DASHBOARD_PORT", "8004")),
            "hyperliquid_testnet": int(secrets("HYPERLIQUID_TESTNET_DASHBOARD_PORT", "8005")),
        }
        return port_map.get(self.environment, 8001)

    def _start_dashboard_server(self) -> None:
        """Inicia el servidor del dashboard en segundo plano."""
        try:
            from dashboard.server import DashboardServer
            server = DashboardServer(self.environment)
            server.start()
            log.info("DashboardServer iniciado", port=self._get_dashboard_port())
        except Exception as e:
            log.warning("No se pudo iniciar DashboardServer", error=str(e))

    # ═════════════════════════════════════════════════════════════════════
    # HELPERS
    # ═════════════════════════════════════════════════════════════════════

    def _print_banner(self) -> None:
        """Imprime el banner de inicio con datos del entorno, símbolo y estrategia."""
        print(f"{'='*60}")
        print(f"  TRADING EN VIVO — {self.environment.upper()}")
        print(f"  Símbolo   : {self.symbol}")
        print(f"  Estrategia: {self.strategy.name}")
        print(f"  Max Posiciones: {self.max_posiciones}")
        print(f"  Slot Factor: {self.slot_factor}")
        print(f"{'='*60}\n")

    def _print_config(self, snap: dict = None) -> None:
        """Imprime la configuración actual del motor en vivo (RSI, capital, portafolio)."""
        print("+" + "-" * 70 + "+")
        print("|   LIVE ENGINE — INICIANDO                               |")
        print("+" + "-" * 70 + "+")
        print(f"  RSI Period      : {self._rsi_period}")
        print(f"  Oversold        : {self._oversold}")
        print(f"  Overbought      : {self._overbought}")
        print(f"  Reduce Long     : {self._reduce_long}")
        print(f"  Reduce Short    : {self._reduce_short}")
        print(f"  Timeframe       : {self.candle_interval}")
        print(f"  Capital .env    : ${self.saldo_inicial:,.2f}")
        if snap:
            print("-" * 72)
            print(f"  Portfolio     : ${snap.get('portfolio_value', 0):,.2f} USD")
            print(f"  Liquidez USD : ${snap.get('usd_balance', 0):,.2f}")
            print(f"  Posiciones    : {snap.get('positions_count', 0)} ({snap.get('btc_en_posiciones', 0):.8f} BTC)")
        print("-" * 72)

    def _print_event(self, msg: str) -> None:
        """Imprime un mensaje de evento en consola."""
        print(msg)

    def _print_status_line(self, current_price: float) -> None:
        """Imprime la línea de estado heartbeat con targets, portafolio y PnL."""
        tz_offset = self._timezone_offset
        tz = timezone(timedelta(hours=tz_offset))
        ts = datetime.now(tz).strftime("%H:%M:%S")
        b = f"B={self.feed.latest_bid:,.1f}" if hasattr(self.feed, 'latest_bid') and self.feed.latest_bid is not None else "B=N/A"
        a = f"A={self.feed.latest_ask:,.1f}" if hasattr(self.feed, 'latest_ask') and self.feed.latest_ask is not None else "A=N/A"
        limits = self._candle_limits.get(self._current_candle_ts, {})
        buy_limit = limits.get("buy_limit")
        sell_limit = limits.get("sell_limit")
        buy_text = f"${buy_limit:,.1f}" if buy_limit is not None else "N/A"
        sell_text = f"${sell_limit:,.1f}" if sell_limit is not None else "N/A"
        port_val = self.wallet.portfolio_value(current_price) if self.wallet else 0.0
        pnl_pct = ((port_val / self.saldo_inicial) - 1) * 100 if self.saldo_inicial > 0 else 0.0
        trades = self._daily_buys + self._daily_sells
        line = (f"[HEARTBEAT][{ts}] BTC=${current_price:,.1f} {b} {a} | "
                f"Targets BUY={buy_text} SELL={sell_text} | "
                f"Velas:{self._candle_count} Trades:{trades} | "
                f"Portfolio:${port_val:,.2f} ({pnl_pct:+.2f}%) Pos:{self.wallet.positions_count}")
        print(f"\r{line}", end="", flush=True)
        set_status_line_active(True)

    def _build_summary(self, current_price: float, live_market_data: dict) -> dict:
        if not self.wallet:
            return {"estrategia": self.strategy.name, "environment": self.environment}
        pv = self.wallet.portfolio_value(current_price)
        pp = ((pv / self.saldo_inicial) - 1) * 100 if self.saldo_inicial > 0 else 0.0

        # Incluir targets (buy_limit/sell_limit) de la vela actual en live_market
        lm = dict(live_market_data) if live_market_data else {}
        current_ts = lm.get("timestamp")
        if current_ts and current_ts in self._candle_limits:
            cl = self._candle_limits[current_ts]
            lm["targets"] = {
                "buy_target": cl.get("buy_limit"),
                "sell_target": cl.get("sell_limit"),
                "buy_label": cl.get("buy_reason", "Buy Order"),
                "sell_label": cl.get("sell_reason", "Sell Order"),
            }

        return {
            "estrategia": self.strategy.name, "environment": self.environment, "symbol": self.symbol,
            "account_currency": self.account_currency,
            "collateral_currency": self.collateral_currency,
            "fecha_fin": to_iso(int(time.time())),
            "initial_capital_usd": self.saldo_inicial,
            "portfolio_value_final": round(pv, 4),
            "usd_balance_final": round(self.wallet.get_usd_balance(), 4),
            "btc_en_posiciones_final": round(self.wallet.btc_en_posiciones(), 10),
            "positions_count_final": self.wallet.positions_count,
            "pnl_pct": round(pp, 4),
            "config": {
                "rsi_period": self._rsi_period,
                "oversold": self._oversold,
                "overbought": self._overbought,
                "reduce_long": self._reduce_long,
                "reduce_short": self._reduce_short,
                "max_posiciones": self.max_posiciones, "slot_factor": self.slot_factor,
            },
            "param_display_map": {
                **self.strategy.get_param_display_map(),
                "max_posiciones": "Max Posiciones",
                "slot_factor": "Slot Factor",
                "environment": "Entorno",
                "symbol": "Símbolo",
            },
            "timezone_offset": self._timezone_offset,
            "live_market": lm,
        }
