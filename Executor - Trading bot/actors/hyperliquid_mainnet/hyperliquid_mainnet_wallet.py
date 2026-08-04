"""
actors/hyperliquid_mainnet/hyperliquid_mainnet_wallet.py — Billetera sincronizada con Hyperliquid Mainnet
═══════════════════════════════════════════════════════════════════════════════════════════════════════════
Implementación completa para el entorno Hyperliquid Mainnet (Perps).
Usa API mainnet y claves HL_* desde .env.
"""
from __future__ import annotations

import aiohttp
from hyperliquid.utils import constants
from actors.wallet import JSONWallet, AsyncWallet, AggregatePosition
from support.logger import get_logger

log = get_logger("hyperliquid_mainnet_wallet")

MAINNET_API = constants.MAINNET_API_URL


class HyperliquidWallet(JSONWallet, AsyncWallet):
    """Billetera sincronizada con Hyperliquid Mainnet."""

    def __init__(self, usd_initial: float, max_posiciones: int,
                 json_path: str, account_address: str,
                 slot_factor: float = 1.0) -> None:
        super().__init__(
            usd_initial,
            max_posiciones,
            json_path,
            slot_factor=slot_factor,
            environment="hyperliquid_mainnet",
            collateral_currency="USDC",
        )
        self._account_address = account_address
        self._info = None
        self._last_account_value: float | None = None
        log.info("HyperliquidWallet Mainnet inicializado", address=account_address)

    def _get_info(self):
        if self._info is None:
            from hyperliquid.info import Info
            self._info = Info(MAINNET_API, skip_ws=True)
        return self._info

    @classmethod
    def from_account(cls, max_posiciones: int, json_path: str,
                     slot_factor: float = 1.0) -> "HyperliquidWallet":
        """Crea la wallet sincronizando el saldo desde Hyperliquid mainnet."""
        from support.secrets import secrets
        account_address = secrets("HL_ACCOUNT_ADDRESS")

        from hyperliquid.info import Info
        info = Info(MAINNET_API, skip_ws=True)
        user_state = info.user_state(account_address)

        usdc_balance = 0.0
        margin_summary = user_state.get("marginSummary", {})
        if margin_summary:
            usdc_balance = float(margin_summary.get("accountValue", 0.0))
        else:
            cross = user_state.get("crossMarginSummary", {})
            usdc_balance = float(cross.get("accountValue", 0.0))

        log.info("Cuenta Hyperliquid Mainnet sincronizada",
                 address=account_address, usdc=usdc_balance)

        wallet = cls(
            usd_initial=0.0,
            max_posiciones=max_posiciones,
            json_path=json_path,
            account_address=account_address,
            slot_factor=slot_factor,
        )

        # El símbolo HL se deriva desde SYMBOL (fuente única) en el Executor,
        # pero aquí usamos fallback a HL_SYMBOL para compatibilidad.
        from support.secrets import secrets as _secrets
        hl_symbol = _secrets("HL_SYMBOL", "BTC")
        pos_size = 0.0
        entry_price = 0.0
        position_value = 0.0
        for pos in user_state.get("assetPositions", []):
            p = pos.get("position", {})
            if p.get("coin") == hl_symbol:
                pos_size = float(p.get("szi", 0.0))
                entry_price = float(p.get("entryPx", 0.0))
                position_value = float(p.get("positionValue", 0.0))
                break

        try:
            current_price = float(info.all_mids().get(hl_symbol, 0.0))
        except Exception:
            current_price = (position_value / pos_size) if pos_size > 0 else 0.0

        usd_sintetico = usdc_balance - position_value
        json_usd = wallet._usd
        json_btc = wallet._btc_libre + wallet.btc_en_posiciones()
        delta_usdc = usd_sintetico - json_usd
        delta_btc = pos_size - json_btc

        if not wallet.get_trade_log():
            if usdc_balance > 0:
                wallet._usd_initial = usdc_balance
                log.info("Autodescubrimiento Inicial Mainnet", usd_initial=wallet._usd_initial)
            else:
                log.info("Saldo Hyperliquid en 0, usando saldo de configuración",
                         usd_initial=wallet._usd_initial)
        elif abs(delta_usdc) > 2.0 or abs(delta_btc) > 0.00001:
            ajuste = delta_usdc + (delta_btc * current_price)
            wallet._usd_initial += ajuste
            log.info("Auditoría Contable: Ajuste de Capital",
                     delta_usdc=delta_usdc, delta_btc=delta_btc,
                     ajuste=ajuste, nuevo_capital=wallet._usd_initial)
            
        wallet._usd = usd_sintetico

        if wallet._slots_used == 0 and pos_size > 0:
            wallet._slots_used = 1
            wallet._posicion.total_btc = pos_size
            wallet._posicion.avg_entry_price = entry_price
            wallet._btc_por_posicion = [pos_size]
            log.info("Posición huérfana recuperada en Hyperliquid mainnet",
                     btc=pos_size, entry=entry_price)

        if wallet._slots_used > 0 and pos_size <= 0:
            wallet._slots_used = 0
            wallet._posicion = AggregatePosition()
            wallet._btc_por_posicion = []
            log.warning("Posición fantasma eliminada: existía en JSON local pero no en Hyperliquid mainnet")

        wallet._recalcular_slot()
        return wallet

    @staticmethod
    def _extract_usd_sintetico(user_state: dict, hl_symbol: str) -> tuple[float, float]:
        margin_summary = user_state.get("marginSummary", {})
        account_value = float(margin_summary.get("accountValue", 0.0)) if margin_summary else 0.0
        position_value = 0.0
        for pos in user_state.get("assetPositions", []):
            p = pos.get("position", {})
            if p.get("coin") == hl_symbol:
                position_value = float(p.get("positionValue", 0.0))
                break
        return account_value - position_value, account_value

    def sync_with_api_sync(self) -> None:
        from support.secrets import secrets
        hl_symbol = secrets("HL_SYMBOL", "BTC")
        info = self._get_info()
        try:
            user_state = info.user_state(self._account_address)
            self._apply_sync_state(user_state, hl_symbol)
            log.info("Saldo Hyperliquid sincronizado mainnet",
                     usd=round(self._usd, 4),
                     slots_used=self._slots_used)
        except Exception as e:
            log.warning("Error sincronizando saldo Hyperliquid Mainnet", error=str(e))

    async def sync_with_api(self, session: aiohttp.ClientSession) -> dict | None:
        """Implementación requerida por AsyncWallet (abstract method).
        Retorna dict con cambios detectados o None."""
        return await self.sync_with_api_async(session)

    async def sync_with_api_async(self, session: aiohttp.ClientSession) -> dict | None:
        from support.secrets import secrets
        hl_symbol = secrets("HL_SYMBOL", "BTC")
        url = f"{MAINNET_API}/info"
        payload = {"type": "clearinghouseState", "user": self._account_address}
        try:
            async with session.post(url, json=payload) as resp:
                user_state = await resp.json(content_type=None)
            return self._apply_sync_state(user_state, hl_symbol)
        except Exception as e:
            log.warning("Error en sync_with_api_async Hyperliquid Mainnet", error=str(e))
            return None

    def _apply_sync_state(self, user_state: dict, hl_symbol: str) -> dict | None:
        """
        Reconciliación completa con Hyperliquid Mainnet exchange:
          1. Actualiza balance USDC sintético
          2. Detecta posiciones reales y sincroniza _slots_used, _posicion, _btc_por_posicion
          3. Recalcula los slots de compra si el balance cambió
          4. Retorna dict con cambios detectados o None si no hubo cambios relevantes

        Retorna:
          {"type": "new_position", "side": "BUY", "price": X, "qty": Y}  o
          {"type": "position_closed", "side": "SELL", "price": X, "qty": Y}  o
          {"type": "position_changed", "side": "BUY"|"SELL", "price": X,
           "qty": delta_btc, "szi": pos_size, "prev_qty": old_btc}  o
          None
        """
        old_usd = self._usd
        old_slots_used = self._slots_used
        old_btc = self._posicion.total_btc if self._posicion else 0.0

        usd_sintetico, account_value = self._extract_usd_sintetico(user_state, hl_symbol)
        self._usd = usd_sintetico

        # Detectar posición real
        pos_size = 0.0
        entry_price = 0.0
        for pos in user_state.get("assetPositions", []):
            p = pos.get("position", {})
            if p.get("coin") == hl_symbol:
                pos_size = float(p.get("szi", 0.0))
                entry_price = float(p.get("entryPx", 0.0))
                break

        result = None

        # Reconciliar slots según posición real
        if pos_size > 1e-10 and old_slots_used == 0:
            # El exchange tiene una posición que la wallet no conocía
            # NO incrementamos _slots_used — puede ser fill parcial
            # Solo actualizamos la posición para tracking
            # Solo reportar cambio si la cantidad realmente varió
            diff = abs(pos_size - old_btc)
            if diff > 1e-8:
                self._posicion.total_btc = pos_size
                self._posicion.avg_entry_price = entry_price
                self._btc_por_posicion = [pos_size]
                result = {
                    "type": "position_changed",
                    "side": "BUY",
                    "price": entry_price,
                    "qty": pos_size - old_btc,
                    "szi": pos_size,
                    "prev_qty": old_btc,
                    "exchange_oid": None,
                }
            # Si la cantidad no cambió, result se queda en None
            # (evita loop infinito de notificaciones)

        elif pos_size <= 1e-10 and old_slots_used > 0:
            # La wallet cree tener una posición que el exchange no tiene
            log.info(
                "Reconciliación: wallet reporta posición pero exchange no tiene",
                wallet_slots=old_slots_used,
            )
            old_avg = self._posicion.avg_entry_price
            old_btc_total = self._posicion.total_btc
            self._slots_used = 0
            self._posicion = AggregatePosition()
            self._btc_por_posicion = []
            result = {
                "type": "position_closed",
                "side": "SELL",
                "price": old_avg,
                "qty": old_btc_total,
                "szi": 0.0,
                "exchange_oid": None,
            }

        elif pos_size > 1e-10 and old_slots_used > 0:
            # Ya había posición — detectar si cambió la cantidad
            diff = abs(pos_size - old_btc)
            if diff > 1e-8:
                # Cambio en la cantidad (fill parcial adicional o cierre parcial)
                cambio = "increase" if pos_size > old_btc else "decrease"
                log.info(
                    "Reconciliación: cantidad de posición modificada",
                    old=old_btc, new=pos_size, diff=diff, cambio=cambio,
                )
                self._posicion.total_btc = pos_size
                if cambio == "increase":
                    # Recalcular precio promedio ponderado con el nuevo BTC
                    old_total = old_btc
                    nuevo_btc = pos_size - old_btc
                    old_avg = self._posicion.avg_entry_price
                    self._posicion.avg_entry_price = (
                        (old_avg * old_total + entry_price * nuevo_btc) / pos_size
                    )
                self._recalcular_btc_por_posicion()
                result = {
                    "type": "position_changed",
                    "side": "BUY" if cambio == "increase" else "SELL",
                    "price": entry_price,
                    "qty": pos_size - old_btc,
                    "szi": pos_size,
                    "prev_qty": old_btc,
                    "exchange_oid": None,
                }

        # Almacenar el accountValue real del exchange como fuente de verdad
        # para portfolio_value(), sin depender de un precio externo.
        self._last_account_value = account_value

        # Recalcular slots de compra si cambió el balance o los slots usados
        if abs(self._usd - old_usd) > 0.01 or self._slots_used != old_slots_used:
            self._recalcular_slot()
            log.debug(
                "Slots recalculados post-sync mainnet",
                usd=self._usd, slots_used=self._slots_used,
                usable_slots=[round(s, 2) for s in self._usable_slots],
            )

        return result

    def account_value(self) -> float | None:
        """Valor nativo `accountValue` reportado por Hyperliquid mainnet, si ya fue sincronizado."""
        return self._last_account_value

    def mark_to_market(self, current_price: float) -> float:
        """Valuacion local de fallback usando balance sintetico y precio explicito."""
        return super().mark_to_market(current_price)

    def portfolio_value(self, current_price: float = 0.0) -> float:
        """
        Retorna el valor total del portfolio.
        En modo real (HL), usa el accountValue directamente del exchange,
        que ya incluye colateral libre + valor_de_la_posicion_al_mid.
        En modo simulado (papper), usa el cálculo local como fallback.
        """
        native_value = self.account_value()
        if native_value is not None:
            return native_value
        return self.mark_to_market(current_price)

    def get_open_positions(self) -> list:
        info = self._get_info()
        try:
            return info.user_state(self._account_address).get("assetPositions", [])
        except Exception as e:
            log.warning("Error obteniendo posiciones Hyperliquid mainnet", error=str(e))
            return []

    async def get_open_positions_async(self, session: aiohttp.ClientSession) -> list:
        url = f"{MAINNET_API}/info"
        payload = {"type": "clearinghouseState", "user": self._account_address}
        try:
            async with session.post(url, json=payload) as resp:
                user_state = await resp.json(content_type=None)
            return user_state.get("assetPositions", [])
        except Exception as e:
            log.warning("Error en get_open_positions_async Hyperliquid mainnet", error=str(e))
            return []