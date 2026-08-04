"""
risk/risk_manager.py — Gestor de riesgo mejorado
═══════════════════════════════════════════════════
Responsabilidad única: decidir si una señal puede ejecutarse según las reglas de riesgo.

MEJORAS sobre la versión anterior:
  - Max drawdown (original, siempre activo)
  - Límite de trades diarios       (RISK_MAX_TRADES_PER_DAY, opcional)
  - Cooldown entre trades           (RISK_COOLDOWN_SECONDS, opcional)
  - Circuit breaker                 (RISK_CIRCUIT_BREAKER_N, opcional)

REFACTOR ASYNC:
  - Se agregó check_async() como corrutina para el motor asíncrono (LiveEngine).
  - check() original se mantiene síncrono para BacktestEngine (sin cambios de API).
  - get_state() / restore_state() permiten persistencia del Circuit Breaker
    entre reinicios del proceso a través del JSONStateManager.
  - Modo "Solo Gestión": cuando el Circuit Breaker está activo, se bloquean
    las órdenes de apertura pero se permiten las órdenes de cierre para
    gestionar posiciones ya abiertas.

Los controles nuevos son opcionales y se activan vía .env o parámetros explícitos.
El backtest NO los activa por defecto (para no cambiar resultados históricos).

Configuración en .env (solo para modo live):
  RISK_MAX_DRAWDOWN_PCT=25.0
  RISK_MAX_TRADES_PER_DAY=0      # 0 = sin límite
  RISK_COOLDOWN_SECONDS=0        # 0 = sin cooldown
  RISK_CIRCUIT_BREAKER_N=0       # 0 = sin circuit breaker
"""

from __future__ import annotations

import time as _time  # usar _time.monotonic() para intervalos, _time.time() para timestamps absolutos
from typing import Optional

from actors.order_book import OrderSide
from actors.wallet     import Wallet
from support.types     import Candle
from support.logger    import get_logger

log = get_logger("risk_manager")


