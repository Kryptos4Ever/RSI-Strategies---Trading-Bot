"""
strategies/rsi_standard.py — Estrategia RSI Standard (Cutler's RSI)
═══════════════════════════════════════════════════════════════════
Opera LONG cuando RSI está en zona de sobreventa (RSI <= oversold=30)
y SHORT cuando RSI está en zona de sobrecompra (RSI >= overbought=70).

Lógica de zonas (basada en RSI al inicio de la vela, ZONAS FIJAS):
  Las 3 zonas se definen por el punto neutral de la media (RSI = 50),
  independientemente de los niveles de reducción configurables:
    - RSI < 50 (zona LONG)
    - RSI > 50 (zona SHORT)
    - RSI = 50 (zona neutral)

  Los niveles de precio autónomos (reduce_long / reduce_short) definen
  SOLO el precio límite de las órdenes REDUCE, no la zona:

  - RSI < 50 (zona LONG):
      * Sin posición → OPEN_LONG al precio OVERSOLD_THRESHOLD SOLO si RSI > OVERSOLD_THRESHOLD
      * Posición LONG → ADD_LONG al precio OVERSOLD_THRESHOLD SOLO si RSI > OVERSOLD_THRESHOLD
                      + REDUCE_LONG al precio price_for_rsi(REDUCE_LONG)
      * Posición SHORT → CLOSE_SHORT al precio OVERSOLD_THRESHOLD
  - RSI > 50 (zona SHORT):
      * Sin posición → OPEN_SHORT al precio OVERBOUGHT_THRESHOLD SOLO si RSI < OVERBOUGHT_THRESHOLD
      * Posición SHORT → ADD_SHORT al precio OVERBOUGHT_THRESHOLD SOLO si RSI < OVERBOUGHT_THRESHOLD
                       + REDUCE_SHORT al precio price_for_rsi(REDUCE_SHORT)
      * Posición LONG → CLOSE_LONG al precio OVERBOUGHT_THRESHOLD
  - RSI = 50 (zona neutral):
      * No emitir ninguna orden en esa vela.

Los precios se calculan con StandardRSIEngine.price_for_rsi() (inversa estándar).
"""
from __future__ import annotations

from typing import List, Optional

from indicadores.rsi_standard import StandardRSIEngine
from strategies.base_strategy import BaseStrategy
from support.logger import get_logger
from support.types import Candle, Signal, SignalType, PositionDirection, HOLD_LIST

log = get_logger("rsi_standard")


