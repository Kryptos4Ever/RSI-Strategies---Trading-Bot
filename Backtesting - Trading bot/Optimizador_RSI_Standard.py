"""
╔══════════════════════════════════════════════════════════════════╗
║  OPTIMIZADOR RSI STANDARD  v1.0                                ║
║  Barrido automático de parámetros para encontrar la mejor      ║
║  configuración de la estrategia RSI Standard (Cutler's RSI).   ║
║                                                                ║
║  Uso:  python Optimizador_RSI_Standard.py                     ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import signal
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import config_local as CL
from actors.clock import LocalClock
from actors.order_book import (
    OrderSide,
    SimulatedLimitPostOnlyOrderBook,
    SimulatedLimitGTCOrderBook,
)
from actors.price_feed import SQLiteFeed, resolve_db_path
from actors.wallet import MemoryWallet, TradeRecord
from engine.backtest_engine import BacktestEngine
from risk.risk_manager import RiskManager, build_risk_manager
from state.state_manager import MemoryStateManager
from strategies.rsi_standard import RSIStandardStrategy
from support.logger import get_logger
from support.time_utils import to_epoch_s
from support.types import Candle, PositionDirection, Signal, SignalType, HOLD_LIST

log = get_logger("optimizador_rsi_standard")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DEL OPTIMIZADOR  (Editar aquí antes de ejecutar)
# ══════════════════════════════════════════════════════════════════════════════

# ── Paralelismo ──────────────────────────────────────────────────────────────
CORES_A_DEJAR_LIBRES = 1        # 0 = usar todos los cores disponibles
                                # 1 = dejar 1 core libre, etc.

# ── Checkpoints (separados por estrategia para evitar colisiones) ────────────
CHECKPOINT_PATH     = "optimizer_checkpoint_rsi_standard.json"
AUTO_SAVE_INTERVAL  = 50         # Guardar checkpoint cada N combinaciones

# ── Pruning (early stopping) ─────────────────────────────────────────────────
PRUNE_ENABLED       = True
PRUNE_AFTER_PCT     = 0.15       # % de velas antes de empezar a evaluar
PRUNE_THRESHOLD     = 0.90       # Abortar si portfolio < 90% del capital inicial
PRUNE_BH_RATIO      = 0.85       # Abortar si está 5% peor que Buy & Hold

# ── Filtros de resultados ────────────────────────────────────────────────────
MIN_TRADES          = 10         # Ignorar estrategias con menos operaciones
TOP_N               = 20         # Tamaño del ranking

# ── Archivos de salida (separados por estrategia) ─────────────────────────────
TOP20_JSON_PATH     = "optimizer_top20_rsi_standard.json"
TOP20_CSV_PATH      = "optimizer_top20_rsi_standard.csv"

# ── Rangos de parámetros a barrer ────────────────────────────────────────────
PARAM_RANGES = {
    "RSI_PERIOD":            [8, 9, 10, 11, 12, 13, 14, 15, 20, 25, 30],
    "OVERSOLD_THRESHOLD":    [25.0, 27.5, 30.0, 32.5, 35.0, 40.0],
    "OVERBOUGHT_THRESHOLD":  [60, 65.0, 67.5, 70.0, 72.5, 75.0],
    "REDUCE_LONG":           [40.0, 42.5, 50.0, 52.5, 60.0],
    "REDUCE_SHORT":          [40.0, 42.5, 50.0, 52.5, 60.0],
    "MAX_POSICIONES":        [1, 2, 3],
    "SLOT_FACTOR":           [1.0, 1.2, 1.5, 1.7, 2.0],
    "MODO_OPERACION":        ["limite_gtc", "limit_post_only"],
}

# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# NÚCLEO DEL OPTIMIZADOR
# ══════════════════════════════════════════════════════════════════════════════

def _hash_params(params: dict) -> str:
    """Genera un hash único para una combinación de parámetros."""
    raw = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _build_config_dict() -> dict:
    """Construye el diccionario de configuración global."""
    return {
        "usd_initial": CL.SALDO_USD_INICIAL,
        "commission_pct": CL.COMMISSION_PCT,
        "primary_timeframe": CL.PRIMARY_TIMEFRAME,
        "secondary_timeframe": CL.SECONDARY_TIMEFRAME or "",
        "symbol": CL.SYMBOL,
        "fecha_inicio": CL.FECHA_INICIO,
        "fecha_fin": CL.FECHA_FIN,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PARAMETER GRID
# ══════════════════════════════════════════════════════════════════════════════

class ParameterGrid:
    """Genera y gestiona todas las combinaciones de parámetros."""

    def __init__(self, ranges: Dict[str, list]):
        self.ranges = ranges
        self._combos: List[Dict] = []
        self._hash_set: Set[str] = set()
        self._generate()

    def _generate(self) -> None:
        """Genera todas las combinaciones mediante producto cartesiano."""
        keys = list(self.ranges.keys())
        values = list(self.ranges.values())

        def _cartesian(idx: int, current: Dict):
            if idx == len(keys):
                combo = dict(current)
                combo["_hash"] = _hash_params(combo)
                self._combos.append(combo)
                self._hash_set.add(combo["_hash"])
                return
            for v in values[idx]:
                current[keys[idx]] = v
                _cartesian(idx + 1, current)

        _cartesian(0, {})

    @property
    def combinations(self) -> List[Dict]:
        return list(self._combos)

    @property
    def total(self) -> int:
        return len(self._combos)

    def get_hashes(self) -> Set[str]:
        return set(self._hash_set)

    def filter_completed(self, completed_hashes: Set[str]) -> List[Dict]:
        """Retorna solo las combinaciones no evaluadas."""
        return [c for c in self._combos if c["_hash"] not in completed_hashes]


# ══════════════════════════════════════════════════════════════════════════════
# RSI CACHE
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class RSIEngineSnapshot:
    """Estado interno del StandardRSIEngine en un momento dado."""
    sum_gain: float
    sum_loss: float
    prev_close: Optional[float]
    count: int
    value: Optional[float]
    gains: List[float]
    losses: List[float]


class RSICache:
    """
    Precalcula RSI estándar para cada período único sobre todas las velas.
    Almacena valores RSI y estados del engine para poder llamar a price_for_rsi().
    """

    def __init__(self, candles: List[Candle], periods: List[int]):
        self.candles = candles
        self.periods = sorted(set(periods))
        self._rsi_values: Dict[int, List[Optional[float]]] = {}
        self._snapshots: Dict[int, List[RSIEngineSnapshot]] = {}
        self._build()

    def _build(self) -> None:
        """Construye la caché para todos los períodos."""
        for period in self.periods:
            self._build_for_period(period)

    def _build_for_period(self, period: int) -> None:
        """Construye la caché para un período específico."""
        from indicadores.rsi_standard import StandardRSIEngine

        engine = StandardRSIEngine(period=period)
        values: List[Optional[float]] = []
        snaps: List[RSIEngineSnapshot] = []

        for c in self.candles:
            rsi = engine.update(c.close)
            values.append(rsi)
            snaps.append(RSIEngineSnapshot(
                sum_gain=engine._sum_gain,
                sum_loss=engine._sum_loss,
                prev_close=engine._prev_close,
                count=engine._count,
                value=engine._value,
                gains=list(engine._gains),
                losses=list(engine._losses),
            ))

        self._rsi_values[period] = values
        self._snapshots[period] = snaps

    def get_rsi(self, period: int, idx: int) -> Optional[float]:
        """Retorna el valor RSI en la posición idx para el período dado."""
        if period not in self._rsi_values:
            return None
        vals = self._rsi_values[period]
        if idx < 0 or idx >= len(vals):
            return None
        return vals[idx]

    def get_snapshot(self, period: int, idx: int) -> Optional[RSIEngineSnapshot]:
        """Retorna el snapshot del engine en la posición idx."""
        if period not in self._snapshots:
            return None
        snaps = self._snapshots[period]
        if idx < 0 or idx >= len(snaps):
            return None
        return snaps[idx]

    def restore_engine(self, period: int, idx: int, engine) -> bool:
        """
        Restaura el estado de un StandardRSIEngine al snapshot en la posición idx.
        Retorna True si se pudo restaurar, False si no.
        """
        snap = self.get_snapshot(period, idx)
        if snap is None:
            return False
        engine._sum_gain = snap.sum_gain
        engine._sum_loss = snap.sum_loss
        engine._prev_close = snap.prev_close
        engine._count = snap.count
        engine._value = snap.value
        engine._gains.clear()
        engine._gains.extend(snap.gains)
        engine._losses.clear()
        engine._losses.extend(snap.losses)
        return True


# ══════════════════════════════════════════════════════════════════════════════
# CACHED RSI STRATEGY
# ══════════════════════════════════════════════════════════════════════════════

class CachedRSIStrategy(RSIStandardStrategy):
    """
    Estrategia RSI que usa valores pre-computados en lugar de recalcular.
    Mantiene la misma lógica de señales que RSIStandardStrategy.
    """

    def __init__(
        self,
        rsi_cache: RSICache,
        period: int,
        warmup_offset: int = 0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._rsi_cache = rsi_cache
        self._cache_period = period
        self._candle_idx = 0
        self._warmup_offset = warmup_offset

    def load_warmup(self, candles: List[Candle]) -> None:
        """No-op: el RSI ya está precargado en la caché."""
        pass

    def on_candle(self, candle: Candle, wallet) -> List[Signal]:
        """
        Procesa una vela usando RSI cacheados.
        La lógica es idéntica a RSIStandardStrategy.on_candle().
        IMPORTANTE (lookahead bias): se usa el RSI AL OPEN (close de la vela
        ANTERIOR), no el del close de la vela actual. El engine se restaura
        al estado previo al update del RSI usado, para que price_for_rsi()
        use el estado correcto.
        """
        # RSI al OPEN de esta vela = valor calculado con closes hasta i-1.
        cache_idx = self._warmup_offset + self._candle_idx
        rsi_idx = cache_idx - 1  # RSI al open (close de la vela anterior)
        self._candle_idx += 1

        rsi = self._rsi_cache.get_rsi(self._cache_period, rsi_idx)
        if rsi is None:
            return list(HOLD_LIST)

        # Restaurar el estado del engine con closes hasta rsi_idx (la vela cuyo
        # close generó el RSI usado). El snapshot en `rsi_idx` guarda el estado
        # DESPUÉS de update(closes[rsi_idx]), que es exactamente el estado previo
        # al inicio de la vela actual → price_for_rsi() usa el estado correcto.
        if rsi_idx < 0:
            return list(HOLD_LIST)
        self._rsi_cache.restore_engine(self._cache_period, rsi_idx, self._rsi_engine)

        self._rsi_buffer.append(rsi)
        if len(self._rsi_buffer) > 10:
            self._rsi_buffer = self._rsi_buffer[-10:]

        signals: List[Signal] = []
        ts = candle.ts

        # ── Estado actual de la wallet ─────────────────────────────────────
        has_position = wallet.positions_count > 0
        current_dir = wallet.current_direction

        # ── ZONA LONG: RSI < 50 (punto neutral de la media) ───────────────
        if rsi < 50.0:
            price_open = self._rsi_engine.price_for_rsi(self._oversold)
            price_exit = self._rsi_engine.price_for_rsi(self._reduce_long)
            in_entry_zone = rsi > self._oversold

            if not has_position:
                if in_entry_zone and ts not in self._fired_open_long:
                    signals.append(Signal(
                        signal_type=SignalType.OPEN_LONG,
                        price=price_open,
                        reason=f"rsi_{rsi:.1f}>{self._oversold}_open_long@{price_open:.2f}",
                        ts=ts,
                    ))
                    self._fired_open_long.add(ts)

            elif current_dir == PositionDirection.LONG:
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
            price_open = self._rsi_engine.price_for_rsi(self._overbought)
            price_exit = self._rsi_engine.price_for_rsi(self._reduce_short)
            in_entry_zone = rsi < self._overbought

            if not has_position:
                if in_entry_zone and ts not in self._fired_open_short:
                    signals.append(Signal(
                        signal_type=SignalType.OPEN_SHORT,
                        price=price_open,
                        reason=f"rsi_{rsi:.1f}<{self._overbought}_open_short@{price_open:.2f}",
                        ts=ts,
                    ))
                    self._fired_open_short.add(ts)

            elif current_dir == PositionDirection.SHORT:
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
            pass

        # Limpiar sets periódicamente
        if len(self._fired_open_long) > 100:
            self._fired_open_long.clear()
            self._fired_open_short.clear()
            self._fired_close_long.clear()
            self._fired_close_short.clear()
            self._fired_reduce_long.clear()
            self._fired_reduce_short.clear()

        return signals if signals else list(HOLD_LIST)


# ══════════════════════════════════════════════════════════════════════════════
# PRUNING DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

class PruningDetector:
    """
    Detecta si una combinación debe abortarse por mal rendimiento.
    """

    def __init__(
        self,
        threshold: float = 0.90,
        after_pct: float = 0.15,
        bh_ratio: float = 0.95,
    ):
        self.threshold = threshold
        self.after_pct = after_pct
        self.bh_ratio = bh_ratio
        self._first_close: Optional[float] = None

    def set_first_close(self, close: float) -> None:
        self._first_close = close

    def should_prune(
        self,
        wallet,
        initial_capital: float,
        current_price: float,
        candle_idx: int,
        total_candles: int,
        first_close: Optional[float] = None,
    ) -> bool:
        """
        Evalúa si se debe abortar la combinación actual.
        Retorna True si el rendimiento es demasiado malo.
        """
        # No evaluar hasta haber procesado el % mínimo de velas
        if candle_idx < total_candles * self.after_pct:
            return False

        current_value = wallet.portfolio_value(current_price)
        pct_remaining = current_value / initial_capital if initial_capital > 0 else 0

        # Umbral absoluto
        if pct_remaining < self.threshold:
            return True

        # Comparación con Buy & Hold
        fc = first_close or self._first_close
        if fc is not None and fc > 0 and current_price > 0:
            bh_value = (initial_capital / fc) * current_price
            if current_value < bh_value * self.bh_ratio:
                return True

        return False


# ══════════════════════════════════════════════════════════════════════════════
# TOP 20 LEADERBOARD
# ══════════════════════════════════════════════════════════════════════════════

class Top20Leaderboard:
    """Ranking de los mejores resultados, ordenado por PNL% descendente."""

    def __init__(self, top_n: int = 20, min_trades: int = 10):
        self.top_n = top_n
        self.min_trades = min_trades
        self._rankings: List[Dict] = []

    def add(self, params: dict, summary: dict, elapsed_s: float) -> bool:
        """
        Agrega un resultado al ranking.
        Retorna True si entró en el top N.
        """
        # Filtrar por MIN_TRADES
        total_trades = summary.get("total_trades_ejecutados", 0)
        if total_trades < self.min_trades:
            return False

        # Si está pruned, no incluir
        if summary.get("pruned", False):
            return False

        entry = {
            "hash": params.get("_hash", ""),
            "params": {
                "RSI_PERIOD": params.get("RSI_PERIOD", 0),
                "OVERSOLD_THRESHOLD": params.get("OVERSOLD_THRESHOLD", 0.0),
                "OVERBOUGHT_THRESHOLD": params.get("OVERBOUGHT_THRESHOLD", 0.0),
                "REDUCE_LONG": params.get("REDUCE_LONG", 0.0),
                "REDUCE_SHORT": params.get("REDUCE_SHORT", 0.0),
                "MAX_POSICIONES": params.get("MAX_POSICIONES", 0),
                "SLOT_FACTOR": params.get("SLOT_FACTOR", 0.0),
                "MODO_OPERACION": params.get("MODO_OPERACION", ""),
            },
            "pnl_pct": summary.get("pnl_pct", 0.0),
            "sharpe": summary.get("sharpe", 0.0),
            "max_drawdown_pct": summary.get("max_drawdown_pct", 0.0),
            "total_trades": total_trades,
            "alpha_vs_bh": summary.get("alpha_vs_bh", 0.0),
            "portfolio_value_final": summary.get("portfolio_value_final", 0.0),
            "elapsed_s": round(elapsed_s, 2),
        }

        # Verificar si ya existe (por hash) y actualizar
        for i, existing in enumerate(self._rankings):
            if existing["hash"] == entry["hash"]:
                self._rankings[i] = entry
                self._rankings.sort(key=lambda x: x["pnl_pct"], reverse=True)
                return True

        self._rankings.append(entry)
        self._rankings.sort(key=lambda x: x["pnl_pct"], reverse=True)
        self._rankings = self._rankings[:self.top_n]
        return entry in self._rankings

    def get_top20(self) -> List[Dict]:
        return list(self._rankings)

    def save_json(self, path: str) -> None:
        """Guarda el top 20 en formato JSON."""
        payload = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "top_n": self.top_n,
            "min_trades": self.min_trades,
            "rankings": self._rankings,
        }
        temp_path = path + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
            os.replace(temp_path, path)
        except Exception as e:
            log.error("Error guardando top 20 JSON", error=str(e))

    def save_csv(self, path: str) -> None:
        """Guarda el top 20 en formato CSV."""
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(
                    "Rank,RSI_PERIOD,OVERSOLD,OVERBOUGHT,"
                    "REDUCE_LONG,REDUCE_SHORT,MAX_POSICIONES,"
                    "SLOT_FACTOR,MODO_OPERACION,PNL_PCT,SHARPE,"
                    "MAX_DD_PCT,TOTAL_TRADES,ALPHA_VS_BH,ELAPSED_S\n"
                )
                for i, entry in enumerate(self._rankings, 1):
                    p = entry["params"]
                    f.write(
                        f'{i},{p["RSI_PERIOD"]},{p["OVERSOLD_THRESHOLD"]},'
                        f'{p["OVERBOUGHT_THRESHOLD"]},{p["REDUCE_LONG"]},'
                        f'{p["REDUCE_SHORT"]},{p["MAX_POSICIONES"]},'
                        f'{p["SLOT_FACTOR"]},"{p["MODO_OPERACION"]}",'
                        f'{entry["pnl_pct"]:.4f},{entry["sharpe"]:.4f},'
                        f'{entry["max_drawdown_pct"]:.4f},{entry["total_trades"]},'
                        f'{entry["alpha_vs_bh"]:.4f},{entry["elapsed_s"]:.2f}\n'
                    )
        except Exception as e:
            log.error("Error guardando top 20 CSV", error=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# CHECKPOINT MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class CheckpointManager:
    """
    Persistencia del progreso del optimizador.
    Soporta: guardado periódico, reanudación, CTRL+C.
    """

    def __init__(self, path: str, auto_save_interval: int = 50):
        self.path = path
        self.auto_save_interval = auto_save_interval
        self._interrupted = False
        self._completed_hashes: Set[str] = set()
        self._results: Dict[str, dict] = {}
        self._evaluated_count = 0
        self._last_save_count = 0

        # Configurar manejador de CTRL+C
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame) -> None:
        """Manejador de CTRL+C."""
        print("\n\n⚠️  CTRL+C detectado. Guardando checkpoint y saliendo...")
        self._interrupted = True
        self.save(self._results, self._rankings or [])
        print(f"✓ Checkpoint guardado en: {self.path}")
        print(f"  Progreso: {self._evaluated_count} combinaciones evaluadas")
        print(f"  Para reanudar: python Optimizador_RSI_Standard.py")
        sys.exit(0)

    def set_rankings(self, rankings: list) -> None:
        self._rankings = rankings

    def load(self) -> Tuple[Set[str], Dict[str, dict], Optional[List[Dict]]]:
        """
        Carga el checkpoint desde disco.
        Retorna: (completed_hashes, results, top20)
        """
        if not os.path.exists(self.path):
            return set(), {}, None

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)

            completed = set(data.get("completed_hashes", []))
            results = data.get("results", {})
            top20 = data.get("top20", None)

            log.info(
                "Checkpoint cargado",
                evaluadas=len(completed),
                desde=data.get("updated_at", "desconocido"),
            )
            return completed, results, top20
        except Exception as e:
            log.warning("Error cargando checkpoint", error=str(e))
            return set(), {}, None

    def add_result(self, params_hash: str, params: dict, summary: dict, elapsed_s: float) -> None:
        """Registra un resultado completado."""
        self._completed_hashes.add(params_hash)
        self._results[params_hash] = {
            "params": params,
            "summary": summary,
            "elapsed_s": round(elapsed_s, 2),
        }
        self._evaluated_count += 1

    def save(self, results: Dict, top20: list) -> None:
        """Guarda el checkpoint a disco."""
        payload = {
            "version": 2,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_evaluated": self._evaluated_count,
            "completed_hashes": list(self._completed_hashes),
            "results": results,
            "top20": top20,
        }
        temp_path = self.path + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
            os.replace(temp_path, self.path)
        except Exception as e:
            log.error("Error guardando checkpoint", error=str(e))

    def should_auto_save(self) -> bool:
        """Determina si es momento de guardar automáticamente."""
        if self.auto_save_interval <= 0:
            return False
        return (self._evaluated_count - self._last_save_count) >= self.auto_save_interval

    def reset_auto_save_counter(self) -> None:
        self._last_save_count = self._evaluated_count

    @property
    def is_interrupted(self) -> bool:
        return self._interrupted

    @property
    def evaluated_count(self) -> int:
        return self._evaluated_count

    @property
    def completed_hashes(self) -> Set[str]:
        return self._completed_hashes


# ══════════════════════════════════════════════════════════════════════════════
# WORKER FUNCTION (module-level para multiprocessing en Windows)
# ══════════════════════════════════════════════════════════════════════════════

def worker_chunk(chunk_data: tuple) -> List[Tuple[Dict, Dict, float]]:
    """
    Procesa un lote de combinaciones en un proceso hijo.
    
    Args:
        chunk_data: (combos, candles, rsi_cache_data, config_dict, prune_config)
    
    Returns:
        Lista de (params, summary, elapsed_s)
    """
    combos, candles, rsi_cache_data, config, prune_config = chunk_data

    # Reconstruir RSICache desde datos serializables
    rsi_cache = _deserialize_rsi_cache(rsi_cache_data)

    results: List[Tuple[Dict, Dict, float]] = []

    for params in combos:
        t0 = time.time()

        # Crear actores
        wallet = MemoryWallet(
            usd_initial=config["usd_initial"],
            max_posiciones=params["MAX_POSICIONES"],
            slot_factor=params["SLOT_FACTOR"],
        )

        if params["MODO_OPERACION"] == "limit_post_only":
            ob = SimulatedLimitPostOnlyOrderBook(
                commission_pct=config["commission_pct"],
                max_posiciones=params["MAX_POSICIONES"],
            )
        else:
            ob = SimulatedLimitGTCOrderBook(
                commission_pct=config["commission_pct"],
                max_posiciones=params["MAX_POSICIONES"],
            )

        risk = RiskManager(usd_initial=config["usd_initial"])
        state = MemoryStateManager()

        # Estrategia con RSI cache
        period = params["RSI_PERIOD"]
        strategy = CachedRSIStrategy(
            rsi_cache=rsi_cache,
            period=period,
            # Warmup por combinación: replicar BacktestEngine (rsi_period + 20).
            # Cada combo usa su propio offset para que el estado del RSIEngine
            # (sum_gain/sum_loss) coincida EXACTAMENTE con el backtest individual.
            warmup_offset=params.get("_warmup_offset", config.get("warmup_offset", 0)),
            rsi_period=period,
            oversold_threshold=params["OVERSOLD_THRESHOLD"],
            overbought_threshold=params["OVERBOUGHT_THRESHOLD"],
            reduce_long=params["REDUCE_LONG"],
            reduce_short=params["REDUCE_SHORT"],
            max_positions=params["MAX_POSICIONES"],
        )

        # Contadores (como en BacktestEngine)
        n_long_opens = 0
        n_long_adds = 0
        n_long_reduces = 0
        n_long_closes = 0
        n_short_opens = 0
        n_short_adds = 0
        n_short_reduces = 0
        n_short_closes = 0
        n_ignorados = 0
        realized_pnl_total = 0.0

        # Pruning
        pruning = PruningDetector(
            threshold=prune_config.get("threshold", 0.90),
            after_pct=prune_config.get("after_pct", 0.15),
            bh_ratio=prune_config.get("bh_ratio", 0.95),
        )
        total = len(candles)
        last_candle = None
        pruned = False
        portfolio_history: List[float] = []

        strategy.on_start(wallet)

        # Loop principal
        for i, candle in enumerate(candles):
            last_candle = candle
            signals = strategy.tick(candle, wallet)

            # Procesar señales
            actionable = [s for s in signals if s.is_actionable]
            if actionable:
                if hasattr(ob, 'set_candle'):
                    ob.set_candle(candle)

                for signal in actionable:
                    st = signal.signal_type
                    if st == SignalType.HOLD:
                        continue

                    # Risk check
                    tentative_side = OrderSide.BUY if st in (
                        SignalType.OPEN_LONG, SignalType.ADD_LONG,
                        SignalType.REDUCE_SHORT, SignalType.CLOSE_SHORT,
                    ) else OrderSide.SELL

                    risk_reason = risk.check(tentative_side, signal.price, wallet, candle)
                    if risk_reason:
                        n_ignorados += 1
                        continue

                    # Determinar dirección
                    if st in (SignalType.OPEN_LONG, SignalType.ADD_LONG,
                              SignalType.REDUCE_LONG, SignalType.CLOSE_LONG):
                        direction = PositionDirection.LONG
                    else:
                        direction = PositionDirection.SHORT

                    # Ejecutar según SignalType (con initial_candle_open como BacktestEngine)
                    order = None
                    ic_open = candle.open
                    if st == SignalType.OPEN_LONG:
                        order = ob.open_position(direction, signal.price, wallet,
                                                  candle_ts=candle.ts,
                                                  initial_candle_open=ic_open,
                                                  signal_type=st.value)
                    elif st == SignalType.ADD_LONG:
                        order = ob.add_position(direction, signal.price, wallet,
                                                 candle_ts=candle.ts,
                                                 initial_candle_open=ic_open,
                                                 signal_type=st.value)
                    elif st == SignalType.REDUCE_LONG:
                        order = ob.reduce_position(direction, signal.price, wallet,
                                                    candle_ts=candle.ts,
                                                    initial_candle_open=ic_open,
                                                    signal_type=st.value)
                    elif st == SignalType.CLOSE_LONG:
                        order = ob.close_position(direction, signal.price, wallet,
                                                   candle_ts=candle.ts,
                                                   initial_candle_open=ic_open,
                                                   signal_type=st.value)
                    elif st == SignalType.OPEN_SHORT:
                        order = ob.open_position(direction, signal.price, wallet,
                                                  candle_ts=candle.ts,
                                                  initial_candle_open=ic_open,
                                                  signal_type=st.value)
                    elif st == SignalType.ADD_SHORT:
                        order = ob.add_position(direction, signal.price, wallet,
                                                 candle_ts=candle.ts,
                                                 initial_candle_open=ic_open,
                                                 signal_type=st.value)
                    elif st == SignalType.REDUCE_SHORT:
                        order = ob.reduce_position(direction, signal.price, wallet,
                                                    candle_ts=candle.ts,
                                                    initial_candle_open=ic_open,
                                                    signal_type=st.value)
                    elif st == SignalType.CLOSE_SHORT:
                        order = ob.close_position(direction, signal.price, wallet,
                                                   candle_ts=candle.ts,
                                                   initial_candle_open=ic_open,
                                                   signal_type=st.value)

                    if order is not None and order.is_filled:
                        risk.on_trade_executed()
                        if order.trade and order.trade.realized_pnl:
                            realized_pnl_total += order.trade.realized_pnl
                        # Contabilizar solo si la orden se llenó (como BacktestEngine)
                        if st == SignalType.OPEN_LONG:
                            n_long_opens += 1
                        elif st == SignalType.ADD_LONG:
                            n_long_adds += 1
                        elif st == SignalType.REDUCE_LONG:
                            n_long_reduces += 1
                        elif st == SignalType.CLOSE_LONG:
                            n_long_closes += 1
                        elif st == SignalType.OPEN_SHORT:
                            n_short_opens += 1
                        elif st == SignalType.ADD_SHORT:
                            n_short_adds += 1
                        elif st == SignalType.REDUCE_SHORT:
                            n_short_reduces += 1
                        elif st == SignalType.CLOSE_SHORT:
                            n_short_closes += 1
                    elif order is not None:
                        n_ignorados += 1

                    risk.update_peak(wallet.portfolio_value(candle.close))

            # Pruning check
            if prune_config.get("enabled", True):
                should_prune = pruning.should_prune(
                    wallet, config["usd_initial"],
                    candle.close, i, total,
                    candles[0].open if candles else None,
                )
                if should_prune:
                    pruned = True
                    break

        strategy.on_stop(wallet)

        if pruned:
            elapsed = time.time() - t0
            results.append((params, {"pruned": True}, elapsed))
            continue

        # Construir summary
        import numpy as np

        precio_final = last_candle.close if last_candle else 0
        port_final = wallet.portfolio_value(precio_final)
        pnl_pct = (port_final / config["usd_initial"] - 1) * 100 if config["usd_initial"] > 0 else 0.0

        precio_inicial = candles[0].open if candles else precio_final
        bh_pnl = (precio_final / precio_inicial - 1) * 100 if precio_inicial > 0 else 0.0

        # Sharpe y MaxDD
        try:
            port_arr = np.array([
                wallet.portfolio_value(c.close) for c in candles
            ], dtype=np.float64)

            peak = np.maximum.accumulate(port_arr)
            dd = (port_arr - peak) / np.where(peak == 0, 1, peak) * 100
            max_dd = float(dd.min())

            returns = np.diff(port_arr) / np.where(port_arr[:-1] == 0, 1, port_arr[:-1])
            if len(returns) >= 2 and np.std(returns) > 0:
                tf_seconds = 3600  # Asumimos 1H
                ann_factor = np.sqrt(365 * 24 * 3600 / tf_seconds)
                sharpe = float(np.mean(returns) / np.std(returns) * ann_factor)
            else:
                sharpe = 0.0
        except Exception:
            max_dd = 0.0
            sharpe = 0.0

        total_trades = (n_long_opens + n_long_adds + n_long_reduces + n_long_closes +
                        n_short_opens + n_short_adds + n_short_reduces + n_short_closes)

        summary = {
            "estrategia": "RSI_Standard",
            "pnl_pct": round(pnl_pct, 4),
            "sharpe": round(sharpe, 4),
            "max_drawdown_pct": round(max_dd, 4),
            "buy_hold_pnl_pct": round(bh_pnl, 4),
            "alpha_vs_bh": round(pnl_pct - bh_pnl, 4),
            "total_trades_ejecutados": total_trades,
            "total_compras": n_long_opens + n_long_adds + n_short_reduces + n_short_closes,
            "total_ventas": n_long_reduces + n_long_closes + n_short_opens + n_short_adds,
            "total_ignorados": n_ignorados,
            "long_opens": n_long_opens,
            "long_adds": n_long_adds,
            "long_reduces": n_long_reduces,
            "long_closes": n_long_closes,
            "short_opens": n_short_opens,
            "short_adds": n_short_adds,
            "short_reduces": n_short_reduces,
            "short_closes": n_short_closes,
            "portfolio_value_final": round(port_final, 4),
            "pruned": False,
        }

        elapsed = time.time() - t0
        results.append((params, summary, elapsed))

    return results


def _serialize_rsi_cache(cache: RSICache) -> dict:
    """Serializa RSICache para pasar a workers."""
    return {
        "periods": cache.periods,
        "rsi_values": {str(k): v for k, v in cache._rsi_values.items()},
        "snapshots": {
            str(k): [
                {
                    "sum_gain": s.sum_gain,
                    "sum_loss": s.sum_loss,
                    "prev_close": s.prev_close,
                    "count": s.count,
                    "value": s.value,
                    "gains": s.gains,
                    "losses": s.losses,
                }
                for s in snaps
            ]
            for k, snaps in cache._snapshots.items()
        },
    }


def _deserialize_rsi_cache(data: dict) -> RSICache:
    """Reconstruye RSICache desde datos serializados."""
    cache = RSICache.__new__(RSICache)
    cache.candles = []  # No se necesitan las velas originales
    cache.periods = data.get("periods", [])
    cache._rsi_values = {int(k): v for k, v in data.get("rsi_values", {}).items()}
    cache._snapshots = {}
    for k, snaps in data.get("snapshots", {}).items():
        cache._snapshots[int(k)] = [
            RSIEngineSnapshot(
                sum_gain=s["sum_gain"],
                sum_loss=s["sum_loss"],
                prev_close=s["prev_close"],
                count=s["count"],
                value=s["value"],
                gains=s.get("gains", []),
                losses=s.get("losses", []),
            )
            for s in snaps
        ]
    return cache


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API (para tests)
# ══════════════════════════════════════════════════════════════════════════════

def run_single_combo_lightweight(
    candles: List[Candle],
    params: dict,
    usd_initial: float = 1000.0,
    commission_pct: float = 0.02,
    rsi_cache: Optional[RSICache] = None,
) -> dict:
    """
    Ejecuta UNA combinación con el worker lightweight.
    Función pública para uso externo (tests).
    
    Args:
        candles: Velas a usar
        params: Parámetros de la combinación
        usd_initial: Capital inicial
        commission_pct: Comisión en %
        rsi_cache: Caché RSI (si es None, se calcula sobre la marcha)
    
    Returns:
        dict con summary
    """
    from indicadores.rsi_standard import StandardRSIEngine

    # Warmup: replicar BacktestEngine._load_warmup_candles
    # El backtest individual calienta el RSI con las primeras n_warm velas
    # y luego las vuelve a procesar en el loop principal. Para que el
    # optimizador coincida EXACTAMENTE, la caché debe incluir esas velas
    # de warmup y el offset debe apuntar al inicio del rango.
    rsi_period = params["RSI_PERIOD"]
    n_warm = rsi_period + 20
    warmup_candles = candles[:n_warm] if len(candles) > n_warm else candles
    warmup_offset = len(warmup_candles)

    # Crear o usar caché RSI (con warmup incluido)
    if rsi_cache is None:
        cache_candles = warmup_candles + candles
        rsi_cache = RSICache(cache_candles, [params["RSI_PERIOD"]])

    # Serializar datos para el worker
    chunk_data = (
        [params],  # combos
        candles,
        _serialize_rsi_cache(rsi_cache),
        {
            "usd_initial": usd_initial,
            "commission_pct": commission_pct,
            "primary_timeframe": "1h",
            "secondary_timeframe": "",
            "symbol": "BTCUSDT",
            "fecha_inicio": "",
            "fecha_fin": "",
            "warmup_offset": warmup_offset,
        },
        {
            "enabled": False,  # Sin pruning en modo single
            "threshold": 0.90,
            "after_pct": 0.15,
            "bh_ratio": 0.95,
        },
    )

    results = worker_chunk(chunk_data)
    if not results:
        return {}

    _, summary, _ = results[0]
    return summary


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Punto de entrada principal del optimizador."""
    t_start = time.time()

    # ── Calcular workers ─────────────────────────────────────────────────
    import os as _os
    n_cores = _os.cpu_count() or 4
    n_workers = max(1, n_cores - CORES_A_DEJAR_LIBRES)

    # ── Cargar configuración ─────────────────────────────────────────────
    config = _build_config_dict()

    print("╔" + "═" * 70 + "╗")
    print("║" + "   OPTIMIZADOR RSI STANDARD v1.0".ljust(70) + "║")
    print("╚" + "═" * 70 + "╝")
    print(f"  Rango fechas     : {CL.FECHA_INICIO} → {CL.FECHA_FIN}")
    print(f"  Timeframe         : {CL.PRIMARY_TIMEFRAME.upper()} (sec: {CL.SECONDARY_TIMEFRAME or 'N/A'})")
    print(f"  Capital inicial   : ${CL.SALDO_USD_INICIAL:,.2f}")
    print(f"  Comisión          : {CL.COMMISSION_PCT}%")
    print(f"  Cores totales     : {n_cores}")
    print(f"  Cores a dejar     : {CORES_A_DEJAR_LIBRES}")
    print(f"  Workers           : {n_workers}")
    print(f"  Pruning           : {'ACTIVADO' if PRUNE_ENABLED else 'DESACTIVADO'}")
    print(f"  MIN_TRADES        : {MIN_TRADES}")
    print(f"  Auto-save         : Cada {AUTO_SAVE_INTERVAL} evaluaciones")
    print("─" * 72)

    # ── Cargar checkpoint ────────────────────────────────────────────────
    chk = CheckpointManager(CHECKPOINT_PATH, AUTO_SAVE_INTERVAL)
    completed_hashes, previous_results, previous_top20 = chk.load()

    # ── Generar combinaciones ────────────────────────────────────────────
    grid = ParameterGrid(PARAM_RANGES)
    pendientes = grid.filter_completed(completed_hashes)
    total = grid.total

    print(f"  Combinaciones     : {total}")
    print(f"  Ya evaluadas      : {len(completed_hashes)}")
    print(f"  Pendientes        : {len(pendientes)}")
    print("─" * 72)

    if not pendientes:
        print("✓ Todas las combinaciones ya fueron evaluadas.")
        return

    # ── Cargar datos ─────────────────────────────────────────────────────
    print("  Cargando datos desde SQLite...")
    db_path = resolve_db_path(CL.PRIMARY_TIMEFRAME)
    if not os.path.exists(db_path):
        print(f"✗ Base de datos no encontrada: {db_path}")
        print(f"  Ejecute el script de descarga correspondiente a {CL.PRIMARY_TIMEFRAME}")
        sys.exit(1)

    feed = SQLiteFeed(db_path=db_path)
    candles = feed.get_candles(
        start=CL.FECHA_INICIO,
        end=CL.FECHA_FIN,
        symbol=CL.SYMBOL,
    )
    print(f"  Velas cargadas    : {len(candles)}")

    if not candles:
        print("✗ No se encontraron velas en el rango indicado.")
        sys.exit(1)

    # ── Períodos RSI únicos ──────────────────────────────────────────────
    unique_periods = sorted(set(c["RSI_PERIOD"] for c in pendientes))

    # ── Warmup (replicar BacktestEngine._load_warmup_candles) ────────────
    # El backtest individual calienta el RSI con velas previas a FECHA_INICIO.
    # Para que el optimizador coincida EXACTAMENTE, la caché debe incluir
    # esas velas de warmup y el offset debe apuntar al inicio del rango.
    max_rsi_period = max(unique_periods) if unique_periods else 14
    n_warm = max_rsi_period + 20
    try:
        start_s = to_epoch_s(CL.FECHA_INICIO)
        seconds_per_candle = BacktestEngine._timeframe_to_seconds(CL.PRIMARY_TIMEFRAME)
        margin_s = start_s - (n_warm + 50) * seconds_per_candle
        warm_candles = feed.get_candles(margin_s, start_s - 1, CL.SYMBOL)
        warmup_candles = warm_candles[-n_warm:] if len(warm_candles) > n_warm else warm_candles
    except Exception:
        warmup_candles = []
    warmup_offset = len(warmup_candles)
    print(f"  Velas warmup      : {warmup_offset}")

    # ── Precalcular RSI Cache (con warmup incluido) ──────────────────────
    print("  Precalculando RSI cache...")
    cache_candles = warmup_candles + candles
    rsi_cache = RSICache(cache_candles, unique_periods)
    rsi_cache_data = _serialize_rsi_cache(rsi_cache)
    print(f"  Períodos RSI      : {unique_periods}")

    # Añadir warmup_offset al config para los workers
    config["warmup_offset"] = warmup_offset

    # ── Agrupar y dividir en chunks ──────────────────────────────────────
    # Agrupar por RSI_PERIOD para mejor cache local
    from collections import defaultdict
    grouped = defaultdict(list)
    for c in pendientes:
        # Warmup por combinación: replicar BacktestEngine (rsi_period + 20).
        # El backtest individual calienta el RSI con `rsi_period + 20` velas
        # previas. El optimizador usa un warmup global (max_rsi_period + 20),
        # así que cada combo debe apuntar a su propio offset dentro de la caché
        # para que el estado del RSIEngine coincida con el backtest.
        c["_warmup_offset"] = min(c["RSI_PERIOD"] + 20, warmup_offset)
        grouped[c["RSI_PERIOD"]].append(c)

    chunks = []
    chunk_size = max(1, len(pendientes) // (n_workers * 4))  # ~4 chunks por worker
    for period, combos in grouped.items():
        for i in range(0, len(combos), chunk_size):
            chunk = combos[i:i + chunk_size]
            chunks.append(chunk)

    print(f"  Chunks            : {len(chunks)}")
    print("─" * 72)

    # ── Preparar leaderboard y pruning config ────────────────────────────
    lb = Top20Leaderboard(top_n=TOP_N, min_trades=MIN_TRADES)
    chk.set_rankings(lb.get_top20())

    # Restaurar top 20 previo
    if previous_top20:
        for entry in previous_top20:
            lb._rankings.append(entry)
        lb._rankings.sort(key=lambda x: x["pnl_pct"], reverse=True)
        lb._rankings = lb._rankings[:TOP_N]

    prune_config = {
        "enabled": PRUNE_ENABLED,
        "threshold": PRUNE_THRESHOLD,
        "after_pct": PRUNE_AFTER_PCT,
        "bh_ratio": PRUNE_BH_RATIO,
    }

    # ── Ejecutar en paralelo ─────────────────────────────────────────────
    print(f"\n  Iniciando optimización con {n_workers} workers...\n")

    # Recolectar resultados previos
    all_results = dict(previous_results)
    n_pruned = 0
    n_errores = 0

    # Preparar datos para workers (incluir RSI cache serializado)
    worker_data = [
        (chunk, candles, rsi_cache_data, config, prune_config)
        for chunk in chunks
    ]

    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(worker_chunk, data): i for i, data in enumerate(worker_data)}

        try:
            for future in as_completed(futures):
                if chk.is_interrupted:
                    break

                try:
                    chunk_results = future.result()
                except Exception as e:
                    n_errores += 1
                    log.error("Error en worker", error=str(e))
                    continue

                for params, summary, elapsed in chunk_results:
                    h = params.get("_hash", "")
                    chk.add_result(h, params, summary, elapsed)
                    all_results[h] = {
                        "params": params,
                        "summary": summary,
                        "elapsed_s": round(elapsed, 2),
                    }

                    if summary.get("pruned", False):
                        n_pruned += 1
                    else:
                        entered = lb.add(params, summary, elapsed)
                        if entered:
                            # Mostrar nuevo top 20 en consola
                            _print_top20(lb)

                    # Auto-save
                    if chk.should_auto_save():
                        chk.save(all_results, lb.get_top20())
                        lb.save_json(TOP20_JSON_PATH)
                        lb.save_csv(TOP20_CSV_PATH)
                        chk.reset_auto_save_counter()

                    # Mostrar progreso
                    _print_progress(
                        chk.evaluated_count, len(pendientes),
                        n_pruned, n_errores, time.time() - t_start,
                    )

        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupción detectada. Finalizando workers...")
            executor.shutdown(wait=False, cancel_futures=True)
            chk.save(all_results, lb.get_top20())
            lb.save_json(TOP20_JSON_PATH)
            lb.save_csv(TOP20_CSV_PATH)

    # ── Finalizar ────────────────────────────────────────────────────────
    chk.save(all_results, lb.get_top20())
    lb.save_json(TOP20_JSON_PATH)
    lb.save_csv(TOP20_CSV_PATH)

    elapsed_total = time.time() - t_start
    print("\n" + "═" * 72)
    print("  OPTIMIZACIÓN COMPLETADA")
    print("═" * 72)
    print(f"  Evaluadas         : {chk.evaluated_count}")
    print(f"  Pruned            : {n_pruned}")
    print(f"  Errores           : {n_errores}")
    print(f"  Tiempo total      : {elapsed_total:.1f} segundos ({elapsed_total/60:.1f} minutos)")
    print(f"  Promedio          : {elapsed_total/max(1, chk.evaluated_count):.2f} s/combo")
    print("─" * 72)

    _print_top20(lb, final=True)

    print(f"\n✓ Resultados guardados en:")
    print(f"  - {TOP20_JSON_PATH}")
    print(f"  - {TOP20_CSV_PATH}")
    print(f"  - {CHECKPOINT_PATH}")