class RiskManager:
    """
    Evaluador de riesgo multicapa.

    check() retorna None si la operación puede ejecutarse,
    o un string describiendo el motivo de rechazo.
    """

    def __init__(
        self,
        usd_initial: float,
        max_drawdown_pct: float = 100.0,
        max_trades_per_day: int = 0,
        cooldown_seconds: int = 0,
        circuit_breaker_n: int = 0,
        stop_loss_pct: float = 0.0,
    ) -> None:
        # ── Control original: max drawdown ────────────────────────────────────
        self._peak = usd_initial
        self._max_drawdown_pct = max_drawdown_pct
        self._stop_loss_pct = stop_loss_pct

        # ── Límite de trades diarios ──────────────────────────────────────────
        self._max_trades_per_day = max_trades_per_day
        self._trades_today: int = 0
        self._today_date: str = self._current_date()

        # ── Cooldown entre trades (usa time.monotonic para evitar saltos NTP) ─
        self._cooldown_seconds = cooldown_seconds
        self._last_trade_time: float = 0.0

        # ── Circuit breaker ───────────────────────────────────────────────────
        # Pausa operaciones si se rechazan N señales consecutivas por riesgo
        self._circuit_breaker_n = circuit_breaker_n
        self._consecutive_rejections: int = 0
        self._circuit_open: bool = False
        self._circuit_open_until: float = 0.0
        self._circuit_break_cooldown: float = 300.0  # 5 minutos pausa

    def check(self, side: OrderSide, price: float,
              wallet: Wallet, candle: Candle) -> Optional[str]:
        """
        [SÍNCRONO] Verifica si la operación está dentro de los límites de riesgo.
        Usado por BacktestEngine.run() (bucle síncrono).
        Retorna None = OK, str = motivo de rechazo.
        """
        return self._evaluate(side, price, wallet, candle)

    async def check_async(self, side: OrderSide, price: float,
                          wallet: Wallet, candle: Candle) -> Optional[str]:
        """
        [ASÍNCRONO] Verifica si la operación está dentro de los límites de riesgo.
        Usado por BacktestEngine.run_async() y LiveTrader (bucle asíncrono).
        Internamente no hace I/O (todo es en memoria), pero se provee como
        corrutina para uniformidad con la interfaz async del engine.
        Retorna None = OK, str = motivo de rechazo.
        """
        return self._evaluate(side, price, wallet, candle)

    def _evaluate(self, side: OrderSide, price: float,
                  wallet: Wallet, candle: Candle) -> Optional[str]:
        """Lógica compartida de evaluación de riesgo (síncrona pura, sin I/O)."""
        # 1. Max drawdown (activo si > 0)
        if self._max_drawdown_pct > 0:
            port = wallet.portfolio_value(candle.close)
            dd_pct = (self._peak - port) / self._peak * 100 if self._peak > 0 else 0
            if dd_pct > self._max_drawdown_pct:
                return f"drawdown_max({dd_pct:.1f}%>{self._max_drawdown_pct}%)"

        # 2. Circuit breaker (si está configurado)
        #    Modo "Solo Gestión": sólo bloquea COMPRAS (apertura de posición).
        #    Las VENTAS (cierre de posición) siempre pasan si hay algo abierto.
        if self._circuit_breaker_n > 0 and self._circuit_open:
            remaining = self._circuit_open_until - _time.time()
            if remaining > 0:
                if side == OrderSide.BUY:  # Solo bloquear aperturas
                    return f"circuit_breaker_abierto({remaining:.0f}s restantes) [Solo Gestión]"
                else:
                    log.info("Circuit Breaker activo pero permitiendo cierre de posición [Solo Gestión]")
            else:
                # Reanudar operaciones
                self._circuit_open = False
                self._consecutive_rejections = 0
                log.info("Circuit breaker cerrado: reanudando operaciones")

        # 3. Límite de trades diarios (si está configurado)
        if self._max_trades_per_day > 0:
            today = self._current_date()
            if today != self._today_date:
                # Nuevo día: resetear contador
                self._trades_today = 0
                self._today_date = today
            if self._trades_today >= self._max_trades_per_day:
                return f"limite_diario({self._trades_today}>={self._max_trades_per_day})"

        # 4. Cooldown entre trades (usa time.monotonic para evitar saltos NTP)
        if self._cooldown_seconds > 0 and self._last_trade_time > 0:
            elapsed = _time.monotonic() - self._last_trade_time
            if elapsed < self._cooldown_seconds:
                remaining = self._cooldown_seconds - elapsed
                return f"cooldown({remaining:.0f}s restantes)"

        return None  # OK

    def on_trade_executed(self) -> None:
        """Notifica al RiskManager que se ejecutó un trade."""
        self._trades_today += 1
        self._last_trade_time = _time.monotonic()
        self._consecutive_rejections = 0  # resetear circuit breaker

    def on_signal_rejected(self) -> None:
        """
        Notifica que una señal fue rechazada por riesgo.
        Incrementa el contador del circuit breaker.
        """
        if self._circuit_breaker_n <= 0:
            return
        self._consecutive_rejections += 1
        if self._consecutive_rejections >= self._circuit_breaker_n:
            self._circuit_open = True
            self._circuit_open_until = _time.time() + self._circuit_break_cooldown
            log.warning(
                "Circuit breaker ABIERTO",
                rejections=self._consecutive_rejections,
                cooldown_s=self._circuit_break_cooldown,
            )

    def update_peak(self, port_value: float) -> None:
        if port_value > self._peak:
            self._peak = port_value

    @property
    def peak(self) -> float:
        """Valor pico del portfolio (acceso público)."""
        return self._peak

    def check_stop_loss(self, wallet: Wallet, current_price: float) -> Optional[str]:
        """
        Verifica si ALGUNA posición supera el límite de stop loss individual.
        Itera sobre TODAS las posiciones (O(n) con n = número de posiciones).
        Si alguna tiene pérdida >= stop_loss_pct, retorna el motivo
        (SELL de emergencia). None si no aplica.
        """
        if self._stop_loss_pct <= 0:
            return None

        positions = wallet.get_positions()
        if not positions:
            return None

        for pos in positions:
            if pos.total_btc <= 0 or pos.avg_entry_price <= 0:
                continue
            drawdown = (pos.avg_entry_price - current_price) / pos.avg_entry_price * 100
            if drawdown >= self._stop_loss_pct:
                return (
                    f"stop_loss_individual({drawdown:.2f}% >= {self._stop_loss_pct}%)"
                )

        return None

    # ── Persistencia de Estado (Circuit Breaker Survive Restart) ──────────────

    def get_state(self) -> dict:
        """
        Serializa el estado actual del RiskManager para guardarlo en el
        Checkpoint del JSONStateManager. Permite sobrevivir reinicios del proceso.
        """
        return {
            "trades_today": self._trades_today,
            "today_date": self._today_date,
            "consecutive_rejections": self._consecutive_rejections,
            "circuit_open": self._circuit_open,
            "circuit_open_until": self._circuit_open_until,
            "peak_portfolio_value": self._peak,
            "last_trade_time": self._last_trade_time,
            "stop_loss_pct": self._stop_loss_pct,
        }

    def restore_state(self, state: dict) -> None:
        """
        Restaura el estado del RiskManager desde un Checkpoint guardado.
        Llamar durante el arranque del bot en modo live/testnet.

        Si el circuit_open_until ya expiró (tiempo pasado), lo cierra
        automáticamente para no mantener un bloqueo obsoleto.
        """
        self._trades_today          = state.get("trades_today", 0)
        self._today_date            = state.get("today_date", self._current_date())
        self._consecutive_rejections = state.get("consecutive_rejections", 0)
        self._circuit_open          = state.get("circuit_open", False)
        self._circuit_open_until    = state.get("circuit_open_until", 0.0)
        self._peak                  = state.get("peak_portfolio_value", self._peak)
        self._last_trade_time       = state.get("last_trade_time", 0.0)
        self._stop_loss_pct         = state.get("stop_loss_pct", self._stop_loss_pct)

        # Verificar si el circuit breaker ya debió haber expirado (comparar con time.time absoluto)
        if self._circuit_open and self._circuit_open_until <= _time.time():
            self._circuit_open = False
            self._consecutive_rejections = 0
            log.info("Circuit breaker restaurado pero ya expiró: cerrándolo automáticamente")
        elif self._circuit_open:
            remaining = self._circuit_open_until - _time.time()
            log.warning(
                "Circuit breaker RESTAURADO desde checkpoint - sigue ABIERTO [Modo Solo Gestión]",
                restantes_s=f"{remaining:.0f}",
            )

    @staticmethod
    def _current_date() -> str:
        from datetime import date
        return date.today().isoformat()


