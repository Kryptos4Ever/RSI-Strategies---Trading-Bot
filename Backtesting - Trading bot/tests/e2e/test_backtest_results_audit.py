"""
tests/e2e/test_backtest_results_audit.py — Auditoría del artefacto JSON
========================================================================
Se ejecuta sobre el último resultado generado por Backtest_Dual_Bands.py.
No vuelve a ejecutar el backtest: reconstruye la estrategia y el libro mayor a
partir del artefacto persistido.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest


RESULTS_PATH = Path(__file__).resolve().parents[2] / "backtest_results.json"


@pytest.fixture(scope="module")
def results() -> dict:
    if not RESULTS_PATH.exists():
        pytest.skip("No existe backtest_results.json; ejecute un backtest primero.")
    with RESULTS_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def _assert_close(actual: float, expected: float, *, tolerance: float = 1e-4) -> None:
    assert actual == pytest.approx(expected, abs=tolerance)


# Tolerancia para métricas de portfolio: la wallet real usa LIFO por slots
# (`_btc_por_posicion`), que un ledger agregado simple NO replica. La
# reconciliación independiente acumula un residuo pequeño (del orden de
# décimas de USD / centésimas de %), por lo que se acepta un margen razonable.
TOL_PORTFOLIO_USD = 1.0
TOL_PNL_PCT = 0.1


@pytest.mark.e2e
def test_persisted_results_reconcile_trade_ledger_and_summary(results: dict) -> None:
    """Recalcula comisiones, balances y métricas finales desde trade_history."""
    summary = results["summary"]
    commission_pct = summary["parametros"]["commission_pct"]
    usdt = float(results["usd_initial"])
    btc = 0.0
    btc_sold_total = 0.0
    buys = sells = ignored = 0
    collateral_stack: list = []

    for trade in results["trade_history"]:
        if trade["ignorado"]:
            ignored += 1
        elif trade["type"] == "BUY":
            buys += 1
            spent = trade["usd_spent"]
            expected_commission = round(spent * commission_pct / 100.0, 8)
            expected_btc = round((spent - expected_commission) / trade["price"], 10)
            _assert_close(trade["commission_usd"], expected_commission)
            _assert_close(trade["btc_bought"], expected_btc, tolerance=1e-7)

            is_short_close = trade.get("direction") == "SHORT"
            if is_short_close:
                usdt -= spent
                if collateral_stack:
                    released = collateral_stack.pop()
                    usdt += released
                btc += trade["btc_bought"]
            else:
                usdt -= spent
                btc += trade["btc_bought"]
        else:
            sells += 1
            sold = trade["btc_sold"]
            gross = round(sold * trade["price"], 8)
            expected_commission = round(gross * commission_pct / 100.0, 8)
            expected_received = round(gross - expected_commission, 8)
            _assert_close(trade["commission_usd"], expected_commission)
            _assert_close(trade["usd_received"], expected_received, tolerance=1e-5)

            is_short_open = trade.get("direction") == "SHORT"
            if is_short_open:
                slot = trade["usd_received"] + (trade["commission_usd"] or 0.0)
                usdt += trade["usd_received"]
                usdt -= slot
                collateral_stack.append(slot)
                btc -= sold  # SHORT: btc va negativo (deuda)
            else:
                usdt += trade["usd_received"]
                btc = max(0.0, btc - sold)  # LONG: max(0.0) evita residuos

            btc_sold_total += sold

        _assert_close(trade["usd_balance"], usdt)

    final_close = summary["chart_data"]["candles"][-1]["close"]
    short_collateral_final = sum(collateral_stack)
    portfolio = usdt + btc * final_close + short_collateral_final
    pnl_pct = (portfolio / results["usd_initial"] - 1.0) * 100.0
    buy_hold = (final_close / summary["chart_data"]["candles"][0]["open"] - 1.0) * 100.0

    _assert_close(summary["usd_balance_final"], usdt)
    _assert_close(summary["btc_en_posiciones_final"], abs(btc), tolerance=1e-2)
    _assert_close(summary["btc_acumulado_total"], btc_sold_total, tolerance=1e-7)
    _assert_close(summary["portfolio_value_final"], portfolio, tolerance=TOL_PORTFOLIO_USD)
    _assert_close(summary["pnl_pct"], pnl_pct, tolerance=TOL_PNL_PCT)
    _assert_close(summary["buy_hold_pnl_pct"], buy_hold, tolerance=1e-4)
    _assert_close(summary["alpha_vs_bh"], pnl_pct - buy_hold, tolerance=TOL_PNL_PCT)
    assert summary["total_compras"] == buys
    assert summary["total_ventas"] == sells
    assert summary["total_ignorados"] == ignored


@pytest.mark.e2e
def test_post_only_rules_compliance(results: dict) -> None:
    """En modo limit_post_only, cada trade debe cumplir BUY price < open, SELL price > open."""
    params = results["summary"]["parametros"]
    modo = params.get("modo_operacion", "")

    if modo != "limit_post_only":
        pytest.skip("Modo no es limit_post_only, se salta verificación.")

    candles_by_ts = {c["ts"]: c for c in results["summary"]["chart_data"]["candles"]}
    violations = []

    for trade in results["trade_history"]:
        if trade["ignorado"]:
            continue
        candle = candles_by_ts.get(trade["ts"])
        if candle is None:
            continue
        if trade["type"] == "BUY" and trade["price"] >= candle["open"]:
            violations.append(f"BUY ts={trade['ts']}: price={trade['price']} >= open={candle['open']}")
        elif trade["type"] == "SELL" and trade["price"] <= candle["open"]:
            violations.append(f"SELL ts={trade['ts']}: price={trade['price']} <= open={candle['open']}")

    assert not violations, f"Se encontraron {len(violations)} violaciones post-only:\n" + "\n".join(violations)


@pytest.mark.e2e
def test_no_invalid_prices_in_trades(results: dict) -> None:
    """Ningún trade debe tener precio <= 0 ni motivo invalid_price."""
    for trade in results["trade_history"]:
        assert trade["price"] > 0, f"Trade con precio inválido: ts={trade['ts']} price={trade['price']}"
        if trade["ignorado"] and trade.get("motivo_ignorado") == "invalid_price":
            pytest.fail(f"Trade ignorado por invalid_price en ts={trade['ts']}")


@pytest.mark.e2e
def test_realized_pnl_per_trade_is_plausible(results: dict) -> None:
    """Cada trade SELL debe tener un realized_pnl numérico y no NaN."""
    for trade in results["trade_history"]:
        if trade["ignorado"] or trade["type"] != "SELL":
            continue
        rp = trade.get("realized_pnl")
        if rp is not None:
            assert not math.isnan(rp), f"realized_pnl es NaN en ts={trade['ts']}"
            assert math.isfinite(rp), f"realized_pnl no es finito en ts={trade['ts']}"
            assert abs(rp) < 1e12, f"realized_pnl absurdo en ts={trade['ts']}: {rp}"


@pytest.mark.e2e
def test_ignored_reasons_sum_matches_total(results: dict) -> None:
    """La suma de los motivos de ignorados debe igualar total_ignorados."""
    summary = results["summary"]
    total_ignorados = summary["total_ignorados"]
    reasons = summary.get("ignorados_por_motivo", {})
    total_from_reasons = sum(reasons.values())
    ignored_in_history = sum(1 for t in results["trade_history"] if t["ignorado"])

    assert total_from_reasons == total_ignorados
    assert ignored_in_history == total_ignorados


@pytest.mark.e2e
def test_sharpe_and_maxdd_in_plausible_range(results: dict) -> None:
    """Sharpe y Max Drawdown deben estar en rangos plausibles."""
    summary = results["summary"]
    sharpe = summary.get("sharpe", 0.0)
    max_dd = summary.get("max_drawdown_pct", 0.0)

    assert isinstance(sharpe, (int, float)), f"Sharpe no es numérico: {sharpe}"
    assert isinstance(max_dd, (int, float)), f"MaxDD no es numérico: {max_dd}"
    assert -10.0 <= sharpe <= 50.0, f"Sharpe fuera de rango: {sharpe}"
    assert -100.0 <= max_dd <= 0.0, f"MaxDD fuera de rango: {max_dd}%"


@pytest.mark.e2e
def test_basic_summary_integrity(results: dict) -> None:
    """Verifica que existan velas y que el Sharpe esté calculado."""
    n_candles = len(results["summary"]["chart_data"]["candles"])
    assert n_candles > 0, "No hay velas en chart_data"
    sharpe = results["summary"].get("sharpe", 0.0)
    assert isinstance(sharpe, (int, float)), "Sharpe no calculado"


@pytest.mark.e2e
def test_portfolio_value_reconciliation(results: dict) -> None:
    """Verifica portfolio_value_final según la dirección de la posición (LONG o SHORT)."""
    summary = results["summary"]
    usd = summary["usd_balance_final"]
    btc = summary["btc_en_posiciones_final"]
    last_close = summary["chart_data"]["candles"][-1]["close"]
    port = summary["portfolio_value_final"]
    direction = summary.get("current_direction", "NONE")
    if direction == "SHORT":
        short_collateral = summary.get("usd_short_collateral_final", 0.0)
        expected = usd - btc * last_close + short_collateral
    else:
        expected = usd + btc * last_close
    assert port == pytest.approx(expected, abs=1e-4)


@pytest.mark.e2e
def test_trade_timestamps_exist_in_candles(results: dict) -> None:
    """Todo trade.ts debe existir en chart_data.candles[].ts."""
    candle_ts_set = {c["ts"] for c in results["summary"]["chart_data"]["candles"]}
    for trade in results["trade_history"]:
        hourly_ts = (trade["ts"] // 3600) * 3600
        assert hourly_ts in candle_ts_set, f"Trade ts={trade['ts']} (hora {hourly_ts}) no encontrado en velas"


if __name__ == "__main__":
    from tests._direct_runner import run_current_test_file
    raise SystemExit(run_current_test_file(__file__))