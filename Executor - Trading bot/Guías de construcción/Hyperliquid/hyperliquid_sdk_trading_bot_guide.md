# Hyperliquid Python SDK — Guía Completa para Trading Bots de Perpetuos

> **Audiencia:** Agente de programación que construye un bot de trading sobre Hyperliquid Core (mainnet y testnet) operando en el mercado de futuros perpetuos.  
> **Fuente:** Repositorio oficial [`hyperliquid-dex/hyperliquid-python-sdk`](https://github.com/hyperliquid-dex/hyperliquid-python-sdk) — versión actual `0.23.0`.

---

## Tabla de Contenidos

1. [Arquitectura general del SDK](#1-arquitectura-general-del-sdk)
2. [Instalación y configuración](#2-instalación-y-configuración)
3. [Autenticación y wallets](#3-autenticación-y-wallets)
4. [Constantes de red (mainnet vs testnet)](#4-constantes-de-red-mainnet-vs-testnet)
5. [Clase `Info` — Consultas de mercado y cuenta](#5-clase-info--consultas-de-mercado-y-cuenta)
6. [Clase `Exchange` — Ejecución de órdenes y acciones firmadas](#6-clase-exchange--ejecución-de-órdenes-y-acciones-firmadas)
7. [Tipos de órdenes en perpetuos](#7-tipos-de-órdenes-en-perpetuos)
8. [Gestión de posiciones abiertas](#8-gestión-de-posiciones-abiertas)
9. [Leverage y margen](#9-leverage-y-margen)
10. [Cancelación de órdenes](#10-cancelación-de-órdenes)
11. [WebSocket — Datos en tiempo real](#11-websocket--datos-en-tiempo-real)
12. [Custom Order IDs (Cloid)](#12-custom-order-ids-cloid)
13. [Patrón de setup recomendado para bots](#13-patrón-de-setup-recomendado-para-bots)
14. [Manejo de respuestas de la API](#14-manejo-de-respuestas-de-la-api)
15. [Precisión de precios y tamaños](#15-precisión-de-precios-y-tamaños)
16. [Funciones avanzadas](#16-funciones-avanzadas)
17. [Errores comunes y cómo evitarlos](#17-errores-comunes-y-cómo-evitarlos)
18. [Ejemplos completos listos para producción](#18-ejemplos-completos-listos-para-producción)

---

## 1. Arquitectura general del SDK

El SDK se organiza en tres capas principales:

```
hyperliquid/
├── api.py              # Clase base HTTP (POST /info y /exchange)
├── info.py             # Clase Info — datos de mercado + WebSocket (solo lectura)
├── exchange.py         # Clase Exchange — acciones firmadas (órdenes, transfers, etc.)
├── websocket_manager.py# Manager de conexión WebSocket con reconexión automática
└── utils/
    ├── constants.py    # URLs de mainnet y testnet
    ├── signing.py      # Firma de mensajes EIP-712 / L1 actions
    └── types.py        # TypedDicts y tipos auxiliares
```

**Principios clave:**

- Toda lectura va a `POST /info` (sin autenticación).
- Toda acción de trading va a `POST /exchange` (requiere firma EIP-712).
- El SDK firma automáticamente; el bot solo necesita la clave privada.
- `Info` incluye un WebSocket manager interno para streams en tiempo real.
- No existen API keys tradicionales: la autenticación es 100% criptográfica vía wallet.

---

## 2. Instalación y configuración

```bash
pip install hyperliquid-python-sdk
```

**Dependencias principales instaladas automáticamente:** `eth-account`, `websocket-client`, `requests`.

### Configuración mínima (archivo JSON)

Crear `config.json` a partir del ejemplo del repo:

```json
{
  "secret_key": "0xTU_CLAVE_PRIVADA_AQUI",
  "account_address": "0xTU_DIRECCION_PUBLICA_AQUI"
}
```

> **Importante:** `account_address` es SIEMPRE la dirección del wallet principal (no la del API wallet si usás uno separado).

### Cargar la config en el bot

```python
import json
import eth_account

def load_config(path="config.json"):
    with open(path) as f:
        config = json.load(f)
    wallet = eth_account.Account.from_key(config["secret_key"])
    address = config.get("account_address", wallet.address)
    return wallet, address
```

---

## 3. Autenticación y wallets

Hyperliquid **no usa API keys** en el sentido tradicional. Cada acción de trading se firma con una clave privada de Ethereum usando EIP-712.

### Opción A — Wallet principal (más simple)

```python
import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

wallet = eth_account.Account.from_key("0xTU_CLAVE_PRIVADA")
exchange = Exchange(wallet, constants.MAINNET_API_URL)
```

### Opción B — API Wallet (recomendada para bots en producción)

Genera y autoriza un API wallet en `https://app.hyperliquid.xyz/API`. Esto permite usar una clave separada sin exponer la clave principal.

```python
wallet = eth_account.Account.from_key("0xCLAVE_DEL_API_WALLET")

exchange = Exchange(
    wallet,
    constants.MAINNET_API_URL,
    account_address="0xDIRECCION_DEL_WALLET_PRINCIPAL"  # ← crítico
)
```

> El campo `account_address` le indica al exchange en nombre de qué cuenta actuar. Si se omite, usa `wallet.address` como cuenta.

### Opción C — Vault address (para operar con vaults)

```python
exchange = Exchange(
    wallet,
    constants.MAINNET_API_URL,
    vault_address="0xDIRECCION_DEL_VAULT"
)
```

### Generar un nuevo API wallet programáticamente

```python
result, agent_key = exchange.approve_agent(name="mi_bot_v1")
# agent_key es la clave privada del nuevo API wallet
# result contiene la respuesta de la blockchain
```

---

## 4. Constantes de red (mainnet vs testnet)

```python
from hyperliquid.utils import constants

# Mainnet
constants.MAINNET_API_URL   # "https://api.hyperliquid.xyz"

# Testnet
constants.TESTNET_API_URL   # "https://api.hyperliquid-testnet.xyz"
```

**WebSocket:**
- Mainnet: `wss://api.hyperliquid.xyz/ws`
- Testnet: `wss://api.hyperliquid-testnet.xyz/ws`

> **Regla de oro:** Las firmas de mainnet NO son válidas en testnet y viceversa. El SDK detecta esto automáticamente comparando `base_url == MAINNET_API_URL`. Siempre desarrollá y probá en testnet antes de conectar con claves reales.

### Cambiar entre redes

```python
# Testnet
info_test = Info(constants.TESTNET_API_URL, skip_ws=True)
exchange_test = Exchange(wallet, constants.TESTNET_API_URL, account_address=address)

# Mainnet
info_main = Info(constants.MAINNET_API_URL, skip_ws=True)
exchange_main = Exchange(wallet, constants.MAINNET_API_URL, account_address=address)
```

---

## 5. Clase `Info` — Consultas de mercado y cuenta

```python
from hyperliquid.info import Info
from hyperliquid.utils import constants

info = Info(constants.MAINNET_API_URL, skip_ws=True)
```

`skip_ws=True` deshabilita el WebSocket y hace la inicialización más rápida. Usá `skip_ws=False` (o simplemente omitilo) cuando necesites streams en tiempo real.

### 5.1 Estado de cuenta y posiciones abiertas

```python
user_state = info.user_state("0xTU_DIRECCION")
```

**Respuesta relevante para perpetuos:**

```python
{
    "assetPositions": [
        {
            "position": {
                "coin": "ETH",
                "szi": "0.5",           # tamaño: positivo=long, negativo=short
                "entryPx": "3200.0",    # precio de entrada promedio
                "positionValue": "1600.0",
                "unrealizedPnl": "50.0",
                "returnOnEquity": "0.03",
                "liquidationPx": "2800.0",
                "marginUsed": "160.0",
                "leverage": {
                    "type": "cross",    # o "isolated"
                    "value": 10
                }
            },
            "type": "oneWay"
        }
    ],
    "crossMarginSummary": {
        "accountValue": "5000.0",
        "totalMarginUsed": "160.0",
        "totalNtlPos": "1600.0",
        "totalRawUsd": "4950.0"
    },
    "marginSummary": { ... },  # mismo schema
    "withdrawable": "4840.0"   # USDC disponible para retirar
}
```

### 5.2 Órdenes abiertas

```python
open_orders = info.open_orders("0xTU_DIRECCION")
# Retorna lista de órdenes con: coin, limitPx, oid, side ("A"=ask/sell, "B"=bid/buy), sz, timestamp

# Con información adicional para el frontend (incluye TP/SL como children)
frontend_orders = info.frontend_open_orders("0xTU_DIRECCION")
```

### 5.3 Precio mid de todos los activos

```python
mids = info.all_mids()
# {"ETH": "3245.5", "BTC": "67000.0", "SOL": "145.2", ...}

eth_price = float(mids["ETH"])
```

### 5.4 Order book (L2 snapshot)

```python
l2 = info.l2_snapshot("ETH")
# {
#   "coin": "ETH",
#   "levels": [
#       [{"n": 5, "px": "3245.0", "sz": "10.5"}, ...],  # bids (índice 0)
#       [{"n": 3, "px": "3246.0", "sz": "8.2"},  ...]   # asks (índice 1)
#   ],
#   "time": 1717000000000
# }

best_bid = float(l2["levels"][0][0]["px"])
best_ask = float(l2["levels"][1][0]["px"])
```

### 5.5 Historial de fills (ejecuciones)

```python
fills = info.user_fills("0xTU_DIRECCION")
# Cada fill: {closedPnl, coin, crossed, dir, hash, oid, px, side, startPosition, sz, time}

# Por rango de tiempo (timestamps en milisegundos)
fills = info.user_fills_by_time("0xTU_DIRECCION", start_time=1700000000000, end_time=1717000000000)
```

### 5.6 Funding rate

```python
import time

# Historial de funding de ETH en las últimas 24h
start = int((time.time() - 86400) * 1000)
funding = info.funding_history("ETH", startTime=start)
# [{coin, fundingRate, premium, time}, ...]

# Funding del usuario (pagos reales recibidos/pagados)
user_funding = info.user_funding_history("0xTU_DIRECCION", startTime=start)
```

### 5.7 Metadata del exchange

```python
# Todos los perpetuos disponibles
meta = info.meta()
# {"universe": [{"name": "ETH", "szDecimals": 4, "maxLeverage": 50, "onlyIsolated": false}, ...]}

# Con contextos de activos (volumen, OI, mark price, etc.)
meta_ctxs = info.meta_and_asset_ctxs()
# [meta_dict, [{"dayNtlVlm", "funding", "markPx", "midPx", "openInterest", "oraclePx", ...}, ...]]
```

### 5.8 Velas (OHLCV)

```python
import time

end = int(time.time() * 1000)
start = end - 3600 * 1000  # 1 hora atrás

candles = info.candles_snapshot("ETH", interval="1m", startTime=start, endTime=end)
# Intervalos válidos: "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1w"
# Cada vela: {T (close time), c, h, l, o (precios), v (volumen), n (num trades), s (símbolo), t (open time)}
```

### 5.9 Historial de órdenes y estado de una orden

```python
# Últimas 2000 órdenes históricas
hist = info.historical_orders("0xTU_DIRECCION")

# Estado de una orden por OID
status = info.query_order_by_oid("0xTU_DIRECCION", oid=12345678)

# Estado de una orden por Client Order ID
from hyperliquid.utils.types import Cloid
cloid = Cloid.from_str("0x00000000000000000000000000000001")
status = info.query_order_by_cloid("0xTU_DIRECCION", cloid)
```

### 5.10 Fees del usuario

```python
fees = info.user_fees("0xTU_DIRECCION")
# {activeReferralDiscount, dailyUserVlm, feeSchedule, userAddRate, userCrossRate}
# userAddRate = tasa maker, userCrossRate = tasa taker
```

---

## 6. Clase `Exchange` — Ejecución de órdenes y acciones firmadas

```python
import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

wallet = eth_account.Account.from_key("0xTU_CLAVE_PRIVADA")
exchange = Exchange(
    wallet,
    constants.MAINNET_API_URL,
    account_address="0xTU_DIRECCION_PUBLICA"
)
```

El `Exchange` internamente instancia un `Info` propio (con `skip_ws=True`) para resolver nombres de coins y assets.

### 6.1 `exchange.order()` — Orden individual

Es el método de bajo nivel para cualquier tipo de orden.

```python
result = exchange.order(
    name="ETH",           # Nombre del activo (string)
    is_buy=True,          # True=long/buy, False=short/sell
    sz=0.1,               # Tamaño en unidades del activo
    limit_px=3200.0,      # Precio límite
    order_type={"limit": {"tif": "Gtc"}},  # Tipo de orden (ver sección 7)
    reduce_only=False,    # True para cerrar posición exclusivamente
    cloid=None,           # Custom Order ID opcional
    builder=None          # Builder fee opcional
)
```

### 6.2 `exchange.bulk_orders()` — Múltiples órdenes en una sola transacción

```python
orders = [
    {"coin": "ETH", "is_buy": True,  "sz": 0.1, "limit_px": 3000.0, "order_type": {"limit": {"tif": "Gtc"}}, "reduce_only": False},
    {"coin": "BTC", "is_buy": False, "sz": 0.01, "limit_px": 70000.0, "order_type": {"limit": {"tif": "Gtc"}}, "reduce_only": False},
]
result = exchange.bulk_orders(orders, grouping="na")
# grouping: "na" (independientes), "normalTpsl" (TP/SL relacionados), "positionTpsl" (TP/SL de posición)
```

### 6.3 `exchange.market_open()` — Apertura de posición a mercado

Internamente coloca una orden límite IoC con slippage calculado sobre el mid price.

```python
result = exchange.market_open(
    name="ETH",
    is_buy=True,
    sz=0.05,
    px=None,           # None = usa el mid price actual
    slippage=0.05,     # 5% de slippage máximo (default)
    cloid=None,
    builder=None
)
```

### 6.4 `exchange.market_close()` — Cierre de posición a mercado

Automáticamente detecta el tamaño y dirección de la posición existente.

```python
result = exchange.market_close(
    coin="ETH",
    sz=None,           # None = cierra toda la posición
    px=None,           # None = usa mid price
    slippage=0.05,
    cloid=None
)
```

### 6.5 `exchange.modify_order()` — Modificar una orden existente

```python
result = exchange.modify_order(
    oid=12345678,      # OID o Cloid de la orden a modificar
    name="ETH",
    is_buy=True,
    sz=0.2,
    limit_px=3150.0,
    order_type={"limit": {"tif": "Gtc"}},
    reduce_only=False,
    cloid=None
)
```

Para modificar múltiples órdenes de una vez:

```python
modifies = [
    {"oid": 111, "order": {"coin": "ETH", "is_buy": True, "sz": 0.1, "limit_px": 3100.0, "order_type": {"limit": {"tif": "Gtc"}}, "reduce_only": False, "cloid": None}},
    {"oid": 222, "order": {"coin": "BTC", "is_buy": False, "sz": 0.01, "limit_px": 69000.0, "order_type": {"limit": {"tif": "Gtc"}}, "reduce_only": False, "cloid": None}},
]
result = exchange.bulk_modify_orders_new(modifies)
```

---

## 7. Tipos de órdenes en perpetuos

El parámetro `order_type` es un dict que especifica el comportamiento de la orden.

### 7.1 Órdenes Límite

```python
# GTC — Good Till Cancelled (permanece hasta cancelarse)
{"limit": {"tif": "Gtc"}}

# IOC — Immediate Or Cancel (ejecuta lo posible, cancela el resto)
{"limit": {"tif": "Ioc"}}

# ALO — Add Liquidity Only / Post-Only (cancela si tomaría liquidez)
{"limit": {"tif": "Alo"}}
```

### 7.2 Órdenes de Mercado

Hyperliquid no tiene un tipo "market" nativo. Se simula con una orden límite IoC con precio agresivo:

```python
# Forma manual
mid_price = float(info.all_mids()["ETH"])
buy_price = round(mid_price * 1.05, 2)   # 5% sobre el mid para asegurar fill
result = exchange.order("ETH", True, 0.1, buy_price, {"limit": {"tif": "Ioc"}})

# Forma automática (recomendada)
result = exchange.market_open("ETH", True, 0.1, slippage=0.05)
```

### 7.3 Stop Loss y Take Profit

Son órdenes de tipo `trigger`. **Siempre deben tener `reduce_only=True`** para cerrar posición.

```python
# Stop Loss a mercado
stop_loss = exchange.order(
    "ETH",
    is_buy=False,      # opuesto a la posición (long → sell)
    sz=0.1,
    limit_px=2800.0,   # precio "peor caso" para IoC
    order_type={
        "trigger": {
            "triggerPx": 3000.0,   # precio de activación
            "isMarket": True,       # True=mercado al activar, False=límite al activar
            "tpsl": "sl"            # "sl" para stop loss
        }
    },
    reduce_only=True
)

# Take Profit a mercado
take_profit = exchange.order(
    "ETH",
    is_buy=False,
    sz=0.1,
    limit_px=3600.0,
    order_type={
        "trigger": {
            "triggerPx": 3500.0,
            "isMarket": True,
            "tpsl": "tp"            # "tp" para take profit
        }
    },
    reduce_only=True
)
```

**Ejemplo completo de entrada + SL + TP en una sola llamada:**

```python
orders = [
    # Entrada
    {"coin": "ETH", "is_buy": True, "sz": 0.1, "limit_px": 3200.0,
     "order_type": {"limit": {"tif": "Gtc"}}, "reduce_only": False},
    # Stop Loss
    {"coin": "ETH", "is_buy": False, "sz": 0.1, "limit_px": 2900.0,
     "order_type": {"trigger": {"triggerPx": 3000.0, "isMarket": True, "tpsl": "sl"}},
     "reduce_only": True},
    # Take Profit
    {"coin": "ETH", "is_buy": False, "sz": 0.1, "limit_px": 3600.0,
     "order_type": {"trigger": {"triggerPx": 3500.0, "isMarket": True, "tpsl": "tp"}},
     "reduce_only": True},
]
result = exchange.bulk_orders(orders, grouping="normalTpsl")
```

---

## 8. Gestión de posiciones abiertas

### Leer posiciones actuales

```python
def get_position(info, address, coin):
    state = info.user_state(address)
    for pos in state["assetPositions"]:
        p = pos["position"]
        if p["coin"] == coin:
            return {
                "coin": p["coin"],
                "size": float(p["szi"]),        # positivo=long, negativo=short
                "entry_price": float(p["entryPx"]) if p["entryPx"] else None,
                "unrealized_pnl": float(p["unrealizedPnl"]),
                "liquidation_px": float(p["liquidationPx"]) if p["liquidationPx"] else None,
                "margin_used": float(p["marginUsed"]),
                "leverage": p["leverage"]["value"],
                "leverage_type": p["leverage"]["type"]  # "cross" o "isolated"
            }
    return None  # no hay posición abierta

pos = get_position(info, "0xTU_DIRECCION", "ETH")
if pos:
    print(f"ETH: {pos['size']} @ {pos['entry_price']} | PnL: {pos['unrealized_pnl']}")
```

### Cerrar posición parcialmente

```python
# Cierre de mitad de la posición
pos = get_position(info, address, "ETH")
if pos and pos["size"] > 0:
    close_size = pos["size"] / 2
    result = exchange.order(
        "ETH", False, close_size,
        limit_px=round(float(info.all_mids()["ETH"]) * 0.98, 2),
        order_type={"limit": {"tif": "Ioc"}},
        reduce_only=True
    )
```

### Portfolio completo

```python
portfolio = info.portfolio("0xTU_DIRECCION")
# Datos históricos de PnL, account value y volumen por períodos
```

---

## 9. Leverage y margen

### Cambiar leverage

```python
# Cross margin con 10x leverage
result = exchange.update_leverage(
    leverage=10,
    name="ETH",
    is_cross=True   # True=cross, False=isolated
)

# Isolated margin con 5x
result = exchange.update_leverage(leverage=5, name="BTC", is_cross=False)
```

> Cambiar el leverage **no requiere tener una posición abierta**. Es recomendable configurarlo antes de colocar la primera orden.

### Agregar/retirar margen en posición isolated

```python
# Agregar $100 de margen a la posición isolated de ETH
result = exchange.update_isolated_margin(amount=100.0, name="ETH")

# Retirar margen (valor negativo)
result = exchange.update_isolated_margin(amount=-50.0, name="ETH")
```

### Transferir USDC entre spot y perpetuos

```python
# Depositar desde spot wallet a perp wallet
result = exchange.usd_class_transfer(amount=500.0, to_perp=True)

# Retirar de perp wallet a spot wallet
result = exchange.usd_class_transfer(amount=200.0, to_perp=False)
```

---

## 10. Cancelación de órdenes

### Cancelar por OID

```python
result = exchange.cancel("ETH", oid=12345678)
```

### Cancelar por Cloid

```python
from hyperliquid.utils.types import Cloid
cloid = Cloid.from_str("0x00000000000000000000000000000001")
result = exchange.cancel_by_cloid("ETH", cloid)
```

### Cancelar múltiples órdenes

```python
result = exchange.bulk_cancel([
    {"coin": "ETH", "oid": 111},
    {"coin": "BTC", "oid": 222},
    {"coin": "SOL", "oid": 333},
])
```

### Dead Man's Switch — cancelar todo en una hora futura

Programa la cancelación automática de todas las órdenes abiertas. Útil como seguro si el bot cae.

```python
import time

# Cancelar todo en 60 segundos
cancel_at = int(time.time() * 1000) + 60_000
result = exchange.schedule_cancel(time=cancel_at)

# Cancelar el Dead Man's Switch (pasar None)
result = exchange.schedule_cancel(time=None)
```

> Mínimo 5 segundos en el futuro. Máximo 10 activaciones por día (reset a las 00:00 UTC).

---

## 11. WebSocket — Datos en tiempo real

Inicializar con `skip_ws=False` (o simplemente no pasar el parámetro):

```python
info = Info(constants.MAINNET_API_URL)  # WebSocket activo
```

### Suscripciones disponibles

```python
# Precios mid de todos los activos
info.subscribe({"type": "allMids"}, callback)

# Order book de un activo
info.subscribe({"type": "l2Book", "coin": "ETH"}, callback)

# Trades recientes de un activo
info.subscribe({"type": "trades", "coin": "ETH"}, callback)

# Velas en tiempo real
info.subscribe({"type": "candle", "coin": "ETH", "interval": "1m"}, callback)

# Eventos del usuario (fills, liquidaciones, etc.)
info.subscribe({"type": "userEvents", "user": "0xTU_DIRECCION"}, callback)

# Fills del usuario
info.subscribe({"type": "userFills", "user": "0xTU_DIRECCION"}, callback)

# Actualizaciones de órdenes
info.subscribe({"type": "orderUpdates", "user": "0xTU_DIRECCION"}, callback)

# Pagos de funding
info.subscribe({"type": "userFundings", "user": "0xTU_DIRECCION"}, callback)

# Movimientos de cuenta (depósitos, retiros, etc.)
info.subscribe({"type": "userNonFundingLedgerUpdates", "user": "0xTU_DIRECCION"}, callback)

# Best Bid/Offer
info.subscribe({"type": "bbo", "coin": "ETH"}, callback)

# Web data completo del usuario (estado completo)
info.subscribe({"type": "webData2", "user": "0xTU_DIRECCION"}, callback)
```

### Patrón de callback recomendado

```python
import threading

latest_mid = {}
lock = threading.Lock()

def on_mids(msg):
    with lock:
        if msg.get("channel") == "allMids":
            latest_mid.update(msg["data"]["mids"])

sub_id = info.subscribe({"type": "allMids"}, on_mids)

# Para desuscribirse
info.unsubscribe({"type": "allMids"}, sub_id)
```

### Bot reactivo con WebSocket

```python
def on_user_fill(msg):
    if msg.get("channel") == "userFills":
        for fill in msg["data"]["fills"]:
            print(f"Fill: {fill['coin']} {fill['side']} {fill['sz']} @ {fill['px']}")
            # Lógica de seguimiento de posición, ajuste de SL, etc.

info.subscribe({"type": "userFills", "user": address}, on_user_fill)
```

> **Crítico:** Siempre implementar reconexión ante desconexiones. El SDK tiene reconexión automática interna, pero los datos perdidos durante el gap deben recuperarse con llamadas REST.

### Desconectar WebSocket

```python
info.disconnect_websocket()
```

---

## 12. Custom Order IDs (Cloid)

Los `Cloid` (Client Order IDs) permiten identificar órdenes con IDs propios de 128 bits (hex de 32 caracteres).

```python
from hyperliquid.utils.types import Cloid

# Crear desde string hex
cloid = Cloid.from_str("0x00000000000000000000000000000001")

# Crear desde entero
cloid = Cloid.from_int(42)

# Usar en orden
result = exchange.order("ETH", True, 0.1, 3200.0, {"limit": {"tif": "Gtc"}}, cloid=cloid)

# Consultar estado
status = info.query_order_by_cloid(address, cloid)

# Cancelar por cloid
result = exchange.cancel_by_cloid("ETH", cloid)
```

**Patrón para bots:** Asignar un Cloid secuencial por orden para tracking interno sin depender de OIDs del exchange.

---

## 13. Patrón de setup recomendado para bots

### Setup estándar (igual al de los ejemplos oficiales)

```python
import json
import eth_account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

def setup(base_url=constants.MAINNET_API_URL, config_path="config.json", skip_ws=True):
    with open(config_path) as f:
        config = json.load(f)
    
    wallet = eth_account.Account.from_key(config["secret_key"])
    address = config.get("account_address", wallet.address)
    
    info = Info(base_url, skip_ws=skip_ws)
    exchange = Exchange(wallet, base_url, account_address=address)
    
    print(f"Conectado como: {address}")
    print(f"Red: {'MAINNET' if base_url == constants.MAINNET_API_URL else 'TESTNET'}")
    
    return address, info, exchange

# Uso
address, info, exchange = setup(constants.TESTNET_API_URL)
```

### Setup para bot con WebSocket

```python
address, info, exchange = setup(constants.MAINNET_API_URL, skip_ws=False)

# Registrar handlers antes del loop principal
info.subscribe({"type": "allMids"}, handle_mids)
info.subscribe({"type": "userEvents", "user": address}, handle_user_events)

# El WebSocket corre en un thread separado automáticamente
```

---

## 14. Manejo de respuestas de la API

### Estructura de respuesta de órdenes

```python
result = exchange.order("ETH", True, 0.1, 3200.0, {"limit": {"tif": "Gtc"}})

if result["status"] == "ok":
    statuses = result["response"]["data"]["statuses"]
    for status in statuses:
        if "resting" in status:
            oid = status["resting"]["oid"]
            print(f"Orden en libro: OID {oid}")
        elif "filled" in status:
            filled = status["filled"]
            print(f"Llenada: {filled['totalSz']} @ {filled['avgPx']} | OID: {filled['oid']}")
        elif "error" in status:
            print(f"Error en orden: {status['error']}")
elif result["status"] == "err":
    print(f"Error: {result['response']}")
```

### Wrapper de manejo seguro

```python
def place_order_safe(exchange, coin, is_buy, sz, px, order_type, reduce_only=False, cloid=None):
    try:
        result = exchange.order(coin, is_buy, sz, px, order_type, reduce_only, cloid)
        if result["status"] != "ok":
            print(f"[ERROR] Orden rechazada: {result}")
            return None
        
        statuses = result["response"]["data"]["statuses"]
        status = statuses[0]
        
        if "resting" in status:
            return {"type": "resting", "oid": status["resting"]["oid"]}
        elif "filled" in status:
            f = status["filled"]
            return {"type": "filled", "oid": f["oid"], "sz": f["totalSz"], "px": f["avgPx"]}
        elif "error" in status:
            print(f"[ERROR] {status['error']}")
            return None
    except Exception as e:
        print(f"[EXCEPTION] {e}")
        return None
```

---

## 15. Precisión de precios y tamaños

Hyperliquid rechaza órdenes con precisión incorrecta. El SDK maneja esto automáticamente en `market_open` / `market_close`, pero al usar `order()` directamente hay que cuidarlo.

### Reglas de precisión

- **Precios (px):** 5 cifras significativas, máximo 6 decimales para perps.
- **Tamaños (sz):** Depende del `szDecimals` del activo (ver `info.meta()`).

```python
def get_sz_decimals(info, coin):
    meta = info.meta()
    for asset in meta["universe"]:
        if asset["name"] == coin:
            return asset["szDecimals"]
    return 4  # default

def round_sz(sz, decimals):
    return round(sz, decimals)

def round_px(px):
    return round(float(f"{px:.5g}"), 6)

# Ejemplo
eth_decimals = get_sz_decimals(info, "ETH")   # típicamente 4
sz = round_sz(0.123456789, eth_decimals)        # → 0.1235
px = round_px(3245.678)                         # → 3245.7
```

---

## 16. Funciones avanzadas

### 16.1 Builder fees (para plataformas que distribuyen fees)

```python
# Aprobar un builder fee (una vez)
exchange.approve_builder_fee(
    builder="0xDIRECCION_DEL_BUILDER",
    max_fee_rate="0.001%"  # máximo fee adicional
)

# Colocar orden con builder
exchange.market_open(
    "ETH", True, 0.05,
    builder={"b": "0xDIRECCION_DEL_BUILDER", "f": 1}  # f = fee en unidades de 0.001%
)
```

### 16.2 Expiración de acciones

```python
import time

# Configurar que las acciones expiren en 30 segundos
expires_at = int(time.time() * 1000) + 30_000
exchange.set_expires_after(expires_at)

# Desactivar expiración
exchange.set_expires_after(None)
```

### 16.3 Sub-accounts

```python
# Crear sub-account
exchange.create_sub_account(name="estrategia_momentum")

# Listar sub-accounts
subs = info.query_sub_accounts("0xTU_DIRECCION")

# Transferir USDC a/desde sub-account
exchange.sub_account_transfer(
    sub_account_user="0xDIRECCION_SUB",
    is_deposit=True,   # True=deposit a sub, False=withdraw de sub
    usd=1000           # en USD cents (1000 = $10)
)
```

### 16.4 Vault transfers

```python
exchange.vault_usd_transfer(
    vault_address="0xDIRECCION_VAULT",
    is_deposit=True,
    usd=5000  # en USD cents
)
```

### 16.5 Referral

```python
exchange.set_referrer(code="MI_CODIGO_REF")
```

### 16.6 Rate limits del usuario

```python
limits = info.user_rate_limit("0xTU_DIRECCION")
# Información sobre el límite de requests por minuto y uso actual
```

---

## 17. Errores comunes y cómo evitarlos

| Error | Causa | Solución |
|---|---|---|
| `Order rejected: insufficient margin` | Margen insuficiente | Verificar `user_state["withdrawable"]` antes de operar |
| `Order rejected: size too small` | `sz` menor al mínimo del activo | Consultar `szDecimals` y mínimos en `meta()` |
| `Invalid price precision` | Precio con demasiados decimales | Usar `round_px()` con 5 cifras significativas |
| `Signature mismatch` | Clave privada incorrecta o `account_address` mal configurado | Verificar que `account_address` sea el wallet principal |
| `Actions signed for mainnet not valid on testnet` | URL incorrecta | Pasar siempre `TESTNET_API_URL` explícitamente |
| `Cannot call subscribe since skip_ws was used` | WebSocket no inicializado | Crear `Info` sin `skip_ws=True` |
| `Position not found for coin` | `market_close()` sin posición abierta | Verificar existencia de posición antes de llamar |
| Timeout en WebSocket | Conexión idle > 60 segundos | El SDK envía pings automáticos; si cae, reconectar |

### Validaciones previas recomendadas

```python
def pre_trade_check(info, exchange, address, coin, usd_size):
    state = info.user_state(address)
    withdrawable = float(state["withdrawable"])
    
    if withdrawable < usd_size * 1.1:  # 10% de buffer
        raise ValueError(f"Margen insuficiente: {withdrawable} < {usd_size * 1.1}")
    
    mids = info.all_mids()
    if coin not in mids:
        raise ValueError(f"Coin {coin} no disponible en el exchange")
    
    return True
```

---

## 18. Ejemplos completos listos para producción

### Ejemplo 1: Bot básico long/short con SL y TP

```python
import time
import json
import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants
from hyperliquid.utils.types import Cloid

# ── CONFIGURACIÓN ────────────────────────────────────────────────────────────
NETWORK = constants.MAINNET_API_URL   # cambiar a TESTNET_API_URL para pruebas
COIN = "ETH"
POSITION_SIZE = 0.05                  # ETH
LEVERAGE = 10
SL_PCT = 0.02                         # 2% stop loss
TP_PCT = 0.04                         # 4% take profit
# ─────────────────────────────────────────────────────────────────────────────

with open("config.json") as f:
    config = json.load(f)

wallet = eth_account.Account.from_key(config["secret_key"])
address = config.get("account_address", wallet.address)
info = Info(NETWORK, skip_ws=True)
exchange = Exchange(wallet, NETWORK, account_address=address)

# 1. Configurar leverage
exchange.update_leverage(LEVERAGE, COIN, is_cross=True)

# 2. Obtener precio actual
mid = float(info.all_mids()[COIN])
print(f"Precio actual de {COIN}: ${mid}")

# 3. Calcular niveles
entry_px = round(float(f"{mid:.5g}"), 6)
sl_px    = round(float(f"{mid * (1 - SL_PCT):.5g}"), 6)
tp_px    = round(float(f"{mid * (1 + TP_PCT):.5g}"), 6)

print(f"Entrada: {entry_px} | SL: {sl_px} | TP: {tp_px}")

# 4. Colocar entrada + SL + TP juntos
order_id = 1
orders = [
    {
        "coin": COIN, "is_buy": True, "sz": POSITION_SIZE,
        "limit_px": entry_px,
        "order_type": {"limit": {"tif": "Gtc"}},
        "reduce_only": False,
        "cloid": Cloid.from_int(order_id)
    },
    {
        "coin": COIN, "is_buy": False, "sz": POSITION_SIZE,
        "limit_px": round(sl_px * 0.95, 6),
        "order_type": {"trigger": {"triggerPx": sl_px, "isMarket": True, "tpsl": "sl"}},
        "reduce_only": True
    },
    {
        "coin": COIN, "is_buy": False, "sz": POSITION_SIZE,
        "limit_px": round(tp_px * 1.05, 6),
        "order_type": {"trigger": {"triggerPx": tp_px, "isMarket": True, "tpsl": "tp"}},
        "reduce_only": True
    }
]

result = exchange.bulk_orders(orders, grouping="normalTpsl")
print(json.dumps(result, indent=2))
```

### Ejemplo 2: Market maker simple (basic_adding style)

```python
import time
import threading
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

COIN = "ETH"
DEPTH = 0.003    # 0.3% de profundidad
TOLERANCE = 0.5  # 50% de tolerancia antes de reemplazar

info = Info(constants.TESTNET_API_URL)  # WebSocket activo
# ... (setup exchange)

state = {"bid_oid": None, "ask_oid": None, "mid": None}

def on_l2(msg):
    if msg.get("channel") != "l2Book":
        return
    levels = msg["data"]["levels"]
    best_bid = float(levels[0][0]["px"])
    best_ask = float(levels[1][0]["px"])
    state["mid"] = (best_bid + best_ask) / 2
    update_quotes()

def update_quotes():
    mid = state["mid"]
    if not mid:
        return
    
    target_bid = round(mid * (1 - DEPTH), 2)
    target_ask = round(mid * (1 + DEPTH), 2)
    
    # Cancelar y reemplazar si el precio se desvió demasiado
    # ... (lógica de gestión de órdenes)

info.subscribe({"type": "l2Book", "coin": COIN}, on_l2)

# Loop principal
while True:
    time.sleep(1)
```

### Ejemplo 3: Monitor de posición con cierre de emergencia

```python
import time
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

def monitor_position(info, exchange, address, coin, max_loss_usd=500):
    while True:
        state = info.user_state(address)
        for pos_wrap in state["assetPositions"]:
            pos = pos_wrap["position"]
            if pos["coin"] != coin:
                continue
            
            pnl = float(pos["unrealizedPnl"])
            print(f"{coin}: PnL = ${pnl:.2f}")
            
            if pnl < -max_loss_usd:
                print(f"¡PnL excede límite! Cerrando posición de emergencia...")
                result = exchange.market_close(coin, slippage=0.1)
                print(f"Cierre: {result}")
                return
        
        time.sleep(5)
```

---

## Recursos adicionales

- **Repositorio oficial:** https://github.com/hyperliquid-dex/hyperliquid-python-sdk
- **Documentación oficial de la API:** https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
- **PyPI:** https://pypi.org/project/hyperliquid-python-sdk/
- **Generar API Wallet:** https://app.hyperliquid.xyz/API
- **Testnet faucet:** https://app.hyperliquid-testnet.xyz/

---

*Generado el 17 de junio de 2026 — SDK versión 0.23.0*