class RSIStandardStrategy(BaseStrategy):
    """
    Estrategia de Mean Reversion basada en RSI estándar (Cutler's RSI) con precios calculados.

    Las 3 zonas de operación están FIJAS en el punto neutral RSI = 50
    (LONG < 50, SHORT > 50, neutral = 50). Los niveles de reducción
    son AUTÓNOMOS y solo definen el precio límite de las órdenes
    REDUCE_LONG / REDUCE_SHORT a través de price_for_rsi().

    Parámetros:
        rsi_period:           Período del RSI (default: 14)
        oversold_threshold:   Límite inferior para abrir/aumentar LONG (default: 30)
        overbought_threshold: Límite superior para abrir/aumentar SHORT (default: 70)
        reduce_long:          Nivel RSI para precio REDUCE_LONG (default: 50)
        reduce_short:         Nivel RSI para precio REDUCE_SHORT (default: 50)
        max_positions:        Máximo de posiciones simultáneas (default: 3)
    """

    def __init__(
        self,
        rsi_period: int = 14,
        oversold_threshold: float = 30.0,
        overbought_threshold: float = 70.0,
        reduce_long: float = 50.0,
        reduce_short: float = 50.0,
        max_positions: int = 3,
        name: str = "RSI_Standard",
    ):
        super().__init__(name=name)
        self._rsi_period = rsi_period
        self._oversold = oversold_threshold
        self._overbought = overbought_threshold
        self._reduce_long = reduce_long
        self._reduce_short = reduce_short
        self._max_pos = max_positions

        self._rsi_engine = StandardRSIEngine(period=rsi_period)
        self._rsi_buffer: List[float] = []

        # IDs de velas donde ya disparamos señales (evita duplicados por vela)
        self._fired_open_long: set = set()
        self._fired_open_short: set = set()
        self._fired_close_long: set = set()
        self._fired_close_short: set = set()
        self._fired_reduce_long: set = set()
        self._fired_reduce_short: set = set()

        # Overlays para el dashboard: niveles RSI por vela (con el MISMO timing
        # y precios que usó la estrategia). No se guarda el RSI en sí, sino los
        # precios límite derivados (inversa estándar).
        self._rsi_levels: list = []
        # Índice ts -> {oversold, overbought, reduce_long, reduce_short} para
        # resolver get_chart_overlay_row(ts) en O(1).
        self._overlay_by_ts: dict = {}

    def on_start(self, wallet) -> None:
        """Hook de inicio: registrar configuración."""
        log.debug("RSI Standard iniciada",
                  period=self._rsi_period,
                  oversold=self._oversold,
                  overbought=self._overbought)

    def load_warmup(self, candles: List[Candle]) -> None:
        """
        Carga velas previas al rango para inicializar el RSI (ventana estándar).

        El BacktestEngine llama a este hook con `rsi_period + 20` velas anteriores
        a FECHA_INICIO. Sin esto, el RSI arrancaría desde cero en la primera vela
        del rango y las primeras `rsi_period + 1` velas devolverían HOLD (warmup).
        """
        for c in candles:
            self._rsi_engine.update(c.close)

    def on_candle(self, candle: Candle, wallet) -> List[Signal]:
        """
        Procesa una vela y emite señales según RSI y estado de la wallet.

        IMPORTANTE (lookahead bias): el RSI usado para decidir es el valor VIGENTE
        al inicio de la vela (calculado con los closes hasta la vela ANTERIOR).
        NO se usa `candle.close de la vela evaluada` para decidir: en ejecución live no se conoce el
        close de la vela que recién abre. El engine se actualiza con `candle.close`
        al FINAL, para que la PRÓXIMA vela tenga el RSI correcto.
        """
        # RSI al INICIO de la vela: valor ya calculado (closes hasta la vela anterior).
        rsi = self._rsi_engine.value
        if rsi is None:
            # Warmup: avanzar el RSI con el close de esta vela y no emitir señales.
            self._rsi_engine.update(candle.close)
            return list(HOLD_LIST)

        self._rsi_buffer.append(rsi)
        if len(self._rsi_buffer) > 10:
            self._rsi_buffer = self._rsi_buffer[-10:]

        signals: List[Signal] = []
        ts = candle.ts

        # ── Estado actual de la wallet ─────────────────────────────────────
        has_position = wallet.positions_count > 0
        current_dir = wallet.current_direction  # PositionDirection

        # ── Precios límite para overlays (mismos valores usados en las señales) ──
        # Se calculan UNA vez por vela con el estado del RSI ANTES de incorporar
        # el close de esta vela (mismo timing que las señales).
        price_over_long      = self._rsi_engine.price_for_rsi(self._oversold)
        price_over_short     = self._rsi_engine.price_for_rsi(self._overbought)
        price_reduce_long    = self._rsi_engine.price_for_rsi(self._reduce_long)
        price_reduce_short   = self._rsi_engine.price_for_rsi(self._reduce_short)

        # ── ZONA LONG: RSI < 50 (punto neutral de la media) ──────────────
        if rsi < 50.0:
            price_open = price_over_long
            price_exit = price_reduce_long

            # OPEN/ADD solo si RSI está ENTRE oversold y reduce_long (30 < RSI < 50)
            in_entry_zone = rsi > self._oversold

            if not has_position:
                # Sin posición → abrir LONG solo si RSI > oversold (entre 30 y 50)
                if in_entry_zone and ts not in self._fired_open_long:
                    signals.append(Signal(
                        signal_type=SignalType.OPEN_LONG,
                        price=price_open,
                        reason=f"rsi_{rsi:.1f}>{self._oversold}_open_long@{price_open:.2f}",
                        ts=ts,
                    ))
                    self._fired_open_long.add(ts)

            elif current_dir == PositionDirection.LONG:
                # Ya estamos en LONG → ADD (solo si RSI > oversold) + REDUCE (siempre)
                if in_entry_zone and ts not in self._fired_open_long:
                    signals.append(Signal(
                        signal_type=SignalType.ADD_LONG,
                        price=price_open,
                        reason=f"rsi_{rsi:.1f}>{self._oversold}_add_long@{price_open:.2f}",
                        ts=ts,
                    ))
                    self._fired_open_long.add(ts)

                if ts not in self._fired_reduce_long:
                    signals.append(Signal(
                        signal_type=SignalType.REDUCE_LONG,
                        price=price_exit,
                        reason=f"rsi_{rsi:.1f}<{self._reduce_long}_reduce_long@{price_exit:.2f}",
                        ts=ts,
                    ))
                    self._fired_reduce_long.add(ts)

            elif current_dir == PositionDirection.SHORT:
                # Estamos en SHORT y RSI < 50 → cerrar short al precio oversold
                if ts not in self._fired_close_short:
                    signals.append(Signal(
                        signal_type=SignalType.CLOSE_SHORT,
                        price=price_open,
                        reason=f"rsi_{rsi:.1f}<{self._reduce_long}_close_short@{price_open:.2f}",
                        ts=ts,
                    ))
                    self._fired_close_short.add(ts)

        # ── ZONA SHORT: RSI > 50 (punto neutral de la media) ──────────────
        elif rsi > 50.0:
            price_open = price_over_short
            price_exit = price_reduce_short

            # OPEN/ADD solo si RSI está ENTRE reduce_short y overbought (50 < RSI < 70)
            in_entry_zone = rsi < self._overbought

            if not has_position:
                # Sin posición → abrir SHORT solo si RSI < overbought (entre 50 y 70)
                if in_entry_zone and ts not in self._fired_open_short:
                    signals.append(Signal(
                        signal_type=SignalType.OPEN_SHORT,
                        price=price_open,
                        reason=f"rsi_{rsi:.1f}<{self._overbought}_open_short@{price_open:.2f}",
                        ts=ts,
                    ))
                    self._fired_open_short.add(ts)

            elif current_dir == PositionDirection.SHORT:
                # Ya estamos en SHORT → ADD (solo si RSI < overbought) + REDUCE (siempre)
                if in_entry_zone and ts not in self._fired_open_short:
                    signals.append(Signal(
                        signal_type=SignalType.ADD_SHORT,
                        price=price_open,
                        reason=f"rsi_{rsi:.1f}<{self._overbought}_add_short@{price_open:.2f}",
                        ts=ts,
                    ))
                    self._fired_open_short.add(ts)

                if ts not in self._fired_reduce_short:
                    signals.append(Signal(
                        signal_type=SignalType.REDUCE_SHORT,
                        price=price_exit,
                        reason=f"rsi_{rsi:.1f}>{self._reduce_short}_reduce_short@{price_exit:.2f}",
                        ts=ts,
                    ))
                    self._fired_reduce_short.add(ts)

            elif current_dir == PositionDirection.LONG:
                # Estamos en LONG y RSI > 50 → cerrar long al precio overbought
                if ts not in self._fired_close_long:
                    signals.append(Signal(
                        signal_type=SignalType.CLOSE_LONG,
                        price=price_open,
                        reason=f"rsi_{rsi:.1f}>{self._reduce_short}_close_long@{price_open:.2f}",
                        ts=ts,
                    ))
                    self._fired_close_long.add(ts)

        # ── ZONA NEUTRAL: RSI == 50 ───────────────────────────────────────
        else:
            # RSI exactamente en 50 → no emitir ninguna orden
            pass

        # Registrar niveles RSI de esta vela para el overlay del dashboard.
        # `rsi` es el valor VIGENTE al inicio de la vela (closes hasta la vela
        # anterior), el mismo que usó la estrategia para decidir.
        level_row = {
            "ts":           ts,
            "rsi":          rsi,
            "oversold":     price_over_long,
            "overbought":   price_over_short,
            "reduce_long":  price_reduce_long,
            "reduce_short": price_reduce_short,
        }
        self._rsi_levels.append(level_row)
        self._overlay_by_ts[ts] = level_row

        # Limpiar sets periódicamente para evitar memory leak
        if len(self._fired_open_long) > 100:
            self._fired_open_long.clear()
            self._fired_open_short.clear()
            self._fired_close_long.clear()
            self._fired_close_short.clear()
            self._fired_reduce_long.clear()
            self._fired_reduce_short.clear()

        # Actualizar el RSI con el close de ESTA vela para la PRÓXIMA.
        # (Se hace al FINAL: la decisión y price_for_rsi ya usaron el estado previo.)
        self._rsi_engine.update(candle.close)

        return signals if signals else list(HOLD_LIST)

    def on_stop(self, wallet) -> None:
        """Cierre de estrategia: log final."""
        log.debug("RSI Standard finalizada",
                  total_velas=self.candles_seen)

    def get_chart_overlay_config(self) -> list:
        """
        Metadata de overlays del dashboard (sin datos): los 4 niveles RSI.
        Los valores por vela los entrega get_chart_overlay_row().
        """
        return [
            {
                "id":    "oversold",
                "title": f"Oversold ({self._oversold:.0f})",
                "color": "#00E676",
            },
            {
                "id":    "overbought",
                "title": f"Overbought ({self._overbought:.0f})",
                "color": "#FF1744",
            },
            {
                "id":    "reduce_long",
                "title": f"Reduce Long ({self._reduce_long:.0f})",
                "color": "#FFD600",
            },
            {
                "id":    "reduce_short",
                "title": f"Reduce Short ({self._reduce_short:.0f})",
                "color": "#FF9100",
            },
        ]

    def get_chart_overlay_row(self, candle_ts) -> dict:
        """
        Valores de overlays para la vela con timestamp `candle_ts`.
        Cada id como clave y su precio calculado (inversa estándar) como valor.
        Si la vela no tiene niveles registrados (p. ej. warmup), no aporta nada.
        """
        row = self._overlay_by_ts.get(candle_ts)
        if row is None:
            return {}
        return {
            "rsi":          row["rsi"],
            "oversold":     row["oversold"],
            "overbought":   row["overbought"],
            "reduce_long":  row["reduce_long"],
            "reduce_short": row["reduce_short"],
        }

    def describe(self) -> dict:
        return {
            "estrategia": self.name,
            "rsi_period": self._rsi_period,
            "oversold_threshold": self._oversold,
            "overbought_threshold": self._overbought,
            "reduce_long": self._reduce_long,
            "reduce_short": self._reduce_short,
            "max_positions": self._max_pos,
        }

    def get_param_display_map(self) -> dict:
        """
        Nombres legibles para los parámetros de describe().
        El dashboard los usa para mostrar cada parámetro sin conocer la
        estrategia (mismo patrón genérico que get_chart_overlay_config()).
        """
        return {
            "estrategia": "Estrategia",
            "rsi_period": "RSI Period",
            "oversold_threshold": "Oversold Threshold",
            "overbought_threshold": "Overbought Threshold",
            "reduce_long": "Reduce Long",
            "reduce_short": "Reduce Short",
            "max_positions": "Max Posiciones",
        }

    @staticmethod
    def get_default_config() -> dict:
        return {
            "rsi_period": 14,
            "oversold_threshold": 30.0,
            "overbought_threshold": 70.0,
            "reduce_long": 50.0,
            "reduce_short": 50.0,
            "max_positions": 3,
        }