"""
tests/actors/test_wallet.py — Tests para Wallet
================================================
Cubre: actors/wallet.py — MemoryWallet y JSONWallet.
Incluye slot redistribution, realized PnL, slot factor, reset, snapshot.
Soporta LONG y SHORT con modelo unificado (slots compartidos).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from support.types import Candle, PositionDirection
from actors.wallet import MemoryWallet, JSONWallet, TradeRecord


class TestMemoryWallet:
    """Tests básicos de MemoryWallet."""

    def test_init_default_values(self):
        """MemoryWallet debe inicializarse con los valores correctos."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        assert wallet.get_usd_balance() == 1000.0
        assert wallet.positions_count == 0

    def test_init_custom_values(self):
        """MemoryWallet debe aceptar valores personalizados."""
        wallet = MemoryWallet(usd_initial=5000.0, max_posiciones=5, slot_factor=2.0)
        assert wallet.get_usd_balance() == 5000.0

    def test_get_slot_usd(self):
        """get_slot_usd debe retornar un valor positivo."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        slot = wallet.get_slot_usd()
        assert slot > 0
        assert slot <= wallet.get_usd_balance()

    def test_get_btc_por_venta_returns_zero_when_empty(self):
        """Sin posiciones, get_btc_por_venta debe retornar 0."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        assert wallet.get_btc_por_venta() == 0.0

    def test_update_buy_increases_positions(self):
        """Una compra debe incrementar positions_count."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        trade = TradeRecord(ts=1_700_000_000, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                            direction=PositionDirection.LONG)
        wallet.update(trade)
        assert wallet.positions_count == 1

    def test_update_buy_decreases_usd(self):
        """Una compra debe decrementar el saldo USDT."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        trade = TradeRecord(ts=1_700_000_000, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                            direction=PositionDirection.LONG)
        wallet.update(trade)
        assert wallet.get_usd_balance() == 900.0

    def test_update_sell_decreases_positions(self):
        """Una venta debe decrementar positions_count."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        buy = TradeRecord(ts=1_700_000_000, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                          direction=PositionDirection.LONG)
        wallet.update(buy)
        sell = TradeRecord(ts=1_700_003_600, side="SELL", price=110.0, btc_sold=1.0, usd_received=110.0,
                           direction=PositionDirection.LONG)
        wallet.update(sell)
        assert wallet.positions_count == 0

    def test_portfolio_value_long(self):
        """portfolio_value debe calcular el valor total del portfolio LONG."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        buy = TradeRecord(ts=1_700_000_000, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                          direction=PositionDirection.LONG)
        wallet.update(buy)
        value = wallet.portfolio_value(current_price=110.0)
        # usdt restante (900) + btc (1.0) * market_price (110) = 1010
        assert abs(value - 1010.0) < 0.01

    def test_portfolio_value_short(self):
        """portfolio_value debe calcular el valor total del portfolio SHORT."""
        wallet = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        # Abrir short: vender 0.1 BTC a 65000
        sell = TradeRecord(ts=1, side="SELL", price=65000.0, btc_sold=0.1, usd_received=6500.0,
                           direction=PositionDirection.SHORT)
        wallet.update(sell)
        # portfolio = 16500 USD - 0.1 * 65000 = 16500 - 6500 = 10000
        pv = wallet.portfolio_value(65000.0)
        assert abs(pv - 10000.0) < 0.01

    def test_get_btc_por_venta_after_buy(self):
        """Después de una compra, get_btc_por_venta debe retornar el BTC del slot."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        buy = TradeRecord(ts=1_700_000_000, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                          direction=PositionDirection.LONG)
        wallet.update(buy)
        assert abs(wallet.get_btc_por_venta() - 1.0) < 0.001

    def test_current_direction_none(self):
        """Sin posiciones, current_direction debe ser NONE."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        assert wallet.current_direction == PositionDirection.NONE

    def test_current_direction_long(self):
        """Con posición LONG, current_direction debe ser LONG."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        buy = TradeRecord(ts=1, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                          direction=PositionDirection.LONG)
        wallet.update(buy)
        assert wallet.current_direction == PositionDirection.LONG

    def test_current_direction_short(self):
        """Con posición SHORT, current_direction debe ser SHORT."""
        wallet = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        sell = TradeRecord(ts=1, side="SELL", price=65000.0, btc_sold=0.1, usd_received=6500.0,
                           direction=PositionDirection.SHORT)
        wallet.update(sell)
        assert wallet.current_direction == PositionDirection.SHORT

    # ══════════════════════════════════════════════════════════════════════════
    # NUEVOS TESTS: Slot redistribution, realized PnL, slot factor, reset, etc.
    # ══════════════════════════════════════════════════════════════════════════

    def test_slot_redistribution_after_sell(self):
        """Tras una venta con ganancia, los slots libres deben redistribuirse con el nuevo saldo."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        # Compra: gasta 100 USDT, obtiene 1 BTC
        buy = TradeRecord(ts=1_700_000_000, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                          direction=PositionDirection.LONG)
        wallet.update(buy)
        # Vende a 110 (ganancia de 10 USDT)
        sell = TradeRecord(ts=1_700_003_600, side="SELL", price=110.0, btc_sold=1.0, usd_received=110.0,
                           direction=PositionDirection.LONG)
        wallet.update(sell)
        # Ahora tiene 1010 USDT y 0 posiciones → 3 slots libres
        assert wallet.positions_count == 0
        assert abs(wallet.get_usd_balance() - 1010.0) < 0.01
        # Cada slot debe ser ~336.67 (1010/3 con factor=1.0)
        slot = wallet.get_slot_usd()
        assert abs(slot - 1010.0 / 3) < 1.0

    def test_realized_pnl_on_sell(self):
        """Una venta debe calcular el realized PnL correctamente."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        buy = TradeRecord(ts=1_700_000_000, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                          direction=PositionDirection.LONG)
        wallet.update(buy)
        sell = TradeRecord(ts=1_700_003_600, side="SELL", price=110.0, btc_sold=1.0, usd_received=110.0,
                           direction=PositionDirection.LONG)
        wallet.update(sell)
        # realized_pnl = (110 - 100) * 1.0 = 10.0
        assert sell.realized_pnl is not None
        assert abs(sell.realized_pnl - 10.0) < 0.001

    def test_realized_pnl_negative(self):
        """Una venta con pérdida debe reflejar PnL negativo."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        buy = TradeRecord(ts=1_700_000_000, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                          direction=PositionDirection.LONG)
        wallet.update(buy)
        sell = TradeRecord(ts=1_700_003_600, side="SELL", price=90.0, btc_sold=1.0, usd_received=90.0,
                           direction=PositionDirection.LONG)
        wallet.update(sell)
        assert sell.realized_pnl is not None
        assert sell.realized_pnl < 0
        assert abs(sell.realized_pnl - (-10.0)) < 0.001

    def test_slot_factor_progressive(self):
        """Con slot_factor > 1.0, los slots deben ser progresivos (geométricos)."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3, slot_factor=2.0)
        # 3 slots libres con factor=2.0: pesos = 1, 2, 4 → suma=7
        # slot[0] = 1000 * 1/7 ≈ 142.86
        slot0 = wallet.get_slot_usd()
        assert abs(slot0 - 1000.0 / 7) < 1.0

    def test_multiple_buys_and_sells(self):
        """Múltiples compras y ventas deben mantener el estado consistente (modelo agregado)."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)

        # Compra 1: gasta 100 USDT, compra 1 BTC a $100
        wallet.update(TradeRecord(ts=1, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                                  direction=PositionDirection.LONG))
        assert wallet._slots_used == 1

        # Compra 2: gasta 100 USDT, compra 0.90909 BTC a $110
        wallet.update(TradeRecord(ts=2, side="BUY", price=110.0, usd_spent=100.0, btc_bought=0.90909,
                                  direction=PositionDirection.LONG))
        assert wallet._slots_used == 2

        # Vende slot 2: BTC total = 1.90909, cada slot = 0.954545
        wallet.update(TradeRecord(ts=3, side="SELL", price=120.0, btc_sold=0.954545, usd_received=114.5454,
                                  direction=PositionDirection.LONG))
        assert wallet._slots_used == 1

        # Vende slot 1: 0.954545 BTC a $130
        wallet.update(TradeRecord(ts=4, side="SELL", price=130.0, btc_sold=0.954545, usd_received=124.09085,
                                  direction=PositionDirection.LONG))
        assert wallet._slots_used == 0

        # Saldo: 1000 - 100 - 100 + 114.5454 + 124.09085 ≈ 1038.636
        assert abs(wallet.get_usd_balance() - 1038.636) < 0.1

    def test_reset_restores_initial_state(self):
        """reset() debe restaurar el estado inicial."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        wallet.update(TradeRecord(ts=1, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                                  direction=PositionDirection.LONG))
        wallet.update(TradeRecord(ts=2, side="SELL", price=110.0, btc_sold=1.0, usd_received=110.0,
                                  direction=PositionDirection.LONG))
        wallet.reset()
        assert wallet.get_usd_balance() == 1000.0
        assert wallet.positions_count == 0
        assert wallet.get_btc_por_venta() == 0.0

    def test_snapshot_contains_all_keys(self):
        """snapshot() debe contener todas las claves esperadas."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        wallet.update(TradeRecord(ts=1, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                                  direction=PositionDirection.LONG))
        snap = wallet.snapshot(current_price=110.0)
        expected_keys = {"usd_balance", "btc_acumulado_total", "btc_en_posiciones",
                         "positions_count", "precio_promedio_posiciones", "current_direction",
                         "slot_usd", "btc_por_venta", "usable_slots", "btc_por_posicion",
                         "slot_factor", "portfolio_value", "pnl_pct"}
        assert expected_keys.issubset(snap.keys())
        assert snap["positions_count"] == 1
        assert snap["portfolio_value"] > 0
        assert snap["current_direction"] == "LONG"

    def test_ignored_trade_does_not_affect_state(self):
        """Un trade ignorado no debe modificar el estado de la wallet."""
        wallet = MemoryWallet(usd_initial=1000.0, max_posiciones=3)
        ignored = TradeRecord(ts=1, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                              ignored=True, ignore_reason="risk_check")
        wallet.update(ignored)
        assert wallet.get_usd_balance() == 1000.0
        assert wallet.positions_count == 0

    # ══════════════════════════════════════════════════════════════════════════
    # TESTS SHORT (modelo unificado)
    # ══════════════════════════════════════════════════════════════════════════

    def test_short_open_increases_btc(self):
        """Abrir short debe aumentar btc_en_posiciones (deuda)."""
        wallet = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        trade = TradeRecord(ts=1, side="SELL", price=65000.0,
                            btc_sold=0.1, usd_received=6500.0,
                            direction=PositionDirection.SHORT)
        wallet.update(trade)
        assert abs(wallet.btc_en_posiciones() - 0.1) < 1e-10

    def test_short_open_increases_usd(self):
        """Abrir short: el colateral se congela, el balance libre se reduce."""
        wallet = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        trade = TradeRecord(ts=1, side="SELL", price=65000.0,
                            btc_sold=0.1, usd_received=6500.0,
                            direction=PositionDirection.SHORT)
        wallet.update(trade)
        # Balance total = 10000 + 6500 - 6500(colateral) = 10000
        assert abs(wallet.get_usd_balance() - 10000.0) < 0.01
        # Balance libre = 10000 - 6500(colateral) = 3500
        assert abs(wallet.get_usd_free() - 3500.0) < 0.01
        # Colateral congelado = 6500
        assert abs(wallet.get_usd_short_collateral() - 6500.0) < 0.01

    def test_short_open_sets_avg_entry_price(self):
        """Abrir short debe establecer el precio promedio de entrada."""
        wallet = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        trade = TradeRecord(ts=1, side="SELL", price=65000.0,
                            btc_sold=0.1, usd_received=6500.0,
                            direction=PositionDirection.SHORT)
        wallet.update(trade)
        assert abs(wallet.precio_promedio_posiciones() - 65000.0) < 0.01

    def test_short_close_reduces_btc(self):
        """Cerrar short debe reducir btc_en_posiciones a 0."""
        wallet = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        # Abrir short: vender 0.1 BTC a 65000
        wallet.update(TradeRecord(ts=1, side="SELL", price=65000.0,
                                   btc_sold=0.1, usd_received=6500.0,
                                   direction=PositionDirection.SHORT))
        # Cerrar short: comprar 0.1 BTC a 64000
        wallet.update(TradeRecord(ts=2, side="BUY", price=64000.0,
                                   btc_bought=0.1, usd_spent=6400.0,
                                   direction=PositionDirection.SHORT))
        assert abs(wallet.btc_en_posiciones()) < 1e-10

    def test_short_close_realized_pnl(self):
        """Cerrar short debe calcular PnL positivo (vendiste caro, compraste barato)."""
        wallet = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        wallet.update(TradeRecord(ts=1, side="SELL", price=65000.0,
                                   btc_sold=0.1, usd_received=6500.0,
                                   direction=PositionDirection.SHORT))
        close_trade = TradeRecord(ts=2, side="BUY", price=64000.0,
                                   btc_bought=0.1, usd_spent=6400.0,
                                   direction=PositionDirection.SHORT)
        wallet.update(close_trade)
        # PnL = (65000 - 64000) * 0.1 = 100
        assert close_trade.realized_pnl is not None
        assert abs(close_trade.realized_pnl - 100.0) < 0.01

    def test_short_multiple_opens(self):
        """Múltiples aperturas short deben calcular promedio ponderado."""
        wallet = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        # Short 1: 0.1 BTC a 60000
        wallet.update(TradeRecord(ts=1, side="SELL", price=60000.0,
                                   btc_sold=0.1, usd_received=6000.0,
                                   direction=PositionDirection.SHORT))
        # Short 2: 0.1 BTC a 70000
        wallet.update(TradeRecord(ts=2, side="SELL", price=70000.0,
                                   btc_sold=0.1, usd_received=7000.0,
                                   direction=PositionDirection.SHORT))
        # Precio promedio = (60000*0.1 + 70000*0.1) / 0.2 = 65000
        assert abs(wallet.precio_promedio_posiciones() - 65000.0) < 0.01
        assert abs(wallet.btc_en_posiciones() - 0.2) < 1e-10

    def test_short_reset(self):
        """reset() debe limpiar el estado short."""
        wallet = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        wallet.update(TradeRecord(ts=1, side="SELL", price=65000.0,
                                   btc_sold=0.1, usd_received=6500.0,
                                   direction=PositionDirection.SHORT))
        wallet.reset()
        assert wallet.btc_en_posiciones() == 0.0
        assert wallet.positions_count == 0
        assert wallet.current_direction == PositionDirection.NONE

    def test_short_then_long_opens_new_position(self):
        """Cerrar short y abrir long debe funcionar correctamente."""
        wallet = MemoryWallet(usd_initial=10000.0, max_posiciones=3)
        # Abrir short
        wallet.update(TradeRecord(ts=1, side="SELL", price=65000.0,
                                   btc_sold=0.1, usd_received=6500.0,
                                   direction=PositionDirection.SHORT))
        assert wallet.current_direction == PositionDirection.SHORT
        # Cerrar short (comprar BTC)
        wallet.update(TradeRecord(ts=2, side="BUY", price=64000.0,
                                   btc_bought=0.1, usd_spent=6400.0,
                                   direction=PositionDirection.SHORT))
        assert wallet.current_direction == PositionDirection.NONE
        # Abrir long
        wallet.update(TradeRecord(ts=3, side="BUY", price=63000.0,
                                   usd_spent=100.0, btc_bought=0.001587,
                                   direction=PositionDirection.LONG))
        assert wallet.current_direction == PositionDirection.LONG


