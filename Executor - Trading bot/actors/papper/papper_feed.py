"""
actors/papper/papper_feed.py — Feed simulado para Paper Trading (autónomo)
═══════════════════════════════════════════════════════════════════════════
Feed asíncrono que se conecta directamente al WebSocket público de Binance.
Hereda de AsyncFeed.

Papper no necesita claves API, solo datos de mercado en vivo.

ARQUITECTURA DE RESILIENCIA:
  Fase 1 — WebSocket combinado (data-stream.binance.vision)
  Fase 2 — WebSocket alternativo (stream.binance.com:9443)
  Fase 3 — REST polling con PapperRESTFeed (fallback definitivo)

Si el WebSocket se conecta pero no recibe datos por > WATCHDOG_TIMEOUT segundos
(dos ciclos consecutivos), se hace switch automático a REST polling.
"""
from __future__ import annotations

import asyncio
import json
import time as _time
from typing import AsyncIterator, Optional, Tuple, List

import aiohttp
import websockets

from actors.price_feed import PriceFeed, AsyncFeed, Candle
from support.logger import get_logger

log = get_logger("papper_feed")

# ── URLs de Binance Stream ──────────────────────────────────────────────
# Orden: data-stream.binance.vision (recomendado por Binance para market data)
WS_DATASTREAM    = "wss://data-stream.binance.vision/stream"
WS_COMBINED_9443 = "wss://stream.binance.com:9443/stream"
WS_COMBINED_443  = "wss://stream.binance.com:443/stream"

REST_BASE        = "https://api.binance.com"

WATCHDOG_TIMEOUT = 30       # segundos sin datos → considerar stale
STALE_LIMIT      = 2        # cuántos watchdog consecutivos antes de ir a REST
REST_POLL_SEC    = 3        # intervalo de polling REST (segundos)
MAX_BACKOFF      = 30.0     # backoff máximo para reconexión WS


def _parse_combined(raw: str) -> Optional[Tuple[str, dict]]:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return None
    stream = msg.get("stream")
    data = msg.get("data")
    if not stream or not data:
        return None
    return stream, data


# ═════════════════════════════════════════════════════════════════════════
# REST FALLBACK FEED
# ═════════════════════════════════════════════════════════════════════════

class PapperRESTFeed:
    """Feed de respaldo que hace polling a la API REST de Binance.
    Obtiene bookTicker (bid/ask) y klines cada REST_POLL_SEC segundos.
    """

    def __init__(self) -> None:
        self._latest_bid: Optional[float] = None
        self._latest_ask: Optional[float] = None
        log.info(f"PapperRESTFeed inicializado (polling cada {REST_POLL_SEC}s)")

    @property
    def latest_bid(self) -> Optional[float]:
        return self._latest_bid

    @property
    def latest_ask(self) -> Optional[float]:
        return self._latest_ask

    @property
    def latest_mid(self) -> Optional[float]:
        if self._latest_bid is not None and self._latest_ask is not None:
            return (self._latest_bid + self._latest_ask) / 2.0
        return None

    def get_ticker(self, symbol: str = "BTCUSDT") -> dict:
        """Obtiene el ultimo precio de mercado de forma sincrona."""
        try:
            import json as _json
            import urllib.request

            url = f"{REST_BASE}/api/v3/ticker/price?symbol={symbol}"
            with urllib.request.urlopen(url, timeout=10) as resp:
                return _json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            log.debug(f"PapperRESTFeed ticker error: {e}")
            return {}

    def get_candles(self, start: int, end: int, symbol: str = "BTCUSDT", interval: str = "1h") -> List[Candle]:
        """Obtiene velas historicas del intervalo indicado de forma sincrona para warm-up."""
        try:
            import json as _json
            import urllib.parse
            import urllib.request

            params = urllib.parse.urlencode({
                "symbol": symbol,
                "interval": interval,
                "startTime": int(start) * 1000,
                "endTime": int(end) * 1000,
                "limit": 1000,
            })
            url = f"{REST_BASE}/api/v3/klines?{params}"
            with urllib.request.urlopen(url, timeout=15) as resp:
                raw = _json.loads(resp.read().decode("utf-8"))
            candles = []
            for k in raw:
                candle = self.kline_to_candle(k)
                if candle is not None:
                    candles.append(candle)
            return candles
        except Exception as e:
            log.debug(f"PapperRESTFeed get_candles error: {e}")
            return []

    async def get_book_ticker(
        self, session: aiohttp.ClientSession, symbol: str = "BTCUSDT",
    ) -> Tuple[Optional[float], Optional[float]]:
        """Obtiene bid/ask desde GET /api/v3/ticker/bookTicker (aiohttp)."""
        try:
            url = f"{REST_BASE}/api/v3/ticker/bookTicker?symbol={symbol}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None, None
                data = await resp.json()
                bid = float(data.get("bidPrice", 0))
                ask = float(data.get("askPrice", 0))
                return (bid, ask) if bid > 0 and ask > 0 else (None, None)
        except Exception as e:
            log.debug(f"PapperRESTFeed bookTicker error: {e}")
            return None, None

    async def get_klines(
        self, session: aiohttp.ClientSession, symbol: str = "BTCUSDT",
        interval: str = "1h", limit: int = 1,
    ) -> List[dict]:
        """Obtiene la última vela desde GET /api/v3/klines (aiohttp)."""
        try:
            url = f"{REST_BASE}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return []
                return await resp.json()
        except Exception as e:
            log.debug(f"PapperRESTFeed klines error: {e}")
            return []

    def kline_to_candle(self, k: list) -> Optional[Candle]:
        """Convierte un kline de Binance (array) a Candle."""
        if not k or len(k) < 11:
            return None
        try:
            return Candle(
                ts=int(k[0]) // 1000,
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
                trades_count=int(k[8]),
            )
        except (ValueError, TypeError, IndexError) as e:
            log.debug(f"PapperRESTFeed parse error: {e}")
            return None

    async def poll_candle(
        self, session: aiohttp.ClientSession, symbol: str = "BTCUSDT", interval: str = "1h",
    ) -> Optional[Candle]:
        """Polling único: actualiza bid/ask y retorna la última vela si hay cambio."""
        bid, ask = await self.get_book_ticker(session, symbol)
        if bid is not None and ask is not None:
            self._latest_bid = bid
            self._latest_ask = ask

        klines = await self.get_klines(session, symbol, interval, limit=1)
        if klines:
            return self.kline_to_candle(klines[-1])
        return None


