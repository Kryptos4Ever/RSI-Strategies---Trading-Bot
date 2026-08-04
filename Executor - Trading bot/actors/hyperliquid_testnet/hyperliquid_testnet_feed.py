"""
actors/hyperliquid_testnet/hyperliquid_testnet_feed.py — Feed de precios para Hyperliquid Testnet
══════════════════════════════════════════════════════════════════════════════════════════════════
Implementación completa para el entorno Hyperliquid Testnet.

Implementaciones:
  - HyperliquidRESTFeed: GET /info histórico (candles_snapshot)
  - HyperliquidWSFeed: AsyncIterator de velas en tiempo real
"""
from __future__ import annotations

import asyncio
import json
import random
import time as _time
from typing import AsyncIterator, Callable, List, Optional, Tuple

import aiohttp
import websockets

from hyperliquid.utils import constants

from actors.price_feed import PriceFeed, AsyncFeed, Candle
from support.logger import get_logger

log = get_logger("hyperliquid_testnet_feed")

HL_TESTNET_API_URL = constants.TESTNET_API_URL
HL_TESTNET_WS_URL = constants.TESTNET_API_URL.replace("https://", "wss://") + "/ws"

WATCHDOG_TIMEOUT = 30
KA_PING_INTERVAL = 20
FALLBACK_POLL_SEC = 10
MAX_BACKOFF = 60.0
RATE_LIMIT_PAUSE = 60.0


def _hl_symbol(symbol: str) -> str:
    for suffix in ("USDT", "USDC", "USD", "PERP"):
        if symbol.upper().endswith(suffix):
            return symbol.upper()[: -len(suffix)]
    return symbol.upper()


class HyperliquidRESTFeed(PriceFeed):
    """Feed histórico desde API REST de Hyperliquid Testnet."""

    CANDLE_INTERVAL = "1h"
    INTERVAL_MS = 3_600_000

    def __init__(self) -> None:
        super().__init__()
        self._info = None
        log.info("HyperliquidRESTFeed Testnet inicializado")

    def _get_info(self):
        if self._info is None:
            from hyperliquid.info import Info
            self._info = Info(HL_TESTNET_API_URL, skip_ws=True)
        return self._info

    def get_candles(self, start: int, end: int, symbol: str = "BTCUSDT") -> List[Candle]:
        hl_sym = _hl_symbol(symbol)
        info = self._get_info()
        start_ms = start * 1000
        end_ms = end * 1000
        all_candles: List[Candle] = []
        while start_ms < end_ms:
            try:
                raw = info.candles_snapshot(
                    name=hl_sym, interval=self.CANDLE_INTERVAL,
                    startTime=start_ms,
                    endTime=min(end_ms, start_ms + 5000 * self.INTERVAL_MS),
                )
            except Exception as e:
                log.error("Error obteniendo velas Hyperliquid Testnet", error=str(e), exc_info=True)
                break
            if not raw:
                break
            for k in raw:
                all_candles.append(Candle(
                    ts=int(k["t"]) // 1000, open=float(k["o"]), high=float(k["h"]),
                    low=float(k["l"]), close=float(k["c"]), volume=float(k["v"]),
                    trades_count=int(k.get("n", 0)),
                ))
            if len(raw) < 5000:
                break
            start_ms = int(raw[-1]["T"]) + 1
        log.info("Velas Hyperliquid Testnet cargadas", symbol=hl_sym, total=len(all_candles))
        return all_candles

    async def get_candles_async(
        self, session: aiohttp.ClientSession, start: int, end: int,
        symbol: str = "BTCUSDT", interval: str = "1h",
    ) -> List[Candle]:
        hl_sym = _hl_symbol(symbol)
        url = f"{HL_TESTNET_API_URL}/info"
        all_candles: List[Candle] = []
        start_ms = start * 1000
        end_ms = end * 1000
        while start_ms < end_ms:
            payload = {
                "type": "candleSnapshot",
                "req": {
                    "coin": hl_sym, "interval": interval,
                    "startTime": start_ms,
                    "endTime": min(end_ms, start_ms + 5000 * 3_600_000)}}
            try:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 429:
                        log.warning("Rate limit Hyperliquid Testnet, esperando", pause_s=RATE_LIMIT_PAUSE)
                        await asyncio.sleep(RATE_LIMIT_PAUSE)
                        break
                    raw = await resp.json(content_type=None)
            except Exception as e:
                log.warning("Error en get_candles_async REST Fallback", error=str(e))
                break
            if not raw:
                break
            for k in raw:
                all_candles.append(Candle(
                    ts=int(k["t"]) // 1000, open=float(k["o"]), high=float(k["h"]),
                    low=float(k["l"]), close=float(k["c"]), volume=float(k["v"]),
                    trades_count=int(k.get("n", 0))))
            if len(raw) < 5000:
                break
            try:
                start_ms = int(raw[-1]["T"]) + 1
            except (KeyError, IndexError):
                break
        return all_candles

    def subscribe(self, callback, symbol="BTCUSDT"): pass
    def subscribe_ticks(self, callback): pass