def build_risk_manager(
    usd_initial: float,
    max_drawdown_pct: float = 100.0,
    enable_live_controls: bool = False,
) -> RiskManager:
    """
    Construye un RiskManager.

    enable_live_controls=True activa los controles adicionales (para modo live).
    En backtest, enable_live_controls=False para no afectar resultados históricos.
    """
    if not enable_live_controls:
        return RiskManager(
            usd_initial=usd_initial,
            max_drawdown_pct=max_drawdown_pct,
        )

    # Modo live: leer controles adicionales del .env
    from support.secrets import secrets

    try:
        max_dd   = float(secrets("RISK_MAX_DRAWDOWN_PCT", str(max_drawdown_pct)))
        max_tpd  = int(secrets("RISK_MAX_TRADES_PER_DAY", "0"))
        cooldown = int(secrets("RISK_COOLDOWN_SECONDS", "0"))
        cb_n     = int(secrets("RISK_CIRCUIT_BREAKER_N", "0"))
        stop_loss_pct = float(secrets("RISK_STOP_LOSS_PCT", "0.0"))
    except Exception:
        max_dd   = max_drawdown_pct
        max_tpd  = 0
        cooldown = 0
        cb_n     = 0
        stop_loss_pct = 0.0

    log.info(
        "RiskManager live configurado",
        max_dd=max_dd, max_tpd=max_tpd,
        cooldown=cooldown, circuit_breaker=cb_n,
        stop_loss_pct=stop_loss_pct,
    )

    return RiskManager(
        usd_initial=usd_initial,
        max_drawdown_pct=max_dd,
        max_trades_per_day=max_tpd,
        cooldown_seconds=cooldown,
        circuit_breaker_n=cb_n,
        stop_loss_pct=stop_loss_pct,
    )
