"""
tests/e2e/test_backtest_json_integrity.py — Integridad estructural del JSON de resultados
=========================================================================================
Audita el archivo ``backtest_results.json`` generado por ``Backtest_Dual_Bands.py``
verificando consistencia estructural, tipos de datos, y relaciones entre campos.

NO vuelve a ejecutar el backtest. Opera exclusivamente sobre el artefacto persistido.
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
        pytest.skip("No existe backtest_results.json; ejecute Backtest_Dual_Bands.py primero.")
    with RESULTS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════════════════════
# ESTRUCTURA DEL JSON
# ══════════════════════════════════════════════════════════════════════════════


class TestJsonStructure:
    """Verifica que el JSON tenga la estructura esperada."""

    def test_root_keys_exist(self, results: dict) -> None:
        """El JSON raíz debe tener las claves mínimas."""
        required = {"usd_initial", "summary", "trade_history"}
        assert required.issubset(results.keys()), (
            f"Faltan claves raíz: {required - results.keys()}"
        )

    def test_summary_keys_exist(self, results: dict) -> None:
        """El summary debe tener todas las claves requeridas."""
        summary = results["summary"]
        required = {
            "estrategia", "fecha_inicio", "fecha_fin",
            "saldo_inicial_usd", "usd_balance_final", "btc_balance_final",
            "btc_acumulado_total", "btc_en_posiciones_final",
            "portfolio_value_final", "pnl_pct", "sharpe", "max_drawdown_pct",
            "buy_hold_pnl_pct", "alpha_vs_bh",
            "total_compras", "total_ventas", "total_ignorados",
            "ignorados_por_motivo", "positions_count_final",
            "parametros", "chart_data",
        }
        missing = required - summary.keys()
        assert not missing, f"Faltan claves en summary: {missing}"

    def test_parametros_keys_exist(self, results: dict) -> None:
        """parametros debe tener las claves de configuración según la estrategia."""
        params = results["summary"]["parametros"]
        estrategia = params.get("estrategia", "")
        # Claves universales que toda estrategia debe tener
        universal = {"estrategia", "max_posiciones", "commission_pct",
                     "slot_usd_final", "primary_timeframe", "secondary_timeframe"}
        missing_universal = universal - params.keys()
        assert not missing_universal, f"Faltan claves universales en parametros: {missing_universal}"

        # Claves específicas según la estrategia
        if "RSI" in estrategia.upper():
            specific = {"rsi_period", "oversold_threshold", "overbought_threshold",
                        "reduce_long", "reduce_short", "max_positions"}
        elif "BOLLINGER" in estrategia.upper() or "BB" in estrategia.upper():
            specific = {"modo_calculo", "modo_operacion",
                        "bb_period_buy", "bb_std_mult_buy", "bb_weight_buy",
                        "bb_period_sell", "bb_std_mult_sell", "bb_weight_sell"}
        else:
            # Estrategia desconocida: solo verificar que tenga al menos las universales
            specific = set()

        if specific:
            missing_specific = specific - params.keys()
            assert not missing_specific, (
                f"Faltan claves específicas para {estrategia}: {missing_specific}"
            )

    def test_chart_data_has_candles(self, results: dict) -> None:
        """chart_data debe tener una lista de velas no vacía."""
        candles = results["summary"]["chart_data"].get("candles", [])
        assert len(candles) > 0, "chart_data.candles está vacío"
        # Verificar estructura de cada vela
        required_candle_keys = {"ts", "open", "high", "low", "close", "volume"}
        for i, c in enumerate(candles):
            missing = required_candle_keys - c.keys()
            assert not missing, f"Vela {i} (ts={c.get('ts')}) sin claves: {missing}"

    def test_trade_history_is_list(self, results: dict) -> None:
        """trade_history debe ser una lista."""
        history = results["trade_history"]
        assert isinstance(history, list), "trade_history no es una lista"

    def test_trade_record_structure(self, results: dict) -> None:
        """Cada trade debe tener las claves mínimas."""
        required = {"ts", "type", "price", "ignorado"}
        for i, trade in enumerate(results["trade_history"]):
            missing = required - trade.keys()
            assert not missing, f"Trade {i} sin claves: {missing}"


# ══════════════════════════════════════════════════════════════════════════════
# CONSISTENCIA DE TIPOS
# ══════════════════════════════════════════════════════════════════════════════


class TestJsonTypeConsistency:
    """Verifica que los tipos de datos sean correctos."""

    def test_numeric_types(self, results: dict) -> None:
        """Campos numéricos clave deben ser números."""
        summary = results["summary"]
        numeric_fields = [
            ("saldo_inicial_usd", summary),
            ("usd_balance_final", summary),
            ("btc_balance_final", summary),
            ("btc_acumulado_total", summary),
            ("btc_en_posiciones_final", summary),
            ("portfolio_value_final", summary),
            ("pnl_pct", summary),
            ("sharpe", summary),
            ("max_drawdown_pct", summary),
            ("buy_hold_pnl_pct", summary),
            ("alpha_vs_bh", summary),
            ("total_compras", summary),
            ("total_ventas", summary),
            ("total_ignorados", summary),
            ("positions_count_final", summary),
        ]
        for field, parent in numeric_fields:
            val = parent[field]
            assert isinstance(val, (int, float)), (
                f"{field} debe ser numérico, es {type(val).__name__}: {val}"
            )
            assert math.isfinite(val) or math.isnan(val), (
                f"{field} no es finito: {val}"
            )

    def test_trade_types(self, results: dict) -> None:
        """Cada trade debe tener type en ('BUY', 'SELL') y price > 0 si no ignorado."""
        for trade in results["trade_history"]:
            assert trade["type"] in ("BUY", "SELL"), (
                f"Trade type inválido: {trade['type']}"
            )
            assert isinstance(trade["ts"], int), (
                f"Trade ts debe ser int, es {type(trade['ts']).__name__}"
            )
            if not trade["ignorado"]:
                assert trade["price"] > 0, (
                    f"Trade ejecutado con price <= 0: {trade}"
                )

    def test_candle_types(self, results: dict) -> None:
        """Cada vela debe tener tipos correctos."""
        for c in results["summary"]["chart_data"]["candles"]:
            assert isinstance(c["ts"], int), f"ts debe ser int: {c['ts']}"
            for field in ("open", "high", "low", "close", "volume"):
                val = c[field]
                assert isinstance(val, (int, float)), (
                    f"{field} debe ser numérico: {val}"
                )


# ══════════════════════════════════════════════════════════════════════════════
# CONSISTENCIA DE RELACIONES
# ══════════════════════════════════════════════════════════════════════════════


class TestJsonRelationshipConsistency:
    """Verifica relaciones entre campos."""

    def test_btc_balance_consistency(self, results: dict) -> None:
        """btc_balance_final debe ser >= btc_en_posiciones_final (puede haber BTC libre)."""
        summary = results["summary"]
        btc_balance = summary["btc_balance_final"]
        btc_en_pos = summary["btc_en_posiciones_final"]
        assert btc_balance >= btc_en_pos - 1e-10, (
            f"btc_balance ({btc_balance}) < btc_en_posiciones ({btc_en_pos})"
        )

    def test_btc_acumulado_consistency(self, results: dict) -> None:
        """btc_acumulado_total debe ser >= btc_en_posiciones_final."""
        summary = results["summary"]
        btc_acum = summary["btc_acumulado_total"]
        btc_en_pos = summary["btc_en_posiciones_final"]
        assert btc_acum >= btc_en_pos - 1e-10, (
            f"btc_acumulado ({btc_acum}) < btc_en_posiciones ({btc_en_pos})"
        )

    def test_trade_count_consistency(self, results: dict) -> None:
        """total_compras + total_ventas + total_ignorados debe coincidir con trade_history."""
        summary = results["summary"]
        total_from_summary = (summary["total_compras"]
                              + summary["total_ventas"]
                              + summary["total_ignorados"])
        total_from_history = len(results["trade_history"])
        assert total_from_summary == total_from_history, (
            f"Suma summary ({total_from_summary}) != len(history) ({total_from_history})"
        )

    def test_ignored_reasons_are_strings(self, results: dict) -> None:
        """Las claves de ignorados_por_motivo deben ser strings descriptivos."""
        reasons = results["summary"].get("ignorados_por_motivo", {})
        for reason, count in reasons.items():
            assert isinstance(reason, str) and len(reason) > 0, (
                f"Motivo de ignorado inválido: {reason!r}"
            )
            assert isinstance(count, int) and count >= 0, (
                f"Conteo de ignorado inválido para {reason}: {count}"
            )

    def test_candle_timestamps_are_ordered(self, results: dict) -> None:
        """Los timestamps de las velas deben estar en orden ascendente."""
        candles = results["summary"]["chart_data"]["candles"]
        timestamps = [c["ts"] for c in candles]
        for i in range(1, len(timestamps)):
            assert timestamps[i] > timestamps[i - 1], (
                f"Velas no ordenadas en ts={timestamps[i]} (anterior {timestamps[i-1]})"
            )

    def test_trade_timestamps_within_range(self, results: dict) -> None:
        """Los timestamps de trades deben estar dentro del rango de velas."""
        candles = results["summary"]["chart_data"]["candles"]
        if not candles:
            pytest.skip("No hay velas para verificar rango")
        min_ts = candles[0]["ts"]
        max_ts = candles[-1]["ts"] + 3600
        for trade in results["trade_history"]:
            assert min_ts <= trade["ts"] <= max_ts, (
                f"Trade ts={trade['ts']} fuera de rango [{min_ts}, {max_ts}]"
            )

    def test_commission_pct_is_reasonable(self, results: dict) -> None:
        """commission_pct debe ser un porcentaje razonable (0.001% - 1%)."""
        commission = results["summary"]["parametros"]["commission_pct"]
        assert 0.001 <= commission <= 1.0, (
            f"commission_pct fuera de rango: {commission}%"
        )

    def test_max_posiciones_is_positive(self, results: dict) -> None:
        """max_posiciones debe ser un entero positivo."""
        max_pos = results["summary"]["parametros"]["max_posiciones"]
        assert isinstance(max_pos, int) and max_pos >= 1, (
            f"max_posiciones inválido: {max_pos}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# CONSISTENCIA DE DATOS OPCIONALES (BB data, GTC stats)
# ══════════════════════════════════════════════════════════════════════════════


class TestJsonOptionalData:
    """Verifica datos opcionales como BB data y GTC stats."""

    def test_bb_data_if_present(self, results: dict) -> None:
        """Si hay bb_data, debe tener la estructura correcta."""
        chart_data = results["summary"].get("chart_data", {})
        bb_data = chart_data.get("bb_data")
        if bb_data is None:
            pytest.skip("No hay bb_data en este resultado")
        required_keys = {"upper", "upper_high", "lower", "lower_low", "timestamps"}
        assert required_keys.issubset(bb_data.keys()), (
            f"bb_data sin claves: {required_keys - bb_data.keys()}"
        )
        # Todos los arrays deben tener la misma longitud
        lengths = {k: len(v) for k, v in bb_data.items() if k != "timestamps"}
        if lengths:
            first_len = list(lengths.values())[0]
            for k, v in lengths.items():
                assert v == first_len, (
                    f"bb_data.{k} tiene longitud {v}, esperada {first_len}"
                )
            # timestamps puede tener longitud diferente (es el índice temporal)
            assert len(bb_data["timestamps"]) == first_len, (
                f"bb_data.timestamps longitud {len(bb_data['timestamps'])} != {first_len}"
            )

    def test_gtc_stats_if_present(self, results: dict) -> None:
        """Si hay gtc_stats, debe tener taker_fills y maker_fills."""
        chart_data = results["summary"].get("chart_data", {})
        gtc_stats = chart_data.get("gtc_stats")
        if gtc_stats is None:
            pytest.skip("No hay gtc_stats en este resultado")
        assert "taker_fills" in gtc_stats, "gtc_stats sin taker_fills"
        assert "maker_fills" in gtc_stats, "gtc_stats sin maker_fills"
        assert isinstance(gtc_stats["taker_fills"], int), "taker_fills debe ser int"
        assert isinstance(gtc_stats["maker_fills"], int), "maker_fills debe ser int"


if __name__ == "__main__":
    from tests._direct_runner import run_current_test_file
    raise SystemExit(run_current_test_file(__file__))