class HyperliquidWSFeed(PriceFeed, AsyncFeed):
    """Feed de tiempo real como AsyncIterator para Hyperliquid Testnet."""

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._latest_bid: Optional[float] = None
        self._latest_ask: Optional[float] = None
        self.fill_queue: asyncio.Queue = asyncio.Queue()
        self._user_events_task: Optional[asyncio.Task] = None
        log.info("HyperliquidWSFeed Testnet inicializado")

    @property
    def latest_bid(self) -> Optional[float]: return self._latest_bid
    @property
    def latest_ask(self) -> Optional[float]: return self._latest_ask
    @property
    def latest_mid(self) -> Optional[float]:
        if self._latest_bid is not None and self._latest_ask is not None:
            return (self._latest_bid + self._latest_ask) / 2.0
        return None

    def get_candles(self, start, end, symbol="BTCUSDT"):
        raise NotImplementedError("Usa HyperliquidRESTFeed para get_candles().")
    def subscribe(self, callback, symbol="BTCUSDT"):
        raise NotImplementedError("Usa stream() en el LiveEngine asíncrono.")
    def subscribe_ticks(self, callback):
        raise NotImplementedError("Usa stream() en el LiveEngine asíncrono.")

    async def stream(
        self, session: aiohttp.ClientSession, symbol: str = "BTCUSDT", interval: str = "1h",
    ) -> AsyncIterator[Tuple[Candle, bool]]:
        self._running = True
        hl_sym = _hl_symbol(symbol)
        rest_feed = HyperliquidRESTFeed()
        attempt = 0
        subscribe_msg = json.dumps({
            "method": "subscribe",
            "subscription": {"type": "candle", "coin": hl_sym, "interval": interval}
        })
        subscribe_msg_l2 = json.dumps({
            "method": "subscribe",
            "subscription": {"type": "l2Book", "coin": hl_sym}
        })

        last_yielded_candle: Optional[Candle] = None

        while self._running:
            try:
                log.info("Conectando WS Hyperliquid Testnet", attempt=attempt)
                async with websockets.connect(
                    HL_TESTNET_WS_URL, ping_interval=30, ping_timeout=10, close_timeout=5,
                ) as ws:
                    await ws.send(subscribe_msg)
                    await ws.send(subscribe_msg_l2)
                    attempt = 0
                    last_tick = _time.time()
                    log.info("WS Hyperliquid Testnet conectado", symbol=hl_sym)

                    async def _keepalive(ws):
                        while self._running:
                            await asyncio.sleep(KA_PING_INTERVAL)
                            try:
                                await ws.send(json.dumps({"method": "ping"}))
                            except Exception: break
                    ka_task = asyncio.create_task(_keepalive(ws))

                    while self._running:
                        elapsed = _time.time() - last_tick
                        if elapsed > WATCHDOG_TIMEOUT:
                            log.warning("Watchdog Hyperliquid Testnet: Feed Zombie", elapsed_s=f"{elapsed:.0f}")
                            ka_task.cancel()
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=35.0)
                        except asyncio.TimeoutError:
                            continue
                        last_tick = _time.time()
                        try:
                            msg_data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if msg_data.get("channel") == "l2Book":
                            self._parse_l2_book(msg_data, hl_sym)
                            continue
                        if msg_data.get("channel") == "candle":
                            candle, _ = self._parse_candle_from_msg(msg_data, hl_sym)
                            if candle:
                                if last_yielded_candle is not None and candle.ts > last_yielded_candle.ts:
                                    yield last_yielded_candle, True
                                last_yielded_candle = candle
                                yield candle, False
            except Exception as e:
                if not self._running:
                    break
                attempt += 1
                delay = min(2.0 ** attempt, MAX_BACKOFF) + random.uniform(0, 1.0)
                log.warning("WS Hyperliquid Testnet caído, REST Fallback", error=str(e),
                            retry_in_s=f"{delay:.1f}", attempt=attempt)
                fallback_end = _time.time() + delay
                _last_fallback_ts = 0
                while self._running and _time.time() < fallback_end:
                    now = int(_time.time())
                    start = now - 3600
                    candles = await rest_feed.get_candles_async(session, start, now, symbol, interval)
                    if candles:
                        latest = candles[-1]
                        if latest.ts != _last_fallback_ts:
                            _last_fallback_ts = latest.ts
                            if last_yielded_candle is not None and latest.ts > last_yielded_candle.ts:
                                yield last_yielded_candle, True
                            last_yielded_candle = latest
                            yield latest, False
                    await asyncio.sleep(FALLBACK_POLL_SEC)

    def subscribe_order_updates(self, account_address: str) -> None:
        """
        Lanza la tarea de escucha de fills en segundo plano.
        Llama desde el LiveEngine después de `await _warm_up()`.
        Los fills se publican en `self.fill_queue`.
        """
        if self._user_events_task is not None:
            return   # ya lanzada
        loop = asyncio.get_event_loop()
        self._user_events_task = loop.create_task(
            self._run_user_events(account_address),
            name="hl_testnet_user_events",
        )
        log.info("Tarea userEvents Hyperliquid Testnet lanzada", address=account_address[:8] + "...")

    async def stream_user_events(
        self, account_address: str, on_order_fill: Optional[Callable] = None) -> None:
        """
        Compatibilidad legada — usa subscribe_order_updates() + fill_queue en su lugar.
        """
        await self._run_user_events(account_address, on_order_fill_cb=on_order_fill)

    async def _run_user_events(self, account_address: str, on_order_fill_cb=None) -> None:
        """
        Loop de userEvents WS (fills de órdenes límite del usuario).
        Publica cada fill en self.fill_queue.
        Formato de cada item en la Queue:
          {
            "oid": int,            # OID del exchange
            "cloid": str | None,   # Custom Order ID si existe
            "side": "BUY" | "SELL",
            "coin": str,
            "px": float,           # Precio de ejecución
            "sz": float,           # Cantidad ejecutada
            "fee": float,          # Comisión cobrada
            "ts": int,             # Timestamp del fill (ms)
            "raw": dict,           # Datos crudos del fill
          }
        """
        subscribe_msg = json.dumps({
            "method": "subscribe",
            "subscription": {"type": "userEvents", "user": account_address},
        })
        attempt = 0
        while self._running:
            try:
                log.info("Conectando userEvents WS Hyperliquid Testnet", attempt=attempt)
                async with websockets.connect(HL_TESTNET_WS_URL, ping_interval=30, ping_timeout=10) as ws:
                    await ws.send(subscribe_msg)
                    attempt = 0
                    log.info("userEvents WS Hyperliquid Testnet conectado")
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        if msg.get("channel") != "userEvents":
                            continue
                        fills = msg.get("data", {}).get("fills", [])
                        for fill in fills:
                            parsed = self._parse_fill(fill)
                            if parsed:
                                await self.fill_queue.put(parsed)
                                if on_order_fill_cb:
                                    try:
                                        on_order_fill_cb(parsed)
                                    except Exception:
                                        pass
                                log.info(
                                    "Fill recibido por WS Testnet",
                                    side=parsed["side"],
                                    px=parsed["px"],
                                    sz=parsed["sz"],
                                    oid=parsed.get("oid"),
                                )
            except Exception as e:
                if not self._running:
                    break
                attempt += 1
                delay = min(2.0 ** attempt, MAX_BACKOFF) + random.uniform(0, 1.0)
                log.warning("userEvents WS Hyperliquid Testnet caído, reconectando", error=str(e), delay=f"{delay:.1f}")
                await asyncio.sleep(delay)

    def _parse_fill(self, fill: dict) -> Optional[dict]:
        """Convierte un fill raw de Hyperliquid al formato normalizado para el engine."""
        try:
            dir_ = fill.get("dir", "")   # "Open Long", "Close Long", "Open Short", etc.
            if "Open Long" in dir_ or "Close Short" in dir_:
                side = "BUY"
            elif "Close Long" in dir_ or "Open Short" in dir_:
                side = "SELL"
            else:
                side = "BUY" if ("Open" in dir_ or "Long" in dir_) else "SELL"
            return {
                "oid":   fill.get("oid"),
                "cloid": fill.get("cloid"),
                "side":  side,
                "coin":  fill.get("coin", ""),
                "px":    float(fill.get("px", 0.0)),
                "sz":    float(fill.get("sz", 0.0)),
                "fee":   float(fill.get("fee", 0.0)),
                "ts":    int(fill.get("time", 0)),
                "raw":   fill,
            }
        except Exception as e:
            log.warning("Error parseando fill Hyperliquid Testnet", error=str(e))
            return None

    def _parse_l2_book(self, data: dict, hl_sym: str) -> None:
        try:
            levels = data.get("data", {}).get("levels", [])
            if len(levels) >= 2 and levels[0] and levels[1]:
                self._latest_bid = float(levels[0][0]["px"])
                self._latest_ask = float(levels[1][0]["px"])
        except (KeyError, ValueError, IndexError, TypeError): pass

    def _parse_candle_from_msg(self, data: dict, hl_sym: str) -> Tuple[Optional[Candle], bool]:
        if data.get("channel") != "candle":
            return None, False
        k = data.get("data", {})
        if not k:
            return None, False
        try:
            candle = Candle(
                ts=int(k["t"]) // 1000, open=float(k["o"]), high=float(k["h"]),
                low=float(k["l"]), close=float(k["c"]), volume=float(k["v"]),
                trades_count=int(k.get("n", 0)))
            is_closed = bool(k.get("closed", False))
            return candle, is_closed
        except (KeyError, ValueError) as e:
            log.warning("Mensaje WS Hyperliquid Testnet formato inesperado", error=str(e))
            return None, False

    def stop(self) -> None:
        self._running = False
        log.info("HyperliquidWSFeed Testnet detenido")