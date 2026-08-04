"""
test_hl_testnet_order_roundtrip.py — Test REAL contra Hyperliquid Testnet
==========================================================================
Ejecuta operaciones reales en Hyperliquid Testnet con confirmación del usuario.

Flujo completo:
  1. Confirmación explícita del usuario
  2. Verificar saldo ≥ $10
  3. Obtener precio actual
  4. Consultar wallet (balance)
  5. ─── RONDA 1: MARKET BUY ($13) ───
  6.   Comprar $13 a mercado → verificar posición abierta
  7. ─── RONDA 2: LIMIT GTC (precios lejanos -20%/+20%) ───
  8.   Obtener bid/ask del order book (L2 snapshot)
  9.   Calcular precio lejano: BUY = bid * 0.8, SELL = ask * 1.2
  10.  Enviar LIMIT GTC BUY → verificar resting → cancelar
  11.  Enviar LIMIT GTC SELL → verificar resting → cancelar
  12. ─── RONDA 3: LIMIT POST-ONLY (Alo) ───
  13.  Repetir pasos 8-11 con order_type Alo (post-only)
  14. ─── RONDA 4: MARKET SELL ───
  15.  Cerrar la posición abierta en R1 → verificar posición cerrada
  16. Resumen final

REQUISITOS:
  - .env: HL_TESTNET_SECRET_KEY, HL_TESTNET_ACCOUNT_ADDRESS, HL_SYMBOL=BTC
  - Saldo disponible en Hyperliquid Testnet
  - Confirmación explícita del usuario

MARCADO: @pytest.mark.requires_network @pytest.mark.requires_keys
"""
from __future__ import annotations

import math
import os
import sys
import time
import pytest

# Asegurar que el raíz del proyecto esté en el path para ejecución directa
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tests.environments.real_operation.conftest_real_operation import (
    MIN_ORDER_USD, MARKET_ORDER_USD, WAIT_AFTER_BUY, WAIT_AFTER_SELL,
    require_confirmation, log_event, print_separator, print_summary, get_event_log,
)