# ═════════════════════════════════════════════════════════════════════════
# MAIN FEED: PapperWSFeed (WebSocket + REST Fallback automático)
# ═════════════════════════════════════════════════════════════════════════

class PapperWSFeed(PriceFeed, AsyncFeed):
    """Feed de tiempo real autónomo para Paper Trading.
    Se conecta directamente al stream público de Binance.

    Falla en cascada:
      1. data-stream.binance.vision (WebSocket)
      2. stream.binance.com:9443    (WebSocket alternativo)
      3. REST polling                (fallback definitivo)
    """

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._latest_bid: Optional[float] = None
        self._latest_ask: Optional[float] = None
        self._rest_feed = PapperRESTFeed()
        self._force_rest = False  # True → salta directo a REST polling
        self._stale_count = 0     # contador de watchdog consecutivos
        log.info("PapperWSFeed inicializado (autónomo)")

    @property
    def latest_bid(self) -> Optional[float]:
        return self._latest_bid or self._rest_feed.latest_bid

    @property
    def latest_ask(self) -> Optional[float]:
        return self._latest_ask or self._rest_feed.latest_ask

    @property
    def latest_mid(self) -> Optional[float]:
        b = self.latest_bid
        a = self.latest_ask
        if b is not None and a is not None:
            return (b + a) / 2.0
        return None

    async def stream(
        self, session: aiohttp.ClientSession, symbol: str = "BTCUSDT", interval: str = "1h",
    ) -> AsyncIterator[Tuple[Candle, bool]]:
        self._running = True
        self._latest_bid = None
        self._latest_ask = None
        self._force_rest = False
        self._stale_count = 0
        symbol_lower = symbol.lower()
        streams = f"{symbol_lower}@kline_{interval}/{symbol_lower}@bookTicker"

        # Lista de URLs en orden de preferencia
        ws_urls = [
            f"{WS_DATASTREAM}?streams={streams}",       # 1er intento: data-stream.binance.vision
            f"{WS_COMBINED_9443}?streams={streams}",     # 2do intento: stream.binance.com:9443
            f"{WS_COMBINED_443}?streams={streams}",      # 3er intento: stream.binance.com:443
        ]

        last_rest_candle_ts: int = 0
        attempt = 0
        url_idx = 0

        while self._running:
            # ── MODO REST FALLBACK ─────────────────────────────────────
            if self._force_rest:
                log.info(f"PapperWSFeed en modo REST fallback (polling cada {REST_POLL_SEC}s)")
                while self._running:
                    candle = await self._rest_feed.poll_candle(session, symbol, interval)
                    if candle and candle.ts > last_rest_candle_ts:
                        last_rest_candle_ts = candle.ts
                        self._latest_bid = self._rest_feed.latest_bid
                        self._latest_ask = self._rest_feed.latest_ask
                        log.info(f"REST poll: candle ts={candle.ts} close={candle.close:.2f} "
                                 f"bid={self._latest_bid or 0:.2f} ask={self._latest_ask or 0:.2f}")
                        yield candle, True

                    # Pequeño sleep para no saturar la API
                    await asyncio.sleep(REST_POLL_SEC)
                return

            # ── MODO WEBSOCKET ─────────────────────────────────────────
            try:
                ws_url = ws_urls[url_idx % len(ws_urls)]
                log.info(f"PapperWSFeed conectando (intento={attempt + 1}, url={ws_url})")

                async with websockets.connect(
                    ws_url, ping_interval=20, ping_timeout=10, close_timeout=5,
                ) as ws:
                    log.info(f"PapperWSFeed conectado exitosamente (url={ws_url})")

                    # Resetear contadores tras conectar exitosamente
                    attempt = 0
                    url_idx = 0
                    stale_this_session = False
                    last_tick = _time.time()
                    _last_kline_ts = 0  # para loggear solo la 1ra kline de cada vela

                    while self._running:
                        elapsed = _time.time() - last_tick

                        # Watchdog: sin datos por demasiado tiempo
                        if elapsed > WATCHDOG_TIMEOUT:
                            self._stale_count += 1
                            log.warning(
                                f"PapperWSFeed watchdog — sin datos por {WATCHDOG_TIMEOUT}s "
                                f"(stale_count={self._stale_count}/{STALE_LIMIT})"
                            )
                            # Si ya tuvimos un watchdog antes en esta misma sesión,
                            # o acumulamos demasiados, pasamos a REST fallback
                            if stale_this_session or self._stale_count >= STALE_LIMIT:
                                log.warning(
                                    "PapperWSFeed — conexión stale persistente, "
                                    "cambiando a REST fallback"
                                )
                                self._force_rest = True
                            stale_this_session = True
                            break

                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            continue

                        # Resetear watchdog al recibir datos
                        last_tick = _time.time()

                        parsed = _parse_combined(raw)
                        if parsed is None:
                            continue

                        sname, data = parsed

                        if sname == f"{symbol_lower}@bookTicker":
                            # Solo actualizar bid/ask en memoria, sin logging
                            try:
                                bid = float(data.get("b", 0))
                                ask = float(data.get("a", 0))
                                if bid > 0 and ask > 0:
                                    self._latest_bid = bid
                                    self._latest_ask = ask
                            except (ValueError, TypeError):
                                pass

                        elif sname == f"{symbol_lower}@kline_{interval}":
                            k = data.get("k", {})
                            if k.get("e") == "kline":
                                candle = Candle(
                                    ts=k["t"] // 1000, open=float(k["o"]),
                                    high=float(k["h"]), low=float(k["l"]),
                                    close=float(k["c"]), volume=float(k["v"]),
                                    trades_count=int(k["n"]),
                                )
                                is_closed = bool(k["x"])
                                # Log solo la 1ra kline de cada vela y cuando se cierra
                                if candle.ts != _last_kline_ts or is_closed:
                                    _last_kline_ts = candle.ts
                                    log.info(f"kline {'CERRADA' if is_closed else 'NUEVA'} "
                                             f"ts={candle.ts} open={candle.open:.2f} "
                                             f"close={candle.close:.2f}")
                                yield candle, is_closed

            except Exception as e:
                if not self._running:
                    break

                attempt += 1
                delay = min(2.0 ** attempt, MAX_BACKOFF)

                # Si fallaron todas las URLs, probar con REST fallback
                if attempt >= len(ws_urls) * 2:
                    log.warning(
                        "PapperWSFeed — múltiples fallos WS, cambiando a REST fallback"
                    )
                    self._force_rest = True
                    continue

                # Rotar URL en cada intento
                url_idx += 1

                log.warning(
                    f"PapperWSFeed error, reconectando (intento={attempt}, "
                    f"url_idx={url_idx % len(ws_urls)}, delay={delay:.1f}s) "
                    f"error={e}"
                )
                await asyncio.sleep(delay)

    # ── Implementación de PriceFeed ─────────────────────────────────────
    def get_candles(self, start: int, end: int, symbol: str = "BTCUSDT", interval: str = "1h") -> List[Candle]:
        """Delega en PapperRESTFeed para obtener velas históricas."""
        return self._rest_feed.get_candles(start, end, symbol, interval)

    def subscribe(self, callback, symbol: str = "BTCUSDT") -> None:
        """No aplica para el feed asíncrono; usar stream() en su lugar."""
        log.debug("PapperWSFeed.subscribe() llamado — sin efecto, usar stream()")

    def stop(self) -> None:
        self._running = False
        log.info("PapperWSFeed detenido")
