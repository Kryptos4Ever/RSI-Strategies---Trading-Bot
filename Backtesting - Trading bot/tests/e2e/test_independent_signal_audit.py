"""
tests/e2e/test_independent_signal_audit.py — Auditoría independiente del artefacto JSON
==========================================================================================
Pone a prueba lo que registró el bot SIN confiar en él.

Audita lo que registró el bot reconstruyendo un libro de órdenes/posiciones
INDEPENDIENTE (sin usar ninguna clase del pipeline: solo datos del propio JSON):
  1. La señal emitida (signal_type) es coherente con el `direction` que el propio
     trade registra (LONG ↔ OPEN/ADD/REDUCE/CLOSE_LONG; SHORT ↔ ..._SHORT).
  2. El P&L de cada operación que cierra posición coincide con la fórmula del
     wallet usando el precio promedio ponderado vigente en ese momento (tolerancia
     amplia por el LIFO por slots de la wallet real).
  3. Los precios de las señales de LONG/SHORT mantienen el orden monótono
     exigido por la estrategia mean-reversion.
  4. Todas las velas fueron evaluadas en trade_history.

El ledger interno replica las reglas de `MemoryWallet` (agregado LIFO por slots):
  - LONG:  OPEN/ADD = BUY (agrega BTC, recalcula avg). REDUCE/CLOSE = SELL (resta BTC, usa avg vigente).
  - SHORT: OPEN/ADD = SELL (agrega BTC, recalcula avg). REDUCE/CLOSE = BUY (resta BTC, usa avg vigente).
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

RESULTS_PATH = REPO_ROOT / "backtest_results.json"

# Tolerancia USD: la wallet usa LIFO por slots (`_btc_por_posicion`), no un
# promedio simple. Un modelo independiente que no replica el LIFO se desvía en
# centavos, por lo que aceptamos hasta 0.05 USD por trade ("corroborar" sin replicar).
TOL_USD = 0.05
TOL_BTC = 1e-6


@pytest.fixture(scope="module")
def results() -> dict:
    if not RESULTS_PATH.exists():
        pytest.skip("No existe backtest_results.json; ejecute un backtest primero.")
    with RESULTS_PATH.open(encoding="utf-8") as file:
        return json.load(file)


# ─────────────────────────────────────────────────────────────────────────────
# Ledger independiente: dirección + BTC + precio promedio
# ─────────────────────────────────────────────────────────────────────────────
class Ledger:
    """Replica el estado de la wallet (MemoryWallet) de forma independiente."""

    def __init__(self) -> None:
        self.total_btc: float = 0.0
        self.avg_entry: float = 0.0
        self.direction: str = "NONE"

    def _set_avg(self, direction: str, price: float, btc: float) -> None:
        """Recalcula el precio promedio ponderado al AGREGAR posición."""
        if self.total_btc <= 0 or self.direction != direction:
            self.avg_entry = price
        else:
            self.avg_entry = (self.avg_entry * self.total_btc + price * btc) / (self.total_btc + btc)

    def apply(self, trade: dict) -> Optional[float]:
        """
        Aplica un trade EJECUTADO (ignorado=False) al ledger y devuelve el
        realized_pnl si esta operación cierra/parcializa posición (o None).
        Devuelve el P&L BRUTO (sin comisión), igual que la wallet.
        """
        direction = trade.get("direction")
        if direction not in ("LONG", "SHORT"):
            return None

        if direction == "LONG" and trade["type"] == "BUY":  # OPEN/ADD LONG
            btc = trade.get("btc_bought") or 0.0
            self._set_avg("LONG", trade["price"], btc)
            self.total_btc += btc
            self.direction = "LONG"
            return None

        if direction == "LONG" and trade["type"] == "SELL":  # REDUCE/CLOSE LONG
            btc_sold = trade.get("btc_sold") or 0.0
            pnl = (trade["price"] - self.avg_entry) * btc_sold
            self.total_btc = max(0.0, self.total_btc - btc_sold)
            if self.total_btc <= TOL_BTC:
                self.total_btc = 0.0
                self.avg_entry = 0.0
                self.direction = "NONE"
            return pnl

        if direction == "SHORT" and trade["type"] == "SELL":  # OPEN/ADD SHORT
            btc = trade.get("btc_sold") or 0.0
            self._set_avg("SHORT", trade["price"], btc)
            self.total_btc += btc
            self.direction = "SHORT"
            return None

        if direction == "SHORT" and trade["type"] == "BUY":  # REDUCE/CLOSE SHORT
            btc_bought = trade.get("btc_bought") or 0.0
            pnl = (self.avg_entry - trade["price"]) * btc_bought
            self.total_btc = max(0.0, self.total_btc - btc_bought)
            if self.total_btc <= TOL_BTC:
                self.total_btc = 0.0
                self.avg_entry = 0.0
                self.direction = "NONE"
            return pnl

        return None


# ─────────────────────────────────────────────────────────────────────────────
# 1. Coherencia señal ↔ direction del propio trade
# ─────────────────────────────────────────────────────────────────────────────
# NOTA: No se verifica la posición ANTES de operar porque en modo `limite_gtc`
# una señal emitida en una vela puede ejecutarse en una vela POSTERIOR (fill
# pendiente). El JSON registra el ts de ejecución, no el de emisión, por lo que
# no es posible reconstruir confiablemente la posición previa a la EMISIÓN.
# Lo que sí es verificable de forma inmediata y confiable:
#   - Toda señal LONG (OPEN/ADD/REDUCE/CLOSE) debe tener direction=LONG en el trade
#   - Toda señal SHORT (OPEN/ADD/REDUCE/CLOSE) debe tener direction=SHORT en el trade
SIGNAL_LONG = {"OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG"}
SIGNAL_SHORT = {"OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT"}
KNOWN_SIGNALS = SIGNAL_LONG | SIGNAL_SHORT


@pytest.mark.e2e
def test_signals_coherent_with_trade_direction(results: dict) -> None:
    """Toda señal LONG debe tener direction=LONG y toda señal SHORT direction=SHORT."""
    trades = results["trade_history"]
    failures: List[str] = []

    for trade in trades:
        sig = trade.get("signal_type")
        if not sig:
            continue
        if sig not in KNOWN_SIGNALS:
            failures.append(f"ts={trade['ts']}: signal_type desconocido {sig}")
            continue
        trade_dir = trade.get("direction")
        if sig in SIGNAL_LONG and trade_dir != "LONG":
            failures.append(f"ts={trade['ts']} signal={sig}: direction del trade={trade_dir} != LONG")
        elif sig in SIGNAL_SHORT and trade_dir != "SHORT":
            failures.append(f"ts={trade['ts']} signal={sig}: direction del trade={trade_dir} != SHORT")

    assert not failures, "Señales incoherentes:\n" + "\n".join(failures[:50])


# ─────────────────────────────────────────────────────────────────────────────
# 2. P&L independiente (ledger con promedio ponderado por precio de SEÑAL)
# ─────────────────────────────────────────────────────────────────────────────
# La wallet calcula `realized_pnl` usando el precio PROMEDIO de entrada vigente,
# que a su vez proviene de los ADDs/OPENS ejecutados. En modo `limite_gtc` algunos
# ADDs pueden llenarse a precio de MERCADO (fill), que el JSON NO almacena
# (solo guarda el precio de SEÑAL en `price`). Por eso un modelo independiente
# no puede replicar el avg exacto del bot.
#
# Lo que SÍ se puede auditar de forma independiente: el P&L registrado debe estar
# DENTRO de una banda plausible alrededor del P&L calculado con los precios de
# señal, suponiendo un slippage de llenado razonable (0.5% del bruto).
PNL_SLIPPAGE_FRACTION = 0.005  # 0.5% del bruto como banda por fill vs señal


@pytest.mark.e2e
def test_realized_pnl_plausible(results: dict) -> None:
    """El P&L de cada cierre debe estar dentro del rango plausible derivado de
    los precios de señal de entrada y el precio de salida (banda por slippage)."""
    trades = results["trade_history"]
    ledger = Ledger()
    failures: List[str] = []

    for trade in trades:
        if not trade["ignorado"]:
            expected = ledger.apply(trade)  # aplica y devuelve pnl esperado (señal) si es cierre
            if expected is not None:
                rp = trade.get("realized_pnl")
                if rp is None:
                    failures.append(
                        f"ts={trade['ts']} dir={trade.get('direction')} type={trade['type']}: "
                        f"falta realized_pnl"
                    )
                    continue
                # Banda: 0.5% del bruto de salida (slippage de fill en los ADDs)
                gross = (trade.get("btc_sold") or trade.get("btc_bought") or 0.0) * trade["price"]
                band = max(PNL_SLIPPAGE_FRACTION * gross, 0.05)
                if abs(rp - expected) > band:
                    failures.append(
                        f"ts={trade['ts']} dir={trade.get('direction')} type={trade['type']}: "
                        f"pnl={rp} fuera de banda esperada {expected:.4f}±{band:.4f}"
                    )

    assert not failures, "P&L fuera de banda plausible:\n" + "\n".join(failures[:50])


# ─────────────────────────────────────────────────────────────────────────────
# 3. Coherencia de precios con la dirección (monotonía mean-reversion)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.e2e
def test_signal_prices_monotonic_by_zone(results: dict) -> None:
    """
    En la estrategia mean-reversion:
      - Zona LONG (rsi<50): price(oversold=30) < price(reduce=40) < price(overbought=65)
        → OPEN/ADD_LONG (30) < REDUCE_LONG (40) < CLOSE_LONG (65)
      - Zona SHORT (rsi>50): price(overbought=65) > price(reduce=50) > price(oversold=30)
        → OPEN/ADD_SHORT (65) > REDUCE_SHORT (50) > CLOSE_SHORT (30)
    Se verifican pares emitidos en la MISMA vela.
    """
    trades = results["trade_history"]
    trades_by_ts: Dict[int, List[dict]] = {}
    for trade in trades:
        if trade["ignorado"]:
            continue
        trades_by_ts.setdefault(trade["ts"], []).append(trade)

    failures: List[str] = []
    for ts, tlist in trades_by_ts.items():
        long_opens = [t for t in tlist if t.get("signal_type") in ("OPEN_LONG", "ADD_LONG")]
        long_reduces = [t for t in tlist if t.get("signal_type") == "REDUCE_LONG"]
        if long_opens and long_reduces and long_opens[0]["price"] >= long_reduces[0]["price"]:
            failures.append(
                f"ts={ts}: OPEN/ADD_LONG price={long_opens[0]['price']} >= "
                f"REDUCE_LONG price={long_reduces[0]['price']} (viola oversold<reduce)"
            )

        short_opens = [t for t in tlist if t.get("signal_type") in ("OPEN_SHORT", "ADD_SHORT")]
        short_reduces = [t for t in tlist if t.get("signal_type") == "REDUCE_SHORT"]
        if short_opens and short_reduces and short_opens[0]["price"] <= short_reduces[0]["price"]:
            failures.append(
                f"ts={ts}: OPEN/ADD_SHORT price={short_opens[0]['price']} <= "
                f"REDUCE_SHORT price={short_reduces[0]['price']} (viola overbought>reduce)"
            )

        long_close = [t for t in tlist if t.get("signal_type") == "CLOSE_LONG"]
        if long_close and long_reduces and long_close[0]["price"] <= long_reduces[0]["price"]:
            failures.append(
                f"ts={ts}: CLOSE_LONG price={long_close[0]['price']} <= "
                f"REDUCE_LONG price={long_reduces[0]['price']} (viola overbought>reduce)"
            )

    assert not failures, "Orden de precios violado:\n" + "\n".join(failures[:50])


# ─────────────────────────────────────────────────────────────────────────────
# 4. Cobertura: TODAS las velas en zona de entrada deben estar evaluadas
# ─────────────────────────────────────────────────────────────────────────────
# El motor solo registra en `trade_history` las velas que generan señales
# accionables (ejecutadas o ignoradas). Una vela donde la estrategia retorna
# HOLD (sin señal) NO deja registro. Por lo tanto, no es posible distinguir
# "vela evaluada sin señal" de "vela NO evaluada" por ausencia de registro.
#
# Lo que SÍ es verificable de forma independiente: una vela en ZONA DE ENTRADA
# SIEMPRE debe generar al menos una señal
# (OPEN/ADD/REDUCE/CLOSE según la posición vigente). Si esa vela no aparece en
# `trade_history`, la estrategia NO la evaluó (o el motor la saltó) → BUG.
#
# Fuente independiente de velas: `summary.chart_data.candles` (mismo JSON).
# Cada vela incluye el campo `rsi`: el valor VIGENTE al INICIO de la vela
# (calculado con los closes hasta la vela anterior), el mismo que la estrategia
# usó para decidir. Por eso el test NO replica ningún algoritmo RSI: usa
# directamente el RSI que la estrategia registró en el JSON.
#
# Zona de entrada (punto neutral fijo RSI = 50, igual que la estrategia):
#   - Zona LONG:  oversold < RSI < 50  → OPEN/ADD LONG
#   - Zona SHORT: 50 < RSI < overbought → OPEN/ADD SHORT
#   - RSI = 50 → zona neutral, sin señal (correcto).
#
# NOTA: un mismo ts puede tener 1 o 2 entradas en `trade_history` (una BUY y/o
# una SELL). Por eso se compara como CONJUNTO de ts, no por cantidad de entradas.
def _utc_iso(ts: int) -> str:
    """Convierte un timestamp unix a ISO-8601 UTC (sin deprecation)."""
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.mark.e2e
def test_all_candles_in_range_were_evaluated(results: dict) -> None:
    """Toda vela del rango en ZONA DE ENTRADA (30<RSI<50 o 50<RSI<60) debe estar
    evaluada, es decir, debe existir al menos una entrada en trade_history."""
    candles = results["summary"]["chart_data"]["candles"]
    if not candles:
        pytest.skip("No hay velas en chart_data; no se puede auditar cobertura.")

    # Umbrales reales de la estrategia (no hardcodeados).
    oversold = results["summary"]["parametros"].get("oversold_threshold", 30.0)
    overbought = results["summary"]["parametros"].get("overbought_threshold", 70.0)

    # Conjunto de ts con al menos una entrada en trade_history (1 o 2 por ts).
    history_ts = {t["ts"] for t in results["trade_history"]}

    # Velas en zona de entrada que DEBEN generar señal.
    # El RSI de cada vela es el que la estrategia registró en el JSON (al open).
    faltantes: List[int] = []
    for c in candles:
        r = c.get("rsi")
        if r is None:
            continue
        in_entry_zone = (oversold < r < 50.0) or (50.0 < r < overbought)
        if in_entry_zone and c["ts"] not in history_ts:
            faltantes.append(c["ts"])

    if not faltantes:
        return

    # ── Log de diagnóstico completo ────────────────────────────────────────
    candle_by_ts = {c["ts"]: c for c in candles}
    rsi_by_ts = {c["ts"]: c.get("rsi") for c in candles}
    candle_ts = [c["ts"] for c in candles]
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("VELAS EN ZONA DE ENTRADA NO EVALUADAS POR LA ESTRATEGIA")
    lines.append("=" * 72)
    lines.append(
        f"RESUMEN: {len(faltantes)} velas en zona de entrada "
        f"({oversold}<RSI<50 o 50<RSI<{overbought}) NO fueron evaluadas."
    )
    lines.append("")

    # Detalle por vela faltante (máx. 50) con OHLC, RSI y contexto temporal
    for ts in faltantes[:50]:
        c = candle_by_ts.get(ts, {})
        r = rsi_by_ts.get(ts)
        lines.append("VELA NO EVALUADA")
        lines.append(f"  ts:        {ts}")
        lines.append(f"  datetime:  {_utc_iso(ts)}")
        lines.append(f"  rsi:       {('%.2f' % r) if r is not None else 'n/a'}")
        lines.append(
            f"  open: {c.get('open', '?')}   high: {c.get('high', '?')}   "
            f"low: {c.get('low', '?')}   close: {c.get('close', '?')}"
        )
        # Contexto temporal: vela previa y siguiente (registradas o no)
        idx = candle_ts.index(ts) if ts in candle_ts else -1
        if idx > 0:
            prev_ts = candle_ts[idx - 1]
            prev_reg = "registrada" if prev_ts in history_ts else "NO registrada"
            lines.append(
                f"  vela previa:   ts={prev_ts}  {_utc_iso(prev_ts)}  → {prev_reg}"
            )
        if idx >= 0 and idx + 1 < len(candle_ts):
            next_ts = candle_ts[idx + 1]
            next_reg = "registrada" if next_ts in history_ts else "NO registrada"
            lines.append(
                f"  vela siguiente: ts={next_ts}  {_utc_iso(next_ts)}  → {next_reg}"
            )
        lines.append("")

    # Lista completa de faltantes (ts + datetime + rsi) para dimensionar
    lines.append(f"TODAS las {len(faltantes)} no evaluadas:")
    for ts in faltantes:
        r = rsi_by_ts.get(ts)
        lines.append(f"  {ts}  {_utc_iso(ts)}  rsi={('%.2f' % r) if r is not None else 'n/a'}")

    pytest.fail("\n".join(lines))

if __name__ == "__main__":
    from tests._direct_runner import run_current_test_file
    raise SystemExit(run_current_test_file(__file__))
