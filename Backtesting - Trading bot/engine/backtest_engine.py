"""
engine/backtest_engine.py — BacktestEngine Genérico
══════════════════════════════════════════════════════
Encapsula el loop principal de simulación, la evaluación de riesgo,
la ejecución de órdenes y el cálculo final de métricas.

Soporta List[Signal] con SignalType (OPEN_LONG, CLOSE_SHORT, etc.).
NO importa config_local — recibe toda la configuración por constructor.
NO tiene código asíncrono (es solo para backtest síncrono).

TEMPORALIDADES:
  - primary_timeframe:   Temporalidad principal (ej: "1h"). Las velas que ve la estrategia.
  - secondary_timeframe: Temporalidad secundaria (ej: "5m"). Sub-velas para ordenar
    ejecución cuando hay múltiples señales en una misma vela primaria.
    Si es None o vacío, no se usa resolución secundaria.
"""
from __future__ import annotations

import sys
import time
from typing import Callable, List, Optional

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from actors.clock        import Clock
from actors.order_book   import OrderBook, OrderSide
from actors.price_feed   import PriceFeed
from actors.wallet       import Wallet, TradeRecord
from risk.risk_manager   import RiskManager
from state.state_manager import StateManager, Checkpoint
from strategies.base_strategy import BaseStrategy
from support.logger      import get_logger
from support.types       import Candle, Signal, SignalType, PositionDirection

log = get_logger("backtest_engine")


