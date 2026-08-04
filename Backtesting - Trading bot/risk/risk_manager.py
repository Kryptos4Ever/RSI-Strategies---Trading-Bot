"""
risk/risk_manager.py — Gestor de riesgo para backtest
═══════════════════════════════════════════════════════
Responsabilidad única: decidir si una señal puede ejecutarse según las reglas de riesgo.

Solo incluye controles relevantes para backtest:
  - Max drawdown (detener operaciones si la pérdida supera un límite)

NO incluye controles de modo live (cooldown, circuit breaker, trades diarios)
que no tienen sentido en tiempo simulado.
"""
from __future__ import annotations

from typing import Optional

from actors.order_book import OrderSide
from actors.wallet     import Wallet
from support.logger    import get_logger
from support.types     import Candle

log = get_logger("risk_manager")


class RiskManager:
    """
    Evaluador de riesgo para backtest.

    check() retorna None si la operación puede ejecutarse,
    o un string describiendo el motivo de rechazo.
    """

    def __init__(
        self,
        usd_initial: float,
        max_drawdown_pct: float = 100.0,
        stop_loss_pct: float = 0.0,
    ) -> None:
        self._peak = usd_initial
        self._max_drawdown_pct = max_drawdown_pct
        self._stop_loss_pct = stop_loss_pct

    def check(self, side: OrderSide, price: float,
              wallet: Wallet, candle: Candle) -> Optional[str]:
        """
        Verifica si la operación está dentro de los límites de riesgo.
        Retorna None = OK, str = motivo de rechazo.
        """
        # Max drawdown
        if self._max_drawdown_pct > 0:
            port = wallet.portfolio_value(candle.close)
            dd_pct = (self._peak - port) / self._peak * 100 if self._peak > 0 else 0
            if dd_pct > self._max_drawdown_pct:
                return f"drawdown_max({dd_pct:.1f}%>{self._max_drawdown_pct}%)"

        return None  # OK

    def on_trade_executed(self) -> None:
        """Notifica al RiskManager que se ejecutó un trade."""
        pass  # Sin controles live que necesiten reset

    def on_signal_rejected(self) -> None:
        """Notifica que una señal fue rechazada por riesgo."""
        pass  # Sin circuit breaker que incrementar

    def update_peak(self, port_value: float) -> None:
        if port_value > self._peak:
            self._peak = port_value

    def check_stop_loss(self, wallet, current_price: float) -> Optional[str]:
        """
        Verifica stop-loss individual por posición.
        Retorna None si no se activa, str con motivo si se activa.
        """
        if self._stop_loss_pct <= 0:
            return None

        positions = wallet.get_positions()
        if not positions:
            return None

        pos = positions[0]
        if pos.total_btc <= 0:
            return None

        # Determinar dirección (compatible con FakePosition sin direction)
        direction = getattr(pos, 'direction', None)
        is_long = direction is None or (hasattr(direction, 'name') and direction.name == "LONG") or direction == "LONG"

        if is_long:
            loss_pct = (pos.avg_entry_price - current_price) / pos.avg_entry_price * 100
        else:
            loss_pct = (current_price - pos.avg_entry_price) / pos.avg_entry_price * 100

        if loss_pct > self._stop_loss_pct:
            return f"stop_loss_individual({loss_pct:.1f}%>{self._stop_loss_pct}%)"

        return None

    @property
    def peak(self) -> float:
        """Valor pico del portfolio (acceso público)."""
        return self._peak


def build_risk_manager(
    usd_initial: float,
    max_drawdown_pct: float = 100.0,
) -> RiskManager:
    """Construye un RiskManager para backtest."""
    return RiskManager(
        usd_initial=usd_initial,
        max_drawdown_pct=max_drawdown_pct,
    )