def _print_progress(evaluated: int, total: int, pruned: int, errors: int, elapsed: float) -> None:
    """Muestra barra de progreso en consola."""
    pct = (evaluated / total * 100) if total > 0 else 0
    bar_len = 30
    filled = int(bar_len * evaluated / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_len - filled)

    avg_time = elapsed / max(1, evaluated)
    remaining = (total - evaluated) * avg_time if evaluated > 0 else 0

    # Formatear tiempos
    elapsed_str = time.strftime("%H:%M:%S", time.gmtime(elapsed))
    remaining_str = time.strftime("%H:%M:%S", time.gmtime(remaining))

    print(
        f"\r  Progreso: [{bar}] {evaluated}/{total} ({pct:.1f}%)  "
        f"Tiempo: {elapsed_str} | Restante: {remaining_str}  "
        f"Pruned: {pruned} | Errores: {errors}   ",
        end="", flush=True,
    )


def _print_top20(lb: Top20Leaderboard, final: bool = False) -> None:
    """Muestra el top 20 en consola."""
    top20 = lb.get_top20()
    if not top20:
        return

    label = "TOP 20 FINAL" if final else "TOP 20 (actualizado)"
    print(f"\n  ─── {label} ───────────────────────────────────")
    for i, entry in enumerate(top20, 1):
        p = entry["params"]
        sign = "+" if entry["pnl_pct"] >= 0 else ""
        print(
            f"  #{i:<2}  PNL: {sign}{entry['pnl_pct']:.2f}%  | "
            f"RSI={p['RSI_PERIOD']} OS={p['OVERSOLD_THRESHOLD']:.0f} "
            f"OB={p['OVERBOUGHT_THRESHOLD']:.0f} "
            f"RL={p['REDUCE_LONG']:.0f} RS={p['REDUCE_SHORT']:.0f} "
            f"MP={p['MAX_POSICIONES']} SF={p['SLOT_FACTOR']} "
            f"Modo={p['MODO_OPERACION'][:8]}  "
            f"Sharpe={entry['sharpe']:.2f} "
            f"DD={entry['max_drawdown_pct']:.1f}% "
            f"Trades={entry['total_trades']}"
        )
    print("  ───────────────────────────────────────────────")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    main()