class BacktestEngine:
    """
    Motor genérico para ejecutar una estrategia sobre un conjunto de actores.

    NO importa config_local. Toda la configuración se recibe por constructor.
    NO tiene código asíncrono (run_async se implementará en un LiveEngine separado).
    """

    def __init__(
        self,
        clock:  Clock,
        wallet: Wallet,
        ob:     OrderBook,
        risk:   RiskManager,
        state:  StateManager,
        feed:   Optional[PriceFeed] = None,
        *,
        usd_initial: float,
        fecha_inicio: str,
        fecha_fin: str,
        commission_pct: float,
        results_json: str,
        max_posiciones: int,
        primary_timeframe: str = "1h",
        secondary_timeframe: Optional[str] = None,
        modo_operacion: str = "limite_gtc",
        on_trade: Optional[Callable[[Wallet, BaseStrategy, Candle], None]] = None,
    ) -> None:
        if feed is None:
            raise TypeError("BacktestEngine.__init__() missing required positional argument: 'feed'")
        self.clock               = clock
        self.wallet              = wallet
        self.ob                  = ob
        self.risk                = risk
        self.state               = state
        self._feed               = feed
        self.primary_timeframe   = primary_timeframe
        self.secondary_timeframe = secondary_timeframe
        self._modo_operacion     = modo_operacion
        self.on_trade            = on_trade

        self._usd_initial   = usd_initial
        self._fecha_inicio   = fecha_inicio
        self._fecha_fin      = fecha_fin
        self._commission_pct = commission_pct
        self._results_json   = results_json
        self._max_pos        = max_posiciones

        self._cached_candles: Optional[List[Candle]] = None
        self._warmup_closes: List[float] = []

        # Contadores internos
        self._n_long_opens = 0
        self._n_long_adds = 0
        self._n_long_reduces = 0
        self._n_long_closes = 0
        self._n_short_opens = 0
        self._n_short_adds = 0
        self._n_short_reduces = 0
        self._n_short_closes = 0
        self._n_ignorados = 0
        self._n_ambiguous_fills = 0
        self._ign_motivos: dict[str, int] = {}
        self._realized_pnl_total = 0.0

    @property
    def _n_compras(self) -> int:
        """Compras totales (long opens/adds + short reduces/closes)."""
        return self._n_long_opens + self._n_long_adds + self._n_short_reduces + self._n_short_closes

    @property
    def _n_ventas(self) -> int:
        """Ventas totales (long reduces/closes + short opens/adds)."""
        return self._n_long_reduces + self._n_long_closes + self._n_short_opens + self._n_short_adds

    # ══════════════════════════════════════════════════════════════════════
    # WARM-UP
    # ══════════════════════════════════════════════════════════════════════

    def _load_warmup_candles(self, start: str, symbol: str, n_candles: int) -> List[Candle]:
        from support.time_utils import to_epoch_s
        try:
            start_s = to_epoch_s(start)
            # Calcular segundos por vela según primary_timeframe
            seconds_per_candle = self._timeframe_to_seconds(self.primary_timeframe)
            margin_s = start_s - (n_candles + 50) * seconds_per_candle
            candles = self._feed.get_candles(margin_s, start_s - 1, symbol)
            return candles[-n_candles:] if len(candles) > n_candles else candles
        except Exception:
            return []

    @staticmethod
    def _timeframe_to_seconds(tf: str) -> int:
        """Convierte "1h", "15m", "5m", "1m" a segundos."""
        tf = tf.strip().lower()
        if tf.endswith("h"):
            return int(tf[:-1]) * 3600
        elif tf.endswith("m"):
            return int(tf[:-1]) * 60
        elif tf.endswith("s"):
            return int(tf[:-1])
        return 3600  # default 1h

    # ══════════════════════════════════════════════════════════════════════
    # EJECUCIÓN
    # ══════════════════════════════════════════════════════════════════════

    def run(self, strategy: BaseStrategy) -> dict:
        t_start = time.time()

        # ── Warm-up ───────────────────────────────────────────────────────
        # Calcular warm-up basado en RSI period si existe
        rsi_period = getattr(strategy, '_rsi_period', 14)
        n_warm = rsi_period + 20
        symbol = getattr(self.clock, '_symbol', 'BTCUSDT')
        warm_candles = self._load_warmup_candles(self._fecha_inicio, symbol, n_warm)
        if len(warm_candles) < n_warm:
            log.warning("Warm-up insuficiente", esperadas=n_warm, obtenidas=len(warm_candles))
        if hasattr(strategy, 'load_warmup') and warm_candles:
            strategy.load_warmup(warm_candles)
            self._warmup_closes = [c.close for c in warm_candles]

        # ── Cachear velas desde el clock ─────────────────────────────────
        self._cached_candles = getattr(self.clock, '_candles', None) or getattr(self.clock, 'candles', None) or []

        strategy.on_start(self.wallet)
        last_candle: Optional[Candle] = None

        # ── Loop principal ────────────────────────────────────────────────
        for candle in self.clock:
            last_candle = candle
            signals: List[Signal] = strategy.tick(candle, self.wallet)

            self._process_signals(signals, candle, strategy)
            self.state.save(Checkpoint.from_wallet(
                self.wallet, close_price=candle.close, ts=candle.ts,
                metadata={"estrategia": strategy.name},
            ))

        # ── Métricas finales ──────────────────────────────────────────────
        strategy.on_stop(self.wallet)
        summary = self._build_summary(strategy, last_candle)

        elapsed = time.time() - t_start
        log.info("Backtest completado", estrategia=strategy.name,
                 velas=len(self._cached_candles), tiempo_s=round(elapsed, 2))

        return summary

    # ══════════════════════════════════════════════════════════════════════
    # PROCESAMIENTO DE SEÑALES
    # ══════════════════════════════════════════════════════════════════════

    def _process_signals(self, signals: List[Signal], candle: Candle, strategy: BaseStrategy) -> None:
        """Procesa una lista de señales usando SignalType para elegir el método del OrderBook."""
        actionable_signals = [s for s in signals if s.is_actionable]
        if not actionable_signals:
            return

        # ── Resolución por sub-velas intradía ─────────────────────────────
        # Si hay secondary_timeframe, intentamos obtener sub-velas para
        # ordenar cronológicamente la ejecución de múltiples señales.
        # Por ahora, procesamos todo en la vela primaria.
        self._process_signals_primary(actionable_signals, candle, strategy)

    # ── Mapeo SignalType → método del OrderBook ───────────────────────────

    def _signaltype_to_direction(self, st: SignalType) -> PositionDirection:
        """Deriva PositionDirection desde SignalType."""
        if st in (SignalType.OPEN_LONG, SignalType.ADD_LONG,
                  SignalType.REDUCE_LONG, SignalType.CLOSE_LONG):
            return PositionDirection.LONG
        if st in (SignalType.OPEN_SHORT, SignalType.ADD_SHORT,
                  SignalType.REDUCE_SHORT, SignalType.CLOSE_SHORT):
            return PositionDirection.SHORT
        return PositionDirection.NONE

    def _ejecutar_segun_signaltype(self, signal: Signal, candle: Candle,
                                    initial_candle_open: Optional[float] = None) -> tuple:
        """
        Ejecuta la señal según su SignalType llamando al método correspondiente del OrderBook.
        
        Returns:
            tuple: (order, signal_type) donde signal_type es el SignalType original
        """
        st = signal.signal_type
        direction = self._signaltype_to_direction(st)
        record_ts = signal.ts if signal.ts else candle.ts

        signal_type_str = st.value

        if st == SignalType.OPEN_LONG:
            order = self.ob.open_position(PositionDirection.LONG, signal.price, self.wallet,
                                           candle_ts=record_ts,
                                           initial_candle_open=initial_candle_open,
                                           signal_type=signal_type_str)
        elif st == SignalType.ADD_LONG:
            order = self.ob.add_position(PositionDirection.LONG, signal.price, self.wallet,
                                          candle_ts=record_ts,
                                          initial_candle_open=initial_candle_open,
                                          signal_type=signal_type_str)
        elif st == SignalType.REDUCE_LONG:
            order = self.ob.reduce_position(PositionDirection.LONG, signal.price, self.wallet,
                                             candle_ts=record_ts,
                                             initial_candle_open=initial_candle_open,
                                             signal_type=signal_type_str)
        elif st == SignalType.CLOSE_LONG:
            order = self.ob.close_position(PositionDirection.LONG, signal.price, self.wallet,
                                            candle_ts=record_ts,
                                            initial_candle_open=initial_candle_open,
                                            signal_type=signal_type_str)
        elif st == SignalType.OPEN_SHORT:
            order = self.ob.open_position(PositionDirection.SHORT, signal.price, self.wallet,
                                           candle_ts=record_ts,
                                           initial_candle_open=initial_candle_open,
                                           signal_type=signal_type_str)
        elif st == SignalType.ADD_SHORT:
            order = self.ob.add_position(PositionDirection.SHORT, signal.price, self.wallet,
                                          candle_ts=record_ts,
                                          initial_candle_open=initial_candle_open,
                                          signal_type=signal_type_str)
        elif st == SignalType.REDUCE_SHORT:
            order = self.ob.reduce_position(PositionDirection.SHORT, signal.price, self.wallet,
                                             candle_ts=record_ts,
                                             initial_candle_open=initial_candle_open,
                                             signal_type=signal_type_str)
        elif st == SignalType.CLOSE_SHORT:
            order = self.ob.close_position(PositionDirection.SHORT, signal.price, self.wallet,
                                            candle_ts=record_ts,
                                            initial_candle_open=initial_candle_open,
                                            signal_type=signal_type_str)
        else:
            raise ValueError(f"SignalType desconocido: {st}")

        return order, st

    def _contabilizar_operacion(self, st: SignalType) -> None:
        """Incrementa el contador correspondiente al tipo de operación."""
        if st == SignalType.OPEN_LONG:
            self._n_long_opens += 1
        elif st == SignalType.ADD_LONG:
            self._n_long_adds += 1
        elif st == SignalType.REDUCE_LONG:
            self._n_long_reduces += 1
        elif st == SignalType.CLOSE_LONG:
            self._n_long_closes += 1
        elif st == SignalType.OPEN_SHORT:
            self._n_short_opens += 1
        elif st == SignalType.ADD_SHORT:
            self._n_short_adds += 1
        elif st == SignalType.REDUCE_SHORT:
            self._n_short_reduces += 1
        elif st == SignalType.CLOSE_SHORT:
            self._n_short_closes += 1

    def _es_senal_buy(self, st: SignalType) -> bool:
        """Retorna True si la señal implica comprar BTC (entrar long o salir short)."""
        return st in (SignalType.OPEN_LONG, SignalType.ADD_LONG,
                      SignalType.REDUCE_SHORT, SignalType.CLOSE_SHORT)

    def _es_senal_sell(self, st: SignalType) -> bool:
        """Retorna True si la señal implica vender BTC (entrar short o salir long)."""
        return st in (SignalType.OPEN_SHORT, SignalType.ADD_SHORT,
                      SignalType.REDUCE_LONG, SignalType.CLOSE_LONG)

    # ── Procesamiento de señales en vela primaria ─────────────────────────

    def _process_signals_primary(self, actionable_signals: List[Signal],
                                  candle: Candle,
                                  strategy: BaseStrategy) -> None:
        # Check de ambigüedad
        buys_trigger = [s for s in actionable_signals if self._es_senal_buy(s.signal_type) and candle.low <= s.price]
        sells_trigger = [s for s in actionable_signals if self._es_senal_sell(s.signal_type) and candle.high >= s.price]
        if buys_trigger and sells_trigger:
            self._n_ambiguous_fills += 1
            if hasattr(self.ob, 'ambiguous_fills_count'):
                self.ob.ambiguous_fills_count += 1

        if hasattr(self.ob, 'set_candle'):
            self.ob.set_candle(candle)

        for signal in actionable_signals:
            st = signal.signal_type
            if st == SignalType.HOLD:
                continue

            tentative_side = OrderSide.BUY if self._es_senal_buy(st) else OrderSide.SELL

            # Risk check
            risk_reason = self.risk.check(tentative_side, signal.price, self.wallet, candle)
            if risk_reason:
                self._n_ignorados += 1
                self._ign_motivos[risk_reason] = self._ign_motivos.get(risk_reason, 0) + 1
                self.wallet.update(TradeRecord(
                    ts=signal.ts or candle.ts, side=tentative_side.value, price=signal.price,
                    ignored=True, ignore_reason=risk_reason,
                    signal_type=st.value,
                ))
                self.risk.on_signal_rejected()
                continue

            # Ejecutar según SignalType
            order, st = self._ejecutar_segun_signaltype(
                signal, candle, initial_candle_open=candle.open
            )

            # Propagar signal_type al TradeRecord ANTES de wallet.update()
            if order.trade:
                order.trade.signal_type = st.value

            if order.is_filled:
                self.risk.on_trade_executed()
                self._contabilizar_operacion(st)
            else:
                self._n_ignorados += 1
                motivo = order.reject_reason or "desconocido"
                self._ign_motivos[motivo] = self._ign_motivos.get(motivo, 0) + 1

            self.risk.update_peak(self.wallet.portfolio_value(candle.close))

            if self.on_trade:
                self.on_trade(self.wallet, strategy, candle)

    # ══════════════════════════════════════════════════════════════════════
    # MÉTRICAS
    # ══════════════════════════════════════════════════════════════════════

    def _calc_sharpe_maxdd(self, strategy, last_candle) -> tuple:
        import numpy as np
        try:
            history = self.state.history() if hasattr(self.state, 'history') else []
            if len(history) >= 2:
                port_arr = np.array(
                    [ckpt.portfolio_value for ckpt in history],
                    dtype=np.float64,
                )
            else:
                return 0.0, 0.0

            peak   = np.maximum.accumulate(port_arr)
            dd     = (port_arr - peak) / np.where(peak == 0, 1, peak) * 100
            max_dd = float(dd.min())

            returns = np.diff(port_arr) / np.where(port_arr[:-1] == 0, 1, port_arr[:-1])
            if len(returns) < 2 or np.std(returns) == 0:
                sharpe = 0.0
            else:
                # Factor de anualización dinámico según primary_timeframe
                tf_seconds = self._timeframe_to_seconds(self.primary_timeframe)
                ann_factor = np.sqrt(365 * 24 * 3600 / tf_seconds)
                sharpe = float(np.mean(returns) / np.std(returns) * ann_factor)

            return round(sharpe, 4), round(max_dd, 4)
        except Exception:
            return 0.0, 0.0

    def _build_summary(self, strategy: BaseStrategy, last_candle: Optional[Candle]) -> dict:
        chart_data_extra = {}

        if hasattr(self.ob, 'gtc_stats'):
            try:
                chart_data_extra["gtc_stats"] = self.ob.gtc_stats
            except Exception as e:
                log.warning("No se pudieron obtener estadísticas GTC", error=str(e))

        if last_candle is None:
            print("✗ No se encontraron velas en el rango indicado.")
            return {}

        precio_final  = last_candle.close
        port_final    = self.wallet.portfolio_value(precio_final)
        pnl_pct       = (port_final / self._usd_initial - 1) * 100

        all_candles = self._cached_candles if self._cached_candles else []
        precio_inicial = all_candles[0].open if all_candles else last_candle.close
        bh_pnl         = (precio_final / precio_inicial - 1) * 100
        atl = min(c.low  for c in all_candles) if all_candles else 0
        ath = max(c.high for c in all_candles) if all_candles else 0

        # Overlays definidos por la estrategia (líneas/bandas/indicadores).
        # chart_data.overlays guarda SOLO la metadata (id, title, color);
        # los valores por vela se fusionan inline en cada candle usando el id.
        chart_overlay_config = []
        if hasattr(strategy, "get_chart_overlay_config"):
            try:
                chart_overlay_config = strategy.get_chart_overlay_config() or []
            except Exception:
                chart_overlay_config = []

        candles_json = []
        for c in all_candles:
            candle_obj = {
                "ts": c.ts, "open": c.open, "high": c.high,
                "low": c.low, "close": c.close,
                "volume": getattr(c, 'volume', 0)
            }
            # Fusionar valores de overlays inline (id -> valor) si la
            # estrategia los provee para esta vela.
            if hasattr(strategy, "get_chart_overlay_row"):
                try:
                    row = strategy.get_chart_overlay_row(c.ts) or {}
                    candle_obj.update(row)
                except Exception:
                    pass
            candles_json.append(candle_obj)

        chart_data = {
            "candles": candles_json,
            # Closes de las velas de warmup usadas por load_warmup.
            # Permite al test independiente replicar el RSI EXACTO de la
            # estrategia en las primeras velas del rango.
            "warmup_closes": self._warmup_closes,
            "overlays": chart_overlay_config,
            **chart_data_extra
        }

        sharpe, max_dd = self._calc_sharpe_maxdd(strategy, last_candle)

        total_trades = self._n_compras + self._n_ventas

        summary = {
            "estrategia":               strategy.name,
            "fecha_inicio":             self._fecha_inicio,
            "fecha_fin":                self._fecha_fin,
            "saldo_inicial_usd":       self._usd_initial,
            "usd_balance_final":             round(self.wallet.get_usd_balance(), 8),
            "usd_free_final":                round(self.wallet.get_usd_free(), 8),
            "usd_short_collateral_final":    round(self.wallet.get_usd_short_collateral(), 8),
            "btc_balance_final":        round(self.wallet.get_btc_balance(), 10),
            "btc_acumulado_total":      round(self.wallet.get_btc_acumulado(), 10),
            "btc_en_posiciones_final":  round(self.wallet.btc_en_posiciones(), 10),
            "precio_promedio_final":    round(self.wallet.precio_promedio_posiciones(), 8),
            "current_direction":        self.wallet.current_direction.value,
            # Portfolio
            "portfolio_value_final":    round(port_final, 4),
            "pnl_pct":                  round(pnl_pct, 4),
            "sharpe":                   sharpe,
            "max_drawdown_pct":         max_dd,
            "buy_hold_pnl_pct":         round(bh_pnl, 4),
            "alpha_vs_bh":              round(pnl_pct - bh_pnl, 4),
            # Trades detallados
            "total_trades_ejecutados":  total_trades,
            "total_compras":            self._n_compras,
            "total_ventas":             self._n_ventas,
            "long_opens":               self._n_long_opens,
            "long_adds":                self._n_long_adds,
            "long_reduces":             self._n_long_reduces,
            "long_closes":              self._n_long_closes,
            "short_opens":              self._n_short_opens,
            "short_adds":               self._n_short_adds,
            "short_reduces":            self._n_short_reduces,
            "short_closes":             self._n_short_closes,
            "total_ignorados":          self._n_ignorados,
            "ordenes_canceladas":       0,
            "ignorados_por_motivo":     self._ign_motivos,
            "positions_count_final":    self.wallet.positions_count,
            "primary_timeframe":        self.primary_timeframe,
            "secondary_timeframe":      self.secondary_timeframe or "none",
            "ambiguous_fills_count":    self._n_ambiguous_fills,
            "ambiguity_pct":            round((self._n_ambiguous_fills / max(1, total_trades)) * 100, 2),
            "parametros": {
                **strategy.describe(),
                "param_display_map": {
                    **strategy.get_param_display_map(),
                    "max_posiciones":    "Max Posiciones",
                    "commission_pct":    "Comisión",
                    "slot_usd_final":   "Slot USD Final",
                    "primary_timeframe":   "Timeframe Principal",
                    "secondary_timeframe": "Timeframe Secundario",
                    "modo_operacion":      "Modo Operación",
                },
                "max_posiciones":    self._max_pos,
                "commission_pct":    self._commission_pct,
                "slot_usd_final":   round(self.wallet.get_slot_usd(), 4),
                "primary_timeframe":   self.primary_timeframe,
                "secondary_timeframe": self.secondary_timeframe or "none",
                "modo_operacion":      self._modo_operacion,
            },
            "chart_data": chart_data,
        }

        if hasattr(self.wallet, 'flush'):
            self.wallet.flush(summary, root_extra=chart_data_extra or None)

        return summary

    # ══════════════════════════════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════════════════════════════

    def print_config(self, strategy_name: str) -> None:
        print("╔" + "═" * 70 + "╗")
        print(f"│   BACKTEST — {strategy_name:^53} │")
        print("╚" + "═" * 70 + "╝")
        print(f"  Rango         : {self._fecha_inicio} → {self._fecha_fin}")
        print(f"  Capital       : ${self._usd_initial:,.2f} USDT")
        print(f"  Max posiciones: {self._max_pos}")
        print(f"  Comisión      : {self._commission_pct}%")
        print(f"  Primaria      : {self.primary_timeframe.upper()}")
        sec = self.secondary_timeframe
        print(f"  Secundaria    : {sec.upper() if sec else 'N/A (sin resolución)'}")
        print(f"  Output JSON   : {self._results_json}")
        print("─" * 72)

    def print_summary(self, summary: dict) -> None:
        if not summary:
            return
        port_final = summary.get("portfolio_value_final", 0)
        pnl_pct = summary.get("pnl_pct", 0)
        total_trades = self._n_compras + self._n_ventas
        ambig_cnt = summary.get("ambiguous_fills_count", 0)
        ambig_pct = summary.get("ambiguity_pct", 0.0)

        sep  = "═" * 60
        sign = "+" if pnl_pct >= 0 else ""
        print(f"\n{sep}")
        print("  RESUMEN DE BACKTEST")
        print(sep)
        print(f"  Primaria        : {self.primary_timeframe.upper()}")
        sec = self.secondary_timeframe
        print(f"  Secundaria      : {sec.upper() if sec else 'N/A'}")
        print(f"  Portfolio final  : ${port_final:>12,.2f} USDT")
        btc_display = self.wallet.btc_en_posiciones()
        btc_label = "BTC en posic."
        if self.wallet.current_direction == PositionDirection.SHORT:
            btc_display = -btc_display
            btc_label = "BTC deuda"
        usd_col = self.wallet.get_usd_short_collateral()
        print(f"  └─ USDT libre    : ${self.wallet.get_usd_free():>12,.2f}")
        if usd_col > 0:
            print(f"  └─ USDT garantías: ${usd_col:>12,.2f}")
        print(f"  └─ {btc_label:<14} :  {btc_display:.8f} BTC")
        print(f"  └─ Dirección     :  {self.wallet.current_direction.value}")
        print(f"  PnL              : {sign}{pnl_pct:.2f}%")
        print(f"  Buy & Hold ref   : {summary.get('buy_hold_pnl_pct', 0):+.2f}%")
        print(f"  Alpha vs B&H     : {summary.get('alpha_vs_bh', 0):+.2f}%")
        print(f"  ── Operaciones ──")
        print(f"  Long Opens       : {self._n_long_opens:,}")
        print(f"  Long Adds        : {self._n_long_adds:,}")
        print(f"  Long Reduces     : {self._n_long_reduces:,}")
        print(f"  Long Closes      : {self._n_long_closes:,}")
        print(f"  Short Opens      : {self._n_short_opens:,}")
        print(f"  Short Adds       : {self._n_short_adds:,}")
        print(f"  Short Reduces    : {self._n_short_reduces:,}")
        print(f"  Short Closes     : {self._n_short_closes:,}")
        print(f"  Ignorados        : {self._n_ignorados:,}  → {self._ign_motivos}")
        print(f"  Posiciones abier.: {self.wallet.positions_count}")
        print("─" * 60)
        print(f"  Ambigüedad Ejec. : {ambig_cnt:,} conflictos en sub-velas ({ambig_pct:.2f}% de los trades)")
        if ambig_cnt == 0:
            print("  └─ Fiabilidad    : 100% Determinista")
        elif ambig_pct < 5.0:
            print(f"  └─ Fiabilidad    : ALTA (Solo {ambig_pct:.1f}% de solapamientos)")
        else:
            print(f"  └─ Fiabilidad    : MEDIA/BAJA ({ambig_cnt} solapamientos)")
        print(sep)
        print(f"\n✓ Resultado guardado en: {self._results_json}")