class TestHLTestnetOrderRoundtrip:
    """Test de operación real contra Hyperliquid Testnet."""

    @pytest.fixture(scope="class")
    def clients(self):
        """Crea los clientes de Hyperliquid Testnet."""
        require_confirmation("Hyperliquid Testnet", "Full Roundtrip (Market + Limit GTC + Post-Only)")
        from support.secrets import secrets, CredentialNotFound
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        from eth_account import Account

        try:
            secret_key = secrets("HL_TESTNET_SECRET_KEY")
            account_address = secrets("HL_TESTNET_ACCOUNT_ADDRESS")
            symbol = secrets("HL_SYMBOL", "BTC").upper().replace("USDT", "").replace("USDC", "")
        except CredentialNotFound as e:
            pytest.skip(f"Credencial no encontrada: {e}")

        if not secret_key or not account_address:
            pytest.skip("HL_TESTNET_SECRET_KEY o HL_TESTNET_ACCOUNT_ADDRESS no configurados en .env")

        api_url = constants.TESTNET_API_URL
        wallet_eth = Account.from_key(secret_key)
        exchange = Exchange(wallet_eth, api_url, account_address=account_address)
        info = Info(api_url, skip_ws=True)

        log_event("CLIENTES_INICIALIZADOS", exchange="HL Testnet", symbol=symbol)
        return exchange, info, account_address, symbol

    @pytest.fixture(scope="class")
    def sz_decimals(self, clients):
        """Obtiene szDecimals del asset."""
        _, info, _, symbol = clients
        meta = info.meta()
        for asset in meta.get("universe", []):
            if asset.get("name") == symbol:
                return int(asset["szDecimals"])
        pytest.fail(f"No se encontró szDecimals para {symbol}")

    def test_full_roundtrip(self, clients, sz_decimals):
        """Test completo: market buy → limit GTC → limit post-only → market sell."""
        exchange, info, account_address, symbol = clients

        # ── Paso 1: Obtener precio actual ─────────────────────────────────
        print_separator("OBTENER PRECIO ACTUAL")
        all_mids = info.all_mids()
        current_price = float(all_mids.get(symbol, 0.0))
        assert current_price > 0, f"No se pudo obtener precio para {symbol}"
        log_event("PRECIO_ACTUAL", symbol=symbol, price=current_price)

        # ── Paso 2: Consultar wallet (balance) ────────────────────────────
        print_separator("CONSULTAR WALLET")
        user_state = info.user_state(account_address)
        initial_balance = float(user_state.get("marginSummary", {}).get("accountValue", 0.0))
        withdrawable = float(user_state.get("withdrawable", 0.0))
        log_event("BALANCE_INICIAL", account_value=initial_balance, withdrawable=withdrawable)
        assert initial_balance >= MIN_ORDER_USD, (
            f"Saldo insuficiente: ${initial_balance:.2f}. Se necesitan >${MIN_ORDER_USD}"
        )

        # ══════════════════════════════════════════════════════════════════
        # RONDA 1: MARKET BUY ($13 para cubrir desvíos de precio)
        # ══════════════════════════════════════════════════════════════════
        print_separator("RONDA 1: MARKET BUY")

        qty = round(MARKET_ORDER_USD / current_price, sz_decimals)
        notional = qty * current_price
        log_event("MARKET_BUY", qty=qty, notional=round(notional, 2), usd_amount=MARKET_ORDER_USD)

        exchange.update_leverage(1, symbol, is_cross=True)
        resp_buy = exchange.market_open(symbol, is_buy=True, sz=qty, slippage=0.05)
        assert resp_buy.get("status") == "ok", f"Market BUY falló: {resp_buy}"
        buy_oid = _extract_oid(resp_buy)
        log_event("MARKET_BUY_OK", qty=qty, oid=buy_oid,
                  response=str(resp_buy.get("response", ""))[:200])

        time.sleep(WAIT_AFTER_BUY)

        # Verificar posición abierta
        user_state = info.user_state(account_address)
        pos = self._get_position(user_state, symbol)
        assert pos is not None, "No se encontró posición abierta tras MARKET BUY"
        szi = float(pos.get("szi", 0.0))
        assert szi > 0, f"szi={szi} debería ser positivo (LONG)"
        log_event("POSICION_ABIERTA_MARKET", szi=szi, entry_px=pos.get("entryPx"))

        qty_market = qty

        # ══════════════════════════════════════════════════════════════════
        # RONDA 2: LIMIT GTC (precios lejanos -20%/+20% → no se ejecutan)
        # ══════════════════════════════════════════════════════════════════
        print_separator("RONDA 2: ÓRDENES LIMIT GTC — PRECIOS LEJANOS (-20%/+20%)")

        # Obtener bid/ask del L2 snapshot
        l2 = info.l2_snapshot(symbol)
        best_bid = float(l2['levels'][0][0]['px'])  # bids
        best_ask = float(l2['levels'][1][0]['px'])  # asks
        mid_price = (best_bid + best_ask) / 2

        # Precios lejanos -20%/+20% para garantizar que NO se ejecuten
        # Usar round_px para respetar tick size del exchange
        far_buy_price = _round_px(best_bid * 0.8)
        far_sell_price = _round_px(best_ask * 1.2)

        # Cantidad calculada sobre el precio de la orden para garantizar notional ≥ $10
        # Usar ceil para redondear hacia arriba y asegurar que notional ≥ $10
        qty_limit_buy = _ceil_sz(MIN_ORDER_USD / far_buy_price, sz_decimals)
        qty_limit_sell = min(_ceil_sz(MIN_ORDER_USD / far_sell_price, sz_decimals), _ceil_sz(szi, sz_decimals))
        log_event("ORDER_BOOK", bid=best_bid, ask=best_ask, mid=mid_price,
                  far_buy=far_buy_price, far_sell=far_sell_price,
                  qty_buy=qty_limit_buy, qty_sell=qty_limit_sell)

        # --- LIMIT GTC BUY (far low) → debe quedar como resting (no ejecutada) ---
        resp_limit_buy = exchange.order(
            name=symbol, is_buy=True, sz=qty_limit_buy,
            limit_px=far_buy_price, order_type={"limit": {"tif": "Gtc"}},
            reduce_only=False,
        )
        assert resp_limit_buy.get("status") == "ok", f"Limit GTC BUY falló: {resp_limit_buy}"
        buy_oid_r2, buy_status = _extract_order_status(resp_limit_buy)
        assert buy_status == "resting", (
            f"Limit GTC BUY debería ser resting, pero es {buy_status}: {resp_limit_buy}"
        )
        log_event("LIMIT_GTC_BUY_RESTING", oid=buy_oid_r2, price=far_buy_price)

        # Cancelar la orden
        cancel_resp = exchange.cancel(symbol, buy_oid_r2)
        assert cancel_resp.get("status") == "ok", f"Cancelación LIMIT GTC BUY falló: {cancel_resp}"
        log_event("LIMIT_GTC_BUY_CANCELED", oid=buy_oid_r2)

        # --- LIMIT GTC SELL (far high) → debe quedar como resting ---
        resp_limit_sell = exchange.order(
            name=symbol, is_buy=False, sz=qty_limit_sell,
            limit_px=far_sell_price, order_type={"limit": {"tif": "Gtc"}},
            reduce_only=False,
        )
        assert resp_limit_sell.get("status") == "ok", f"Limit GTC SELL falló: {resp_limit_sell}"
        sell_oid_r2, sell_status = _extract_order_status(resp_limit_sell)
        assert sell_status == "resting", (
            f"Limit GTC SELL debería ser resting, pero es {sell_status}: {resp_limit_sell}"
        )
        log_event("LIMIT_GTC_SELL_RESTING", oid=sell_oid_r2, price=far_sell_price)

        # Cancelar
        cancel_resp = exchange.cancel(symbol, sell_oid_r2)
        assert cancel_resp.get("status") == "ok", f"Cancelación LIMIT GTC SELL falló: {cancel_resp}"
        log_event("LIMIT_GTC_SELL_CANCELED", oid=sell_oid_r2)

        # ══════════════════════════════════════════════════════════════════
        # RONDA 3: LIMIT POST-ONLY (Alo) — precios lejanos -20%/+20%
        # ══════════════════════════════════════════════════════════════════
        print_separator("RONDA 3: ÓRDENES LIMIT POST-ONLY — PRECIOS LEJANOS (-20%/+20%)")

        # Obtener bid/ask actualizados
        l2 = info.l2_snapshot(symbol)
        best_bid = float(l2['levels'][0][0]['px'])
        best_ask = float(l2['levels'][1][0]['px'])
        mid_price = (best_bid + best_ask) / 2

        far_buy_price_po = _round_px(best_bid * 0.8)
        far_sell_price_po = _round_px(best_ask * 1.2)

        # Cantidad calculada sobre el precio de la orden
        qty_po_buy = _ceil_sz(MIN_ORDER_USD / far_buy_price_po, sz_decimals)
        qty_po_sell = min(_ceil_sz(MIN_ORDER_USD / far_sell_price_po, sz_decimals), _ceil_sz(szi, sz_decimals))
        log_event("POST_ONLY_ORDER_BOOK", bid=best_bid, ask=best_ask,
                  far_buy=far_buy_price_po, far_sell=far_sell_price_po,
                  qty_buy=qty_po_buy, qty_sell=qty_po_sell)

        # --- POST-ONLY BUY (Alo, far low) → debe quedar como resting ---
        resp_po_buy = exchange.order(
            name=symbol, is_buy=True, sz=qty_po_buy,
            limit_px=far_buy_price_po, order_type={"limit": {"tif": "Alo"}},
            reduce_only=False,
        )
        assert resp_po_buy.get("status") == "ok", f"Post-Only BUY falló: {resp_po_buy}"
        po_buy_oid, po_buy_status = _extract_order_status(resp_po_buy)
        assert po_buy_status == "resting", (
            f"Post-Only BUY debería ser resting, pero es {po_buy_status}: {resp_po_buy}"
        )
        log_event("POST_ONLY_BUY_RESTING", oid=po_buy_oid, price=far_buy_price_po)

        # Cancelar
        cancel_resp = exchange.cancel(symbol, po_buy_oid)
        assert cancel_resp.get("status") == "ok", f"Cancelación POST-ONLY BUY falló: {cancel_resp}"
        log_event("POST_ONLY_BUY_CANCELED", oid=po_buy_oid)

        # --- POST-ONLY SELL (Alo, far high) → debe quedar como resting ---
        resp_po_sell = exchange.order(
            name=symbol, is_buy=False, sz=qty_po_sell,
            limit_px=far_sell_price_po, order_type={"limit": {"tif": "Alo"}},
            reduce_only=False,
        )
        assert resp_po_sell.get("status") == "ok", f"Post-Only SELL falló: {resp_po_sell}"
        po_sell_oid, po_sell_status = _extract_order_status(resp_po_sell)
        assert po_sell_status == "resting", (
            f"Post-Only SELL debería ser resting, pero es {po_sell_status}: {resp_po_sell}"
        )
        log_event("POST_ONLY_SELL_RESTING", oid=po_sell_oid, price=far_sell_price_po)

        # Cancelar
        cancel_resp = exchange.cancel(symbol, po_sell_oid)
        assert cancel_resp.get("status") == "ok", f"Cancelación POST-ONLY SELL falló: {cancel_resp}"
        log_event("POST_ONLY_SELL_CANCELED", oid=po_sell_oid)

        # ══════════════════════════════════════════════════════════════════
        # RONDA 4: MARKET SELL — cierra la posición abierta en R1
        # ══════════════════════════════════════════════════════════════════
        print_separator("RONDA 4: MARKET SELL — CIERRE DE POSICIÓN")

        # Verificar que aún tenemos la posición abierta
        user_state = info.user_state(account_address)
        pos = self._get_position(user_state, symbol)
        assert pos is not None, "No se encontró posición antes de MARKET SELL"
        szi_before_sell = float(pos.get("szi", 0.0))
        assert szi_before_sell > 0, f"szi={szi_before_sell} debería ser positivo"
        log_event("POSICION_ANTES_DE_SELL", szi=szi_before_sell)

        # Cerrar la posición con market sell
        resp_sell = exchange.market_close(symbol, sz=None, slippage=0.05)
        assert resp_sell.get("status") == "ok", f"Market SELL falló: {resp_sell}"
        sell_oid_r4 = _extract_oid(resp_sell)
        log_event("MARKET_SELL_OK", oid=sell_oid_r4,
                  response=str(resp_sell.get("response", ""))[:200])

        time.sleep(WAIT_AFTER_SELL)

        # Verificar posición cerrada
        user_state = info.user_state(account_address)
        pos = self._get_position(user_state, symbol)
        szi_final = float(pos.get("szi", 0.0)) if pos else 0.0
        assert abs(szi_final) < 1e-6, f"Posición no cerrada tras MARKET SELL: szi={szi_final}"
        log_event("POSICION_CERRADA_MARKET", szi_final=szi_final)

        # ── Resumen final ─────────────────────────────────────────────────
        user_state = info.user_state(account_address)
        final_balance = float(user_state.get("marginSummary", {}).get("accountValue", 0.0))
        print_summary(initial_balance, final_balance, len(get_event_log()))

    # ══════════════════════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _get_position(user_state: dict, symbol: str) -> dict | None:
        """Busca la posición de un símbolo en el user_state."""
        for pos in user_state.get("assetPositions", []):
            p = pos.get("position", {})
            if p.get("coin") == symbol:
                return p
        return None


