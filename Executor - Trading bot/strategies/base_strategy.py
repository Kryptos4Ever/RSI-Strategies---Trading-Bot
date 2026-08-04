"""
base_strategy.py — Interfaz abstracta de estrategia
═══════════════════════════════════════════════════
Contrato que deben cumplir todas las estrategias del sistema.

La estrategia recibe velas y decide qué señales emitir.
MODIFICADO: on_candle() retorna List[Signal] para permitir
múltiples operaciones dentro de una misma vela.

DESACOPLADO: No importa nada de actors/. Usa support.types para Candle, Signal, SignalType, SignalSide.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from support.logger    import get_logger
from support.types     import Candle, Signal, SignalType, SignalSide, HOLD, HOLD_LIST

log = get_logger("strategy")


class BaseStrategy(ABC):
    """
    Contrato base para todas las estrategias.
    Las subclases implementan on_candle() retornando List[Signal].
    """

    def __init__(self, name: str):
        self.name: str = name
        self._candles_seen: int = 0
        self.last_signals: List[Signal] = [HOLD]
        log.debug("estrategia inicializada", nombre=name)

    def on_start(self, wallet) -> None:
        """Hook opcional de inicialización. Recibe una Wallet (tipo definido en actors/)."""

    @abstractmethod
    def on_candle(self, candle: Candle, wallet) -> List[Signal]:
        """
        Procesa una vela y retorna las señales correspondientes.
        Puede retornar múltiples señales (ej: compras en varias SMAs).
        Si no hay señales, retorna [HOLD].
        """

    def on_stop(self, wallet) -> None:
        """Hook opcional de finalización."""

    @property
    def candles_seen(self) -> int:
        return self._candles_seen

    def tick(self, candle: Candle, wallet) -> List[Signal]:
        """
        Wrapper público llamado por el BacktestEngine.
        Retorna siempre una lista de señales.
        """
        self._candles_seen += 1
        self.last_signals = self.on_candle(candle, wallet)
        if not self.last_signals:
            self.last_signals = [HOLD]
        return self.last_signals

    def describe(self) -> dict:
        return {"estrategia": self.name}

    def get_chart_overlay_config(self) -> list:
        """
        Devuelve la metadata de overlays (líneas/bandas/indicadores) para el
        dashboard. Solo define id, título y color — NO los datos.

        Contrato genérico: cada overlay es un dict con:
            {
                "id":    str,  # identificador único (ej: "reduce_short")
                "title": str,  # etiqueta legible (ej: "Reduce Short (40)")
                "color": str,  # color CSS (ej: "#FF9100")
            }

        Los valores por vela los entrega get_chart_overlay_row(). El
        LiveEngine lo incluye en chart_data.overlays del JSON y fusiona
        los valores inline en cada vela de chart_data.candles usando el "id"
        como clave. El dashboard lo plotea dinámicamente sin conocer la
        estrategia. Las estrategias que no lo implementan devuelven [].
        """
        return []

    def get_chart_overlay_row(self, candle_ts) -> dict:
        """
        Devuelve los valores de overlays correspondientes a UNA vela, con el
        "id" de cada overlay como clave y su valor. NO incluye el ts (viene
        de la propia vela).

        Contrato genérico: { id_overlay: valor_numérico }
        Ejemplo:
            return {"oversold": 54227.40, "short_banda": 81234.12}

        El LiveEngine lo fusiona inline en la vela correspondiente de
        chart_data.candles. Las estrategias que no lo implementan devuelven {}.
        """
        return {}

    def get_param_display_map(self) -> dict:
        """
        Devuelve un mapa de nombres legibles para los parámetros de la
        estrategia (describe()). El dashboard lo usa para mostrar cada
        parámetro con su etiqueta, sin conocer la estrategia.

        Contrato genérico: dict { key_de_describe(): "Etiqueta legible" }
        Ejemplo:
            return {
                "rsi_period": "RSI Period",
                "oversold_threshold": "Oversold Threshold",
            }

        El LiveEngine lo incluye en summary.param_display_map del
        JSON de resultados. Las estrategias que no lo implementan devuelven
        {} (el dashboard usa su mapa por defecto como fallback).
        """
        return {}

    @staticmethod
    @abstractmethod
    def get_default_config() -> dict:
        """
        Retorna un diccionario con los parámetros por defecto de la estrategia.
        Ejemplo:
            return {
                "bb_period_buy": 4,
                "bb_std_mult_buy": 2.0,
                ...
            }
        """