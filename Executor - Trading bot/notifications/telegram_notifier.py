"""
notifications/telegram_notifier.py — Sistema de alertas Telegram
══════════════════════════════════════════════════════════════════
Envía alertas a Telegram de forma asíncrona (thread separado)
para no bloquear el loop principal de trading.

Eventos soportados:
  - TRADE_EXECUTED   → 🟢 BUY / 🔴 SELL ejecutado
  - TRADE_REJECTED   → ⚠️  Señal rechazada (riesgo, guardias)
  - SIGNAL_DETECTED  → 📊 Señal detectada (sin ejecutar aún)
  - DRAWDOWN_WARNING → 🚨 Alerta de drawdown
  - BOT_STARTED      → 🚀 Bot iniciado
  - BOT_STOPPED      → 🛑 Bot detenido
  - ERROR_CRITICAL   → 💥 Error crítico en el loop
  - WS_RECONNECTED   → 🔄 WebSocket reconectado
  - DAILY_SUMMARY    → 📈 Resumen diario

Configuración en .env:
  TELEGRAM_BOT_TOKEN=xxxx:yyyy
  TELEGRAM_CHAT_ID=-1001234567890
  TELEGRAM_ENABLED=true
  TELEGRAM_MIN_LEVEL=INFO    # DEBUG | INFO | WARNING | ERROR

Uso:
  from notifications.telegram_notifier import TelegramNotifier, TelegramEvent
  notifier = TelegramNotifier()
  notifier.notify(TelegramEvent.BOT_STARTED, mode="real", symbol="BTCUSDT", capital=1000.0)
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from support.logger import get_logger

log = get_logger("telegram_notifier")


class TelegramEvent(str, Enum):
    TRADE_EXECUTED          = "TRADE_EXECUTED"
    TRADE_REJECTED          = "TRADE_REJECTED"
    SIGNAL_DETECTED         = "SIGNAL_DETECTED"
    DRAWDOWN_WARNING        = "DRAWDOWN_WARNING"
    BOT_STARTED             = "BOT_STARTED"
    BOT_STOPPED             = "BOT_STOPPED"
    ERROR_CRITICAL          = "ERROR_CRITICAL"
    WS_RECONNECTED          = "WS_RECONNECTED"
    DAILY_SUMMARY           = "DAILY_SUMMARY"
    ORDER_PARTIALLY_FILLED  = "ORDER_PARTIALLY_FILLED"
    TEST                    = "TEST"


# Niveles de importancia para filtrado
_EVENT_LEVEL = {
    TelegramEvent.TRADE_EXECUTED:          "INFO",
    TelegramEvent.TRADE_REJECTED:          "WARNING",
    TelegramEvent.SIGNAL_DETECTED:         "DEBUG",
    TelegramEvent.DRAWDOWN_WARNING:        "WARNING",
    TelegramEvent.BOT_STARTED:             "INFO",
    TelegramEvent.BOT_STOPPED:             "INFO",
    TelegramEvent.ERROR_CRITICAL:          "ERROR",
    TelegramEvent.WS_RECONNECTED:          "WARNING",
    TelegramEvent.DAILY_SUMMARY:           "INFO",
    TelegramEvent.ORDER_PARTIALLY_FILLED:  "WARNING",
    TelegramEvent.TEST:                    "DEBUG",
}

_LEVEL_NUM = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}


@dataclass
class _Message:
    text: str
    retry_count: int = 0
    max_retries: int = 3
    thread_id: Optional[int] = None  # Telegram Forum Topic ID


class TelegramNotifier:
    """
    Notificador Telegram con cola asíncrona y rate limiting.

    Envío en thread daemon separado — nunca bloquea el loop de trading.
    Rate limit: máximo 1 mensaje cada MIN_INTERVAL_S segundos.
    """

    MIN_INTERVAL_S = 2.0    # segundos mínimos entre mensajes
    MAX_QUEUE_SIZE = 200     # mensajes máximos en cola (descarta los más viejos)
    RETRY_DELAYS  = [5, 15, 60]  # segundos entre reintentos

    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None) -> None:
        """Inicializa el notificador: carga token, chat_id y topic_ids desde secrets."""
        from support.secrets import secrets
        # Inicializar topic_ids ANTES del try para que siempre exista
        self._topic_ids: dict[str, int] = {}
        try:
            self._enabled = secrets("TELEGRAM_ENABLED", "false").lower() == "true"
            self._token   = token or secrets("TELEGRAM_BOT_TOKEN", "")
            self._chat_id = chat_id or secrets("TELEGRAM_CHAT_ID", "")
            self._min_level = secrets("TELEGRAM_MIN_LEVEL", "INFO").upper()

            # Topic IDs para cada modo de trading (Telegram Forum Topics)
            # Los nombres coinciden EXACTAMENTE con los modos del executor:
            #   papper, hyperliquid_mainnet, hyperliquid_testnet
            for mode in ("papper", "hyperliquid_mainnet", "hyperliquid_testnet"):
                key = f"TELEGRAM_TOPIC_{mode.upper()}"
                val = secrets(key, "")
                if val:
                    try:
                        self._topic_ids[mode] = int(val)
                    except ValueError:
                        pass
        except Exception:
            self._enabled = False
            self._token   = ""
            self._chat_id = ""
            self._min_level = "INFO"
            self._topic_ids = {}

        self._queue: queue.Queue[_Message] = queue.Queue(maxsize=self.MAX_QUEUE_SIZE)
        self._last_sent_time = 0.0
        self._lock = threading.Lock()

        if self._enabled and self._token and self._chat_id:
            self._worker_thread = threading.Thread(
                target=self._worker_loop, daemon=True, name="telegram-notifier"
            )
            self._worker_thread.start()
            log.info("TelegramNotifier iniciado", chat_id=self._chat_id)
        else:
            self._worker_thread = None
            if not self._enabled:
                log.info("TelegramNotifier deshabilitado (TELEGRAM_ENABLED=false)")
            else:
                log.warning("TelegramNotifier: token o chat_id no configurado")

    # ══════════════════════════════════════════════════════════════════════════
    # API PÚBLICA
    # ══════════════════════════════════════════════════════════════════════════

    def notify(self, event: TelegramEvent, **kwargs: Any) -> None:
        """
        Encola un mensaje de notificación.
        El envío se realiza en el thread worker (no bloquea).

        Si el mensaje incluye el parámetro 'mode', y existe un topic_id
        configurado para ese modo (en .env como TELEGRAM_TOPIC_*),
        el mensaje se enviará al Topic correspondiente del grupo.
        """
        if not self._enabled or not self._worker_thread:
            return

        # Filtrar por nivel mínimo configurado
        event_level = _EVENT_LEVEL.get(event, "INFO")
        if _LEVEL_NUM.get(event_level, 0) < _LEVEL_NUM.get(self._min_level, 0):
            return

        text = self._format_message(event, **kwargs)

        # Determinar thread_id (Topic) según el entorno de trading
        # Las claves en _topic_ids coinciden exactamente con los modos del executor
        thread_id: Optional[int] = None
        mode = kwargs.get("mode", "").lower()
        if mode and mode in self._topic_ids:
            thread_id = self._topic_ids[mode]

        msg = _Message(text=text, thread_id=thread_id)

        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            # Cola llena: descartamos el mensaje más antiguo y agregamos el nuevo
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(msg)
            except queue.Full:
                pass

    def send_test(self) -> bool:
        """Envía un mensaje de prueba de forma síncrona. Retorna True si tuvo éxito."""
        if not self._token or not self._chat_id:
            print("❌ Token o chat_id no configurado.")
            return False
        text = "🧪 *Bot de Trading* — Conexión Telegram funcionando correctamente."
        return self._send_now(text)

    # ══════════════════════════════════════════════════════════════════════════
    # FORMATEO DE MENSAJES
    # ══════════════════════════════════════════════════════════════════════════

    def _format_message(self, event: TelegramEvent, **kw: Any) -> str:
        """Formatea el texto del mensaje según el tipo de evento."""
        from datetime import datetime, timezone, timedelta
        tz_ar = timezone(timedelta(hours=-3))
        now_str = datetime.now(tz_ar).strftime("%d/%m %H:%M:%S")

        mode = kw.get("mode", "?").upper()
        symbol = kw.get("symbol", "BTC/USD")

        if event == TelegramEvent.BOT_STARTED:
            # Parámetros RSI Mean Reversion
            rsi_period  = kw.get('rsi_period', '?')
            oversold    = kw.get('oversold', '?')
            overbought  = kw.get('overbought', '?')
            reduce_long = kw.get('reduce_long', '?')
            reduce_short = kw.get('reduce_short', '?')
            return (
                f"🚀 *BOT INICIADO*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📅 {now_str}\n"
                f"🔧 Modo: `{mode}`\n"
                f"💱 Par: `{symbol}`\n"
                f"💰 Portfolio Total: `${kw.get('portfolio', kw.get('capital', 0)):,.2f} USD`\n"
                f"💵 Liquidez USD: `${kw.get('usd_balance', 0):,.2f}`\n"
                f"📌 Posiciones: `{kw.get('positions_count', kw.get('positions', 0))}` ({kw.get('btc_en_posiciones', 0):.8f} BTC)\n"
                f"📊 RSI Period: `{rsi_period}` | Oversold: `{oversold}` | Overbought: `{overbought}`\n"
                f"📊 Reduce Long: `{reduce_long}` | Reduce Short: `{reduce_short}`\n"
                f"📌 Max posiciones: `{kw.get('max_posiciones','?')}`"
            )

        elif event == TelegramEvent.BOT_STOPPED:
            pnl = kw.get("pnl_pct", 0.0)
            emoji = "📈" if pnl >= 0 else "📉"
            return (
                f"🛑 *BOT DETENIDO*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📅 {now_str} | Modo: `{mode}`\n"
                f"{emoji} PnL: `{pnl:+.2f}%`\n"
                f"💼 Portfolio: `${kw.get('portfolio', 0):,.2f} USD`\n"
                f"🕯️ Velas procesadas: `{kw.get('candles', 0):,}`"
            )

        elif event == TelegramEvent.TRADE_EXECUTED:
            side = kw.get("side", "?")
            emoji = "🟢" if side == "BUY" else "🔴"
            pnl = kw.get("pnl_pct", 0.0)
            pnl_sign = "+" if pnl >= 0 else ""

            # Porcentaje de llenado acumulado
            filled_pct = kw.get("filled_pct", None)
            fill_line = ""
            if filled_pct is not None:
                fill_emoji = "✅" if filled_pct >= 100.0 else "⏳"
                fill_line = f"\n{fill_emoji} Llenado: `{filled_pct:.1f}%`"

            # Slippage y latencia (solo si hay valores reales, omitir en simulado)
            sl_line = ""
            _slippage = kw.get("slippage_pct", "N/A")
            _latency  = kw.get("latency_ms", "N/A")
            if _slippage != "N/A" and _slippage is not None:
                sl_line = f"\n⚡ Slippage: `{_slippage:.4f}%` | Latencia: `{_latency:.0f}ms`" if isinstance(_latency, (int, float)) else f"\n⚡ Slippage: `{_slippage:.4f}%` | Latencia: `{_latency}`"

            return (
                f"{emoji} *TRADE EJECUTADO* — `{mode}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📅 {now_str}\n"
                f"{'🛒 Compra' if side == 'BUY' else '💸 Venta'} @ `${kw.get('price', 0):,.2f}`\n"
                f"🔢 Cantidad: `{kw.get('qty', 0):.8f}` BTC"
                f"{fill_line}\n"
                f"📊 PnL Total: `{pnl_sign}{pnl:.2f}%`\n"
                f"💼 Portfolio: `${kw.get('portfolio', 0):,.2f} USD`\n"
                f"📌 Posiciones: `{kw.get('positions', 0)}`\n"
                f"📝 `{kw.get('reason', '')}`"
                f"{sl_line}"
            )

        elif event == TelegramEvent.TRADE_REJECTED:
            return (
                f"⚠️ *TRADE RECHAZADO* — `{mode}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📅 {now_str}\n"
                f"❌ {kw.get('side', '?')} @ `${kw.get('price', 0):,.2f}`\n"
                f"🔍 Motivo: `{kw.get('reason', 'desconocido')}`"
            )

        elif event == TelegramEvent.DRAWDOWN_WARNING:
            dd = kw.get("drawdown_pct", 0.0)
            return (
                f"🚨 *ALERTA DRAWDOWN* — `{mode}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📅 {now_str}\n"
                f"📉 Drawdown actual: `{dd:.1f}%`\n"
                f"💼 Portfolio: `${kw.get('portfolio', 0):,.2f} USD`\n"
                f"⚠️ Peak: `${kw.get('peak', 0):,.2f} USD`"
            )

        elif event == TelegramEvent.ERROR_CRITICAL:
            error_str = str(kw.get("error", "desconocido"))[:200]
            return (
                f"💥 *ERROR CRÍTICO* — `{mode}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📅 {now_str}\n"
                f"❌ `{error_str}`\n"
                f"🔧 El bot puede haberse detenido. Verificar."
            )

        elif event == TelegramEvent.WS_RECONNECTED:
            return (
                f"🔄 *WS RECONECTADO* — `{mode}`\n"
                f"📅 {now_str} | `{kw.get('feed', 'WebSocket')}` reconectado."
            )

        elif event == TelegramEvent.DAILY_SUMMARY:
            pnl = kw.get("pnl_pct", 0.0)
            emoji = "📈" if pnl >= 0 else "📉"
            return (
                f"📊 *RESUMEN DIARIO* — `{mode}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📅 {now_str}\n"
                f"{emoji} PnL: `{pnl:+.2f}%`\n"
                f"💼 Portfolio: `${kw.get('portfolio', 0):,.2f} USD`\n"
                f"🟢 Compras: `{kw.get('buys', 0)}` | 🔴 Ventas: `{kw.get('sells', 0)}`\n"
                f"📌 Posiciones abiertas: `{kw.get('positions', 0)}`"
            )

        elif event == TelegramEvent.SIGNAL_DETECTED:
            side = kw.get("side", "?")
            emoji = "🔵" if side == "BUY" else "🟡"
            return (
                f"{emoji} *Señal detectada* — `{mode}`\n"
                f"📅 {now_str} | {side} @ `${kw.get('price', 0):,.2f}`\n"
                f"📝 `{kw.get('reason', '')}`"
            )

        elif event == TelegramEvent.ORDER_PARTIALLY_FILLED:
            side = kw.get("side", "?")
            emoji = "🟢" if side == "BUY" else "🔴"
            filled_qty = kw.get("qty", 0)
            total_qty = kw.get("total_qty", filled_qty)
            filled_pct = kw.get("filled_pct", 0)
            remaining_qty = max(0.0, total_qty - filled_qty)
            return (
                f"⏳ *LLENADO PARCIAL* — `{mode}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📅 {now_str}\n"
                f"{emoji} Orden {side} @ `${kw.get('price', 0):,.2f}`\n"
                f"📦 `{filled_qty:.8f} / {total_qty:.8f} BTC` ({filled_pct:.1f}%)\n"
                f"⏳ Restante: `{remaining_qty:.8f} BTC`\n"
                f"💼 Portfolio: `${kw.get('portfolio', 0):,.2f} USDT`\n"
                f"📌 Posiciones: `{kw.get('positions', 0)}`"
            )

        elif event == TelegramEvent.TEST:
            return f"🧪 *TEST* — {now_str}\nConexión Telegram funcionando correctamente. ✅"

        else:
            return f"ℹ️ *{event.value}* — {now_str}\n{kw}"

    # ══════════════════════════════════════════════════════════════════════════
    # WORKER THREAD
    # ══════════════════════════════════════════════════════════════════════════

    def _worker_loop(self) -> None:
        """Loop del thread worker: procesa la cola de mensajes con rate limiting."""
        while True:
            try:
                msg: _Message = self._queue.get(timeout=5)
            except queue.Empty:
                continue

            # Rate limiting
            elapsed = time.time() - self._last_sent_time
            if elapsed < self.MIN_INTERVAL_S:
                time.sleep(self.MIN_INTERVAL_S - elapsed)

            success = self._send_now(msg.text, thread_id=msg.thread_id)
            if success:
                self._last_sent_time = time.time()
            else:
                # Reintentar con backoff
                if msg.retry_count < msg.max_retries:
                    msg.retry_count += 1
                    delay = self.RETRY_DELAYS[min(msg.retry_count - 1, len(self.RETRY_DELAYS) - 1)]
                    time.sleep(delay)
                    try:
                        self._queue.put_nowait(msg)
                    except queue.Full:
                        pass

    def _send_now(self, text: str, thread_id: Optional[int] = None) -> bool:
        """
        Envío HTTP sincrónico al API de Telegram.
        Si thread_id no es None, envía el mensaje a ese Topic del grupo.

        Usa urllib.request en lugar de aiohttp/aiohttp intencionalmente
        porque este método se ejecuta desde un thread daemon separado,
        no desde el event loop de asyncio. Usar aiohttp aquí requeriría
        crear loops adicionales y complicaría el manejo de recursos.

        Retorna True si tuvo éxito.
        """
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        try:
            import urllib.request
            import json as _json
            data = _json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            log.warning("Error enviando mensaje Telegram", error=str(e))
            return False


# ── Instancia global (singleton perezoso) ────────────────────────────────────
_global_notifier: Optional[TelegramNotifier] = None
_notifier_lock = threading.Lock()


def get_notifier() -> TelegramNotifier:
    """Retorna el notificador global (se crea en la primera llamada)."""
    global _global_notifier
    with _notifier_lock:
        if _global_notifier is None:
            _global_notifier = TelegramNotifier()
    return _global_notifier


# ── CLI para pruebas ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Prueba de notificaciones Telegram")
    parser.add_argument("--test", action="store_true", help="Enviar mensaje de prueba")
    args = parser.parse_args()

    if args.test:
        n = TelegramNotifier()
        ok = n.send_test()
        print(f"{'✅ Mensaje enviado correctamente.' if ok else '❌ Error al enviar.'}")