class TestJSONWallet:
    """Tests básicos de JSONWallet."""

    def test_init_sets_json_path(self):
        """JSONWallet debe aceptar json_path."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json_path = f.name
        try:
            wallet = JSONWallet(usd_initial=1000.0, max_posiciones=3, json_path=json_path)
            assert wallet.get_usd_balance() == 1000.0
        finally:
            os.unlink(json_path)

    def test_flush_writes_summary(self):
        """flush() debe escribir datos en el JSON."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json_path = f.name
        try:
            wallet = JSONWallet(usd_initial=1000.0, max_posiciones=3, json_path=json_path)
            summary = {"estrategia": "test", "pnl_pct": 5.0}
            wallet.flush(summary)
            with open(json_path) as f:
                data = json.load(f)
            assert data["summary"]["estrategia"] == "test"
        finally:
            os.unlink(json_path)

    def test_get_usd_balance_returns_initial(self):
        """get_usd_balance debe retornar el saldo inicial."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json_path = f.name
        try:
            wallet = JSONWallet(usd_initial=2000.0, max_posiciones=3, json_path=json_path)
            assert wallet.get_usd_balance() == 2000.0
        finally:
            os.unlink(json_path)

    def test_trade_log_tracks_trades(self):
        """El trade_log de JSONWallet debe registrar las operaciones."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json_path = f.name
        try:
            wallet = JSONWallet(usd_initial=1000.0, max_posiciones=3, json_path=json_path)
            wallet.update(TradeRecord(ts=1, side="BUY", price=100.0, usd_spent=100.0, btc_bought=1.0,
                                      direction=PositionDirection.LONG))
            wallet.update(TradeRecord(ts=2, side="SELL", price=110.0, btc_sold=1.0, usd_received=110.0,
                                      direction=PositionDirection.LONG))
            log = wallet.get_trade_log()
            assert len(log) == 2
            assert log[0]["type"] == "BUY"
            assert log[1]["type"] == "SELL"
        finally:
            os.unlink(json_path)


if __name__ == "__main__":
    from tests._direct_runner import run_current_test_file

    raise SystemExit(run_current_test_file(__file__))