def _ceil_sz(qty: float, decimals: int) -> float:
    """
    Redondea un tamaño hacia arriba con la precisión dada.
    Garantiza que notional = qty * price >= MIN_ORDER_USD.
    """
    factor = 10 ** decimals
    return math.ceil(qty * factor) / factor


def _round_px(px: float) -> float:
    """
    Redondea un precio a 5 cifras significativas para respetar tick size.
    Según la guía del SDK de Hyperliquid: round(float(f"{px:.5g}"), 6)
    """
    return round(float(f"{px:.5g}"), 6)


def _extract_oid(response: dict) -> int | None:
    """Extrae el order ID de la respuesta de Hyperliquid."""
    try:
        return int(response.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("resting", {}).get("oid", 0))
    except (TypeError, ValueError, IndexError, KeyError, AttributeError):
        try:
            return int(response.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("filled", {}).get("oid", 0))
        except (TypeError, ValueError, IndexError, KeyError, AttributeError):
            return None


def _extract_order_status(response: dict) -> tuple[int | None, str | None]:
    """
    Extrae el oid y el tipo de status de la respuesta de Hyperliquid.
    Retorna (oid, status_type) donde status_type es 'resting', 'filled', o None.
    """
    try:
        statuses = response.get("response", {}).get("data", {}).get("statuses", [])
        if not statuses:
            return None, None
        entry = statuses[0]
        if "resting" in entry:
            return int(entry["resting"]["oid"]), "resting"
        elif "filled" in entry:
            return int(entry["filled"]["oid"]), "filled"
        return None, None
    except (TypeError, ValueError, IndexError, KeyError, AttributeError):
        return None, None


if __name__ == '__main__':
    pytest.main([__file__, '-s', '-v'])
