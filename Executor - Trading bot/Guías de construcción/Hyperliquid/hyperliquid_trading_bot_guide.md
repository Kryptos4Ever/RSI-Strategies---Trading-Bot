# Guía Completa para Bots de Trading en Hyperliquid Core — Perpetuos (Mainnet & Testnet)

> **Documento generado para agentes de programación.** Cubre toda la API pública de HyperCore necesaria para operar posiciones en el mercado de perpetuos: endpoints REST, WebSocket, firmas, rate limits, y herramientas del ecosistema.

---

## Índice

1. [Arquitectura General](#1-arquitectura-general)
2. [URLs de Red (Mainnet vs Testnet)](#2-urls-de-red-mainnet-vs-testnet)
3. [SDKs y Librerías](#3-sdks-y-librerías)
4. [Autenticación y Firma](#4-autenticación-y-firma)
5. [Nonces y API Wallets (Agent Wallets)](#5-nonces-y-api-wallets-agent-wallets)
6. [Notación y Asset IDs](#6-notación-y-asset-ids)
7. [Info Endpoint — Datos de Mercado y Cuenta](#7-info-endpoint--datos-de-mercado-y-cuenta)
   - 7.1 [Metadata de Perpetuos](#71-metadata-de-perpetuos)
   - 7.2 [Precios y Contexto de Mercado](#72-precios-y-contexto-de-mercado)
   - 7.3 [Order Book (L2)](#73-order-book-l2)
   - 7.4 [Candles](#74-candles)
   - 7.5 [Estado de Cuenta del Usuario](#75-estado-de-cuenta-del-usuario)
   - 7.6 [Órdenes Abiertas](#76-órdenes-abiertas)
   - 7.7 [Historial de Fills](#77-historial-de-fills)
   - 7.8 [Funding History](#78-funding-history)
   - 7.9 [Estado de una Orden (por OID o CLOID)](#79-estado-de-una-orden-por-oid-o-cloid)
   - 7.10 [Active Asset Data](#710-active-asset-data)
   - 7.11 [Rate Limit del Usuario](#711-rate-limit-del-usuario)
8. [Exchange Endpoint — Operaciones de Trading](#8-exchange-endpoint--operaciones-de-trading)
   - 8.1 [Colocar una Orden](#81-colocar-una-orden)
   - 8.2 [Cancelar Orden(es)](#82-cancelar-órdenes)
   - 8.3 [Cancelar por CLOID](#83-cancelar-por-cloid)
   - 8.4 [Modificar una Orden](#84-modificar-una-orden)
   - 8.5 [Modificar Múltiples Órdenes](#85-modificar-múltiples-órdenes)
   - 8.6 [Actualizar Apalancamiento](#86-actualizar-apalancamiento)
   - 8.7 [Actualizar Margen Aislado](#87-actualizar-margen-aislado)
   - 8.8 [TWAP Order](#88-twap-order)
   - 8.9 [Cancelar TWAP Order](#89-cancelar-twap-order)
   - 8.10 [Dead Man's Switch (Schedule Cancel)](#810-dead-mans-switch-schedule-cancel)
   - 8.11 [Noop (Invalidar Nonce)](#811-noop-invalidar-nonce)
   - 8.12 [Transferencias (USDC, Spot, entre cuentas)](#812-transferencias)
   - 8.13 [Aprobar API Wallet](#813-aprobar-api-wallet)
   - 8.14 [Reservar Actions adicionales](#814-reservar-actions-adicionales)
9. [WebSocket — Streams en Tiempo Real](#9-websocket--streams-en-tiempo-real)
   - 9.1 [Conexión y Reconexión](#91-conexión-y-reconexión)
   - 9.2 [Subscripciones Disponibles](#92-subscripciones-disponibles)
   - 9.3 [Tipos de Datos WebSocket](#93-tipos-de-datos-websocket)
   - 9.4 [Heartbeat y Timeouts](#94-heartbeat-y-timeouts)
10. [Rate Limits](#10-rate-limits)
11. [Errores Comunes y Order Statuses](#11-errores-comunes-y-order-statuses)
12. [Herramientas del Ecosistema](#12-herramientas-del-ecosistema)
13. [Patrones Recomendados para Bots](#13-patrones-recomendados-para-bots)

---

## 1. Arquitectura General

Hyperliquid Core (HyperCore) es un exchange descentralizado de order book on-chain con finality de un bloque. Opera con dos capas:

- **HyperCore**: Libros de órdenes on-chain para perpetuos y spot. Soporta ~200.000 órdenes/segundo.
- **HyperEVM**: Capa EVM compatible con Ethereum sobre HyperCore.

Para un bot de trading de perpetuos, solo se necesita interactuar con **HyperCore** a través de dos endpoints principales:

| Endpoint | Uso |
|---|---|
| `POST /info` | Lectura de datos de mercado y cuenta (sin autenticación) |
| `POST /exchange` | Escritura: órdenes, cancelaciones, transferencias (requiere firma EIP-712) |
| `wss://.../ws` | WebSocket para datos en tiempo real |

---

## 2. URLs de Red (Mainnet vs Testnet)

| Red | REST Base URL | WebSocket URL |
|---|---|---|
| **Mainnet** | `https://api.hyperliquid.xyz` | `wss://api.hyperliquid.xyz/ws` |
| **Testnet** | `https://api.hyperliquid-testnet.xyz` | `wss://api.hyperliquid-testnet.xyz/ws` |

> **Nota importante**: Todos los ejemplos de la documentación oficial usan la URL de mainnet. Para testnet, solo reemplaza el dominio. Además, en los actions que requieren firma, el campo `hyperliquidChain` debe ser `"Mainnet"` o `"Testnet"` según corresponda.

---

## 3. SDKs y Librerías

### SDKs Oficiales y de la Comunidad

| Lenguaje | Librería | URL |
|---|---|---|
| Python (oficial) | hyperliquid-python-sdk | https://github.com/hyperliquid-dex/hyperliquid-python-sdk |
| Rust (comunidad) | hypersdk (Infinite Field) | https://github.com/infinitefield/hypersdk |
| TypeScript (comunidad) | nktkas/hyperliquid | https://github.com/nktkas/hyperliquid |
| TypeScript (comunidad) | nomeida/hyperliquid | https://github.com/nomeida/hyperliquid |
| Multi-lenguaje | CCXT | https://docs.ccxt.com/#/exchanges/hyperliquid |

### APIs de Terceros (No Rate-Limited)

- **Dwellir gRPC**: https://www.dwellir.com/docs/hyperliquid/grpc/
- **Dwellir WebSocket**: https://www.dwellir.com/docs/hyperliquid/websocket-api
- **Hydromancer**: https://docs.hydromancer.xyz/ (APIs indexadas, datos históricos)

> **Recomendación**: El Python SDK oficial es la referencia más completa. Contiene ejemplos de firma EIP-712, API wallets, vaults, y estrategias de batching. Revisar antes de implementar firmas manualmente.

---

## 4. Autenticación y Firma

Las operaciones de escritura (`/exchange`) requieren una firma **EIP-712** (typed data signing). No existe API key/secret tradicional: la identidad es la dirección Ethereum del wallet.

### Flujo de Firma

1. Construir el objeto `action` según el tipo de operación.
2. Firmar con EIP-712 usando la clave privada del usuario **o** una API wallet (agent wallet) previamente aprobada.
3. Enviar al endpoint `POST /exchange` con el cuerpo:

```json
{
  "action": { ... },
  "nonce": <timestamp_ms>,
  "signature": { "r": "0x...", "s": "0x...", "v": 27 },
  "vaultAddress": "0x..." // opcional: si operas en nombre de un vault/subaccount
}
```

### Ejemplo de EIP-712 Domain (para spotSend)

```json
{
  "domain": {
    "name": "HyperliquidSignTransaction",
    "version": "1",
    "chainId": 42161,
    "verifyingContract": "0x0000000000000000000000000000000000000000"
  },
  "primaryType": "HyperliquidTransaction:SpotSend",
  "types": {
    "HyperliquidTransaction:SpotSend": [
      { "name": "hyperliquidChain", "type": "string" },
      { "name": "destination", "type": "string" },
      { "name": "token", "type": "string" },
      { "name": "amount", "type": "string" },
      { "name": "time", "type": "uint64" }
    ]
  }
}
```

> **Importante**: El Python SDK genera automáticamente las firmas correctas para todos los tipos de action. Se recomienda usarlo como referencia para implementaciones en otros lenguajes.

---

## 5. Nonces y API Wallets (Agent Wallets)

### Sistema de Nonces de Hyperliquid

A diferencia de Ethereum (nonce secuencial exacto), Hyperliquid almacena los **100 nonces más altos** por dirección. Las reglas son:

- El nonce debe ser **mayor** al nonce más bajo almacenado.
- El nonce nunca debe haber sido usado antes.
- Los nonces deben estar en el rango `(T - 2 días, T + 1 día)` donde T es el timestamp del bloque.
- **Práctica recomendada**: usar `Date.now()` (timestamp en millisegundos) como nonce, incrementando un contador atómico para evitar colisiones.

### API Wallets (Agent Wallets)

Permiten que una cuenta maestra delegue la firma a otras claves privadas. Son la forma estándar de operar bots.

**Características:**
- La cuenta maestra puede aprobar múltiples API wallets.
- Se pueden crear **1 wallet sin nombre** y hasta **3 con nombre** por cuenta maestra.
- Las API wallets firman los actions pero las consultas de datos deben realizarse con la dirección real de la cuenta maestra (no la del agent).
- Los nonces son **por firmante** (no por cuenta), así que distintos agents comparten su propio espacio de nonces.

**Aprobar una API wallet:**

```json
POST https://api.hyperliquid.xyz/exchange
{
  "action": {
    "type": "approveAgent",
    "hyperliquidChain": "Mainnet",
    "signatureChainId": "0xa4b1",
    "agentAddress": "0x<nueva_wallet>",
    "agentName": "bot_principal",
    "nonce": <timestamp_ms>
  },
  "nonce": <timestamp_ms>,
  "signature": { ... }
}
```

**Advertencia crítica**: No reutilizar direcciones de API wallets. Cuando un agent es dado de baja (deregistrado), su historial de nonces puede ser eliminado, lo que abre la puerta a replay attacks con actions previamente firmadas.

**Estrategia recomendada para múltiples subaccounts:**
- Usar una API wallet separada por proceso de trading.
- Un proceso = una clave privada = un espacio de nonces propio.
- No cruzar API wallets entre distintos subaccounts.

---

## 6. Notación y Asset IDs

### Perpetuos — Nombre de Coin

Para el mercado de perpetuos del primer dex (el estándar de Hyperliquid), el nombre del coin es el retornado por el campo `name` en la respuesta de `meta`. Ejemplos: `"BTC"`, `"ETH"`, `"SOL"`.

Para perpetuos de HIP-3 (builder-deployed DEXs), el coin tiene el prefijo del nombre del dex: `"xyz:XYZ100"`.

### Asset Index (para el Exchange Endpoint)

El endpoint `/exchange` requiere el campo `asset` como índice numérico, **no** el nombre del coin:

- **Perpetuos**: índice en el array `universe` retornado por `meta`. BTC generalmente es `0`, ETH es `1`, etc.
- **Spot**: usar `10000 + index` donde `index` es el índice en `spotMeta.universe`.

```python
# Ejemplo: obtener el asset index de ETH en perpetuos
meta = requests.post("https://api.hyperliquid.xyz/info", json={"type": "meta"}).json()
eth_index = next(i for i, x in enumerate(meta["universe"]) if x["name"] == "ETH")
# eth_index = 1 (en mainnet)
```

### Tick Size y Lot Size

Cada perpetuo tiene:
- `szDecimals`: número de decimales para el tamaño de posición.
- `maxLeverage`: apalancamiento máximo permitido.
- `onlyIsolated` / `marginMode`: restricciones de tipo de margen.

Estos datos se obtienen del endpoint `meta`.

---

## 7. Info Endpoint — Datos de Mercado y Cuenta

**URL**: `POST https://api.hyperliquid.xyz/info`  
**Headers**: `Content-Type: application/json`  
**Autenticación**: Ninguna (todos los endpoints `/info` son públicos)

---

### 7.1 Metadata de Perpetuos

#### Obtener universe y margin tables

```json
// Request
{ "type": "meta", "dex": "" }

// Response
{
  "universe": [
    { "name": "BTC", "szDecimals": 5, "maxLeverage": 50 },
    { "name": "ETH", "szDecimals": 4, "maxLeverage": 50 },
    { "name": "HPOS", "szDecimals": 0, "maxLeverage": 3, "onlyIsolated": true }
  ],
  "marginTables": [
    [50, {
      "description": "",
      "marginTiers": [{ "lowerBound": "0.0", "maxLeverage": 50 }]
    }]
  ]
}
```

> El campo `dex` es opcional y por defecto es el primer perp dex (vacío `""`). Para HIP-3 DEXs, pasar el nombre del dex.

#### Obtener metadata de todos los dexs

```json
{ "type": "allPerpMetas" }
// Retorna array con metadata + asset contexts de todos los dexs
```

#### Listar todos los perp dexs

```json
{ "type": "perpDexs" }
// Retorna: [null, { "name": "test", "fullName": "test dex", "deployer": "0x...", ... }]
// null representa el primer perp dex (Hyperliquid estándar)
```

---

### 7.2 Precios y Contexto de Mercado

#### Mid prices de todos los coins

```json
// Request
{ "type": "allMids", "dex": "" }

// Response
{ "BTC": "104500.0", "ETH": "3520.5", "SOL": "185.2", ... }
```

> Si el libro está vacío, se usa el precio del último trade como fallback.

#### Asset contexts (mark price, funding, open interest, etc.)

```json
// Request
{ "type": "metaAndAssetCtxs", "dex": "" }

// Response: array de dos elementos [metadata, [ctx_asset_0, ctx_asset_1, ...]]
// Cada ctx contiene:
{
  "dayNtlVlm": "1169046.29406",  // volumen notional 24h
  "funding": "0.0000125",         // funding rate actual
  "impactPxs": ["14.30", "14.34"], // impact bid/ask
  "markPx": "14.3161",            // mark price
  "midPx": "14.314",              // mid price
  "openInterest": "688.11",       // open interest en base units
  "oraclePx": "14.32",            // precio oracle
  "premium": "0.00031774",        // premium sobre oracle
  "prevDayPx": "15.322"           // precio hace 24h
}
```

---

### 7.3 Order Book (L2)

```json
// Request
{
  "type": "l2Book",
  "coin": "BTC",
  "nSigFigs": 5,    // opcional: 2, 3, 4, 5 o null (precisión completa)
  "mantissa": null  // opcional: solo si nSigFigs=5; valores: 1, 2, 5
}

// Response
{
  "coin": "BTC",
  "time": 1754450974231,
  "levels": [
    // levels[0] = bids (descending price)
    [
      { "px": "113377.0", "sz": "7.6699", "n": 17 },
      { "px": "113376.0", "sz": "4.13714", "n": 8 }
    ],
    // levels[1] = asks (ascending price)
    [
      { "px": "113397.0", "sz": "0.11543", "n": 3 }
    ]
  ]
}
```

> Retorna como máximo 20 niveles por lado. Usar WebSocket `l2Book` para actualizaciones en tiempo real.

---

### 7.4 Candles

```json
// Request
{
  "type": "candleSnapshot",
  "req": {
    "coin": "BTC",
    "interval": "15m",
    "startTime": 1681923600000,
    "endTime": 1681924500000
  }
}

// Response
[{
  "t": 1681923600000,  // open time (ms)
  "T": 1681924499999,  // close time (ms)
  "s": "BTC",
  "i": "15m",
  "o": "29295.0",  // open
  "c": "29258.0",  // close
  "h": "29309.0",  // high
  "l": "29250.0",  // low
  "v": "0.98639",  // volume (base units)
  "n": 189         // número de trades
}]
```

**Intervalos soportados**: `"1m"`, `"3m"`, `"5m"`, `"15m"`, `"30m"`, `"1h"`, `"2h"`, `"4h"`, `"8h"`, `"12h"`, `"1d"`, `"3d"`, `"1w"`, `"1M"`

> Solo están disponibles las 5000 candles más recientes. Para datos históricos completos, usar Hydromancer o Allium.

---

### 7.5 Estado de Cuenta del Usuario

#### Clearinghouse State (posiciones + resumen de margen)

```json
// Request
{
  "type": "clearinghouseState",
  "user": "0xADDRESS...",
  "dex": ""
}

// Response
{
  "assetPositions": [
    {
      "position": {
        "coin": "ETH",
        "szi": "0.0335",           // tamaño: positivo = long, negativo = short
        "entryPx": "2986.3",
        "positionValue": "100.02",
        "unrealizedPnl": "-0.013",
        "returnOnEquity": "-0.002",
        "liquidationPx": "2866.26",
        "marginUsed": "4.967826",
        "maxLeverage": 50,
        "leverage": {
          "type": "isolated",  // o "cross"
          "value": 20,
          "rawUsd": "-95.05"
        },
        "cumFunding": {
          "allTime": "514.08",
          "sinceChange": "0.0",
          "sinceOpen": "0.0"
        }
      },
      "type": "oneWay"
    }
  ],
  "marginSummary": {
    "accountValue": "13109.48",
    "totalNtlPos": "100.02",
    "totalRawUsd": "13009.45",
    "totalMarginUsed": "4.967826"
  },
  "crossMarginSummary": { ... },
  "crossMaintenanceMarginUsed": "0.0",
  "withdrawable": "13104.51",
  "time": 1708622398623
}
```

> **Nota**: Para cuentas con modo unified account o portfolio margin, usar el endpoint `spotClearinghouseState` para obtener el balance combinado.

#### Active Asset Data (datos específicos por coin para el usuario)

```json
// Request
{
  "type": "activeAssetData",
  "user": "0xADDRESS...",
  "coin": "ETH"
}

// Response
{
  "user": "0xADDRESS...",
  "coin": "ETH",
  "leverage": { "type": "cross", "value": 3 },
  "maxTradeSzs": ["24836370.44", "24836370.44"],  // [buy, sell]
  "availableToTrade": ["37019438.02", "37019438.02"],
  "markPx": "4.4716"
}
```

---

### 7.6 Órdenes Abiertas

```json
// Request (básico)
{ "type": "openOrders", "user": "0xADDRESS...", "dex": "" }

// Response
[{
  "coin": "BTC",
  "side": "A",           // "A" = Ask/Sell, "B" = Bid/Buy
  "limitPx": "29792.0",
  "sz": "0.01",
  "oid": 91490942,       // order id
  "timestamp": 1681247412573
}]

// Request (con info frontend)
{ "type": "frontendOpenOrders", "user": "0xADDRESS...", "dex": "" }

// Response ampliado incluye: orderType, origSz, reduceOnly, isTrigger, triggerPx, etc.
```

---

### 7.7 Historial de Fills

```json
// Fills recientes (hasta 2000 más recientes)
{ "type": "userFills", "user": "0xADDRESS...", "aggregateByTime": false }

// Fills por rango de tiempo (hasta 2000 fills, solo los 10000 más recientes)
{
  "type": "userFillsByTime",
  "user": "0xADDRESS...",
  "startTime": 1681222254710,
  "endTime": 1681222854710,
  "aggregateByTime": false
}

// Response: array de fills
[{
  "coin": "AVAX",
  "px": "18.435",
  "sz": "93.53",
  "side": "B",           // "B" = Buy, "A" = Sell
  "time": 1681222254710,
  "startPosition": "26.86",
  "dir": "Open Long",    // descripción legible
  "closedPnl": "0.0",
  "hash": "0xa166...",   // L1 tx hash
  "oid": 90542681,
  "crossed": false,       // true = taker, false = maker
  "fee": "0.01",          // total fee (negativo = rebate)
  "feeToken": "USDC",
  "tid": 118906512037719  // trade id único
}]
```

> **Paginación**: Para obtener más de 2000 fills, usar el último timestamp retornado como nuevo `startTime`.

---

### 7.8 Funding History

#### Historial de pagos de funding del usuario

```json
{
  "type": "userFunding",
  "user": "0xADDRESS...",
  "startTime": 1681222254710,
  "endTime": 1681922254710
}

// Response
[{
  "delta": {
    "type": "funding",
    "coin": "ETH",
    "usdc": "-3.625312",    // negativo = pagaste funding
    "szi": "49.1477",
    "fundingRate": "0.0000417"
  },
  "hash": "0xa166...",
  "time": 1681222254710
}]
```

#### Funding rates históricas (por coin)

```json
{
  "type": "fundingHistory",
  "coin": "ETH",
  "startTime": 1681222254710
}

// Response
[{
  "coin": "ETH",
  "fundingRate": "-0.00022196",
  "premium": "-0.00052196",
  "time": 1683849600076
}]
```

#### Funding rates predichas (multi-venue)

```json
{ "type": "predictedFundings" }

// Response: compara HL con Binance, Bybit, etc.
[
  ["AVAX", [
    ["BinPerp", { "fundingRate": "0.0001", "nextFundingTime": 1733961600000 }],
    ["HlPerp",  { "fundingRate": "0.0000125", "nextFundingTime": 1733958000000 }],
    ["BybitPerp", { "fundingRate": "0.0001", "nextFundingTime": 1733961600000 }]
  ]],
  ...
]
```

---

### 7.9 Estado de una Orden (por OID o CLOID)

```json
// Request
{
  "type": "orderStatus",
  "user": "0xADDRESS...",
  "oid": 91490942  // o cloid como string hex "0x1234..."
}

// Response
{
  "status": "order",
  "order": {
    "order": {
      "coin": "ETH",
      "side": "A",
      "limitPx": "2412.7",
      "sz": "0.0",
      "oid": 1,
      "timestamp": 1724361546645,
      "isTrigger": false,
      "triggerPx": "0.0",
      "reduceOnly": true,
      "orderType": "Market",
      "origSz": "0.0076",
      "tif": "FrontendMarket",
      "cloid": null
    },
    "status": "filled",   // ver tabla de statuses abajo
    "statusTimestamp": 1724361546645
  }
}
```

---

### 7.10 Active Asset Data

```json
// Request
{
  "type": "activeAssetData",
  "user": "0xADDRESS...",
  "coin": "BTC"
}

// Response
{
  "user": "0xADDRESS...",
  "coin": "BTC",
  "leverage": { "type": "cross", "value": 10 },
  "maxTradeSzs": ["1.5", "1.5"],
  "availableToTrade": ["2.3", "2.3"],
  "markPx": "104500.0"
}
```

---

### 7.11 Rate Limit del Usuario

```json
// Request
{ "type": "userRateLimit", "user": "0xADDRESS..." }

// Response
{
  "cumVlm": "2854574.59",       // volumen acumulado en USDC
  "nRequestsUsed": 2890,         // requests usados (= max(0, usados - reservados))
  "nRequestsCap": 2864574,       // cap basado en volumen
  "nRequestsSurplus": 0          // requests extra reservados sin usar
}
```

---

## 8. Exchange Endpoint — Operaciones de Trading

**URL**: `POST https://api.hyperliquid.xyz/exchange`  
**Headers**: `Content-Type: application/json`  
**Autenticación**: Firma EIP-712 requerida en todos los requests.

**Estructura del request**:
```json
{
  "action": { ... },         // el action específico
  "nonce": 1708622398623,    // timestamp ms actual
  "signature": { "r": "0x...", "s": "0x...", "v": 27 },
  "vaultAddress": "0x...",   // OPCIONAL: vault/subaccount
  "expiresAfter": 1708622998623  // OPCIONAL: ms timestamp de expiración
}
```

> **`expiresAfter`**: Actions cancelados por este campo consumen 5x el rate limit normal. Úsalo solo cuando sea necesario.

---

### 8.1 Colocar una Orden

```json
{
  "action": {
    "type": "order",
    "orders": [{
      "a": 1,          // asset index (ETH = 1 en mainnet perp)
      "b": true,       // isBuy: true = comprar/long, false = vender/short
      "p": "3000.0",   // price (string)
      "s": "0.01",     // size (string, en base units)
      "r": false,      // reduceOnly
      "t": {
        // Limit order:
        "limit": { "tif": "Gtc" }  // "Gtc" | "Ioc" | "Alo"
        
        // O trigger order (TP/SL):
        // "trigger": {
        //   "isMarket": true,
        //   "triggerPx": "2900.0",
        //   "tpsl": "sl"   // "tp" o "sl"
        // }
      },
      "c": "0x1234567890abcdef1234567890abcdef"  // cloid (optional, 128-bit hex)
    }],
    "grouping": "na",  // "na" | "normalTpsl" | "positionTpsl"
    "builder": {       // OPCIONAL: fee para builder
      "b": "0xBUILDER_ADDRESS",
      "f": 10  // tenths of basis point (10 = 1bp)
    }
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

**TIF (Time-in-Force)**:
| TIF | Comportamiento |
|---|---|
| `Gtc` | Good Till Canceled — orden rest en el book hasta ser llenada o cancelada |
| `Ioc` | Immediate or Cancel — llena lo que puede, cancela el resto |
| `Alo` | Add Liquidity Only (Post-Only) — se cancela si haría match inmediato |

**Response exitoso (resting)**:
```json
{
  "status": "ok",
  "response": {
    "type": "order",
    "data": { "statuses": [{ "resting": { "oid": 77738308 } }] }
  }
}
```

**Response exitoso (filled)**:
```json
{
  "status": "ok",
  "response": {
    "type": "order",
    "data": { "statuses": [{ "filled": { "totalSz": "0.02", "avgPx": "1891.4", "oid": 77747314 } }] }
  }
}
```

**Response con error**:
```json
{
  "status": "ok",
  "response": {
    "type": "order",
    "data": { "statuses": [{ "error": "Order must have minimum value of $10." }] }
  }
}
```

> **Órdenes de mercado**: Usar `"p": "0"` con `"t": {"limit": {"tif": "Ioc"}}` para simular market orders. La orden se llenará al mejor precio disponible y el resto se cancela.

> **Batching**: Se pueden enviar múltiples órdenes en el array `orders`. Un batch con N órdenes cuenta como 1 request para el IP rate limit, pero N requests para el address-based rate limit.

---

### 8.2 Cancelar Orden(es)

```json
{
  "action": {
    "type": "cancel",
    "cancels": [
      { "a": 1, "o": 77738308 }  // a = asset index, o = oid
    ]
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

**Response exitoso**:
```json
{ "status": "ok", "response": { "type": "cancel", "data": { "statuses": ["success"] } } }
```

---

### 8.3 Cancelar por CLOID

```json
{
  "action": {
    "type": "cancelByCloid",
    "cancels": [
      { "asset": 1, "cloid": "0x1234567890abcdef1234567890abcdef" }
    ]
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

---

### 8.4 Modificar una Orden

Permite cambiar precio, tamaño, o tipo de una orden existente sin cancelar y re-colocar.

```json
{
  "action": {
    "type": "modify",
    "oid": 77738308,  // o cloid como string
    "order": {
      "a": 1,
      "b": true,
      "p": "3100.0",   // nuevo precio
      "s": "0.02",     // nuevo tamaño
      "r": false,
      "t": { "limit": { "tif": "Gtc" } }
    }
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

---

### 8.5 Modificar Múltiples Órdenes

```json
{
  "action": {
    "type": "batchModify",
    "modifies": [
      { "oid": 77738308, "order": { "a": 1, "b": true, "p": "3100.0", "s": "0.02", "r": false, "t": { "limit": { "tif": "Gtc" } } } },
      { "oid": 77738309, "order": { "a": 3, "b": false, "p": "180.0", "s": "1.0", "r": false, "t": { "limit": { "tif": "Gtc" } } } }
    ]
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

---

### 8.6 Actualizar Apalancamiento

```json
{
  "action": {
    "type": "updateLeverage",
    "asset": 1,         // asset index
    "isCross": true,    // true = cross, false = isolated
    "leverage": 10      // entero: el nuevo apalancamiento
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

**Response**: `{ "status": "ok", "response": { "type": "default" } }`

> El apalancamiento debe respetar el `maxLeverage` del asset y los `marginTiers` según el tamaño de posición.

---

### 8.7 Actualizar Margen Aislado

Agregar o quitar margen de una posición aislada.

```json
{
  "action": {
    "type": "updateIsolatedMargin",
    "asset": 1,
    "isBuy": true,
    "ntli": 1000000  // en millonésimas de USDC (1000000 = 1 USDC)
    // Positivo = agregar margen, negativo = quitar margen
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

> **Alternativa para leverage exacto**: Usar `"type": "topUpIsolatedOnlyMargin"` con campo `"leverage"` como string float en lugar de `ntli`.

---

### 8.8 TWAP Order

Ejecuta una orden distribuida en el tiempo.

```json
{
  "action": {
    "type": "twapOrder",
    "twap": {
      "a": 1,         // asset index
      "b": true,      // isBuy
      "s": "1.0",     // total size
      "r": false,     // reduceOnly
      "m": 30,        // minutes (duración total)
      "t": true       // randomize (randomizar tiempos de ejecución)
    }
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

**Response**:
```json
{
  "status": "ok",
  "response": {
    "type": "twapOrder",
    "data": { "status": { "running": { "twapId": 77738308 } } }
  }
}
```

---

### 8.9 Cancelar TWAP Order

```json
{
  "action": {
    "type": "twapCancel",
    "a": 1,        // asset index
    "t": 77738308  // twap_id
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

---

### 8.10 Dead Man's Switch (Schedule Cancel)

Programa una cancelación masiva de todas las órdenes abiertas en un momento futuro. Esencial para bots que deben garantizar que las órdenes se cancelen si el bot se cae.

```json
{
  "action": {
    "type": "scheduleCancel",
    "time": 1708622998623  // timestamp ms futuro (mínimo 5 segundos en el futuro)
    // Omitir "time" para cancelar un schedule existente
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

**Límites**:
- El tiempo debe ser al menos 5 segundos en el futuro.
- Máximo 10 triggers por día (reset a 00:00 UTC).
- Cuando se dispara, **todos** los open orders son cancelados.

**Patrón recomendado para bots**: Refrescar el dead man's switch cada N segundos (ej: cada 30s con time = now + 60s). Si el bot se cae, las órdenes se cancelan automáticamente.

---

### 8.11 Noop (Invalidar Nonce)

No hace nada, pero marca el nonce como usado. Más eficiente que cancelar órdenes en vuelo cuando se quiere invalidar transacciones pendientes.

```json
{
  "action": { "type": "noop" },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

---

### 8.12 Transferencias

#### USDC entre usuarios (Core USDC Transfer)

```json
{
  "action": {
    "type": "usdSend",
    "hyperliquidChain": "Mainnet",
    "signatureChainId": "0xa4b1",
    "destination": "0xDESTINATION...",
    "amount": "100.0",
    "time": 1708622398623
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

#### Spot a Perp (y viceversa)

```json
{
  "action": {
    "type": "usdClassTransfer",
    "hyperliquidChain": "Mainnet",
    "signatureChainId": "0xa4b1",
    "amount": "500.0",
    "toPerp": true,  // true = spot -> perp, false = perp -> spot
    "nonce": 1708622398623
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

#### Withdraw al bridge de Arbitrum

```json
{
  "action": {
    "type": "withdraw3",
    "hyperliquidChain": "Mainnet",
    "signatureChainId": "0xa4b1",
    "amount": "1000.0",
    "time": 1708622398623,
    "destination": "0xARBITRUM_ADDRESS..."
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

> Fee de $1 por withdrawal. El proceso tarda ~5 minutos.

---

### 8.13 Aprobar API Wallet

Ver Sección 5.

---

### 8.14 Reservar Actions Adicionales

Para aumentar el rate limit por address sin trading, se puede pagar 0.0005 USDC por request adicional (del balance de perps).

```json
{
  "action": {
    "type": "reserveRequestWeight",
    "weight": 100  // número de requests adicionales a reservar
  },
  "nonce": 1708622398623,
  "signature": { ... }
}
```

---

## 9. WebSocket — Streams en Tiempo Real

### 9.1 Conexión y Reconexión

```javascript
// Conexión
const ws = new WebSocket("wss://api.hyperliquid.xyz/ws");

// Suscribirse a trades de BTC
ws.onopen = () => {
  ws.send(JSON.stringify({
    "method": "subscribe",
    "subscription": { "type": "trades", "coin": "BTC" }
  }));
};

// Recibir datos
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  console.log(msg.channel, msg.data);
};
```

**ACK de suscripción**:
```json
{ "channel": "subscriptionResponse", "data": { "method": "subscribe", "subscription": { "type": "trades", "coin": "BTC" } } }
```

> **Crítico para bots**: Manejar desconexiones del servidor y reconectar automáticamente. Los datos perdidos durante la reconexión estarán disponibles en el snapshot del ACK al reconectar (marcados con `isSnapshot: true`).

### 9.2 Subscripciones Disponibles

| Tipo | Subscription Message | Descripción |
|---|---|---|
| `allMids` | `{"type":"allMids","dex":""}` | Mid prices de todos los coins |
| `l2Book` | `{"type":"l2Book","coin":"BTC"}` | Order book completo por coin |
| `bbo` | `{"type":"bbo","coin":"BTC"}` | Best bid/offer, solo si cambia por bloque |
| `trades` | `{"type":"trades","coin":"BTC"}` | Trades públicos del mercado |
| `candle` | `{"type":"candle","coin":"BTC","interval":"1m"}` | Candles en tiempo real |
| `clearinghouseState` | `{"type":"clearinghouseState","user":"0x...","dex":""}` | Estado de cuenta (posiciones + margen) |
| `openOrders` | `{"type":"openOrders","user":"0x...","dex":""}` | Órdenes abiertas del usuario |
| `orderUpdates` | `{"type":"orderUpdates","user":"0x..."}` | Actualizaciones de estado de órdenes |
| `userEvents` | `{"type":"userEvents","user":"0x..."}` | Fills, funding, liquidaciones |
| `userFills` | `{"type":"userFills","user":"0x..."}` | Stream de fills del usuario |
| `userFundings` | `{"type":"userFundings","user":"0x..."}` | Pagos de funding |
| `activeAssetCtx` | `{"type":"activeAssetCtx","coin":"BTC"}` | Contexto de mercado por coin |
| `activeAssetData` | `{"type":"activeAssetData","user":"0x...","coin":"BTC"}` | Datos de posición/leverage por coin |
| `twapStates` | `{"type":"twapStates","user":"0x...","dex":""}` | Estado de TWAPs activos |
| `userTwapSliceFills` | `{"type":"userTwapSliceFills","user":"0x..."}` | Fills de slices TWAP |
| `notification` | `{"type":"notification","user":"0x..."}` | Notificaciones del usuario |
| `spotState` | `{"type":"spotState","user":"0x..."}` | Balance en spot |

#### Unsubscribe

```json
{ "method": "unsubscribe", "subscription": { "type": "trades", "coin": "BTC" } }
```

El objeto `subscription` debe coincidir exactamente con el usado al suscribirse.

### 9.3 Tipos de Datos WebSocket

```typescript
// Trade
interface WsTrade {
  coin: string;
  side: string;   // "B" o "A"
  px: string;
  sz: string;
  hash: string;
  time: number;
  tid: number;    // trade id único (50-bit hash de buyer_oid + seller_oid)
  users: [string, string];  // [buyer, seller]
}

// Order Book
interface WsBook {
  coin: string;
  levels: [Array<WsLevel>, Array<WsLevel>];  // [bids, asks]
  time: number;
}

interface WsLevel {
  px: string;   // price
  sz: string;   // size
  n: number;    // number of orders
}

// Best Bid/Offer
interface WsBbo {
  coin: string;
  time: number;
  bbo: [WsLevel | null, WsLevel | null];  // [bid, ask]
}

// Candle
interface Candle {
  t: number;  // open ms
  T: number;  // close ms
  s: string;  // coin
  i: string;  // interval
  o: number;  // open
  c: number;  // close
  h: number;  // high
  l: number;  // low
  v: number;  // volume (base units)
  n: number;  // num trades
}

// Fill del usuario
interface WsFill {
  coin: string;
  px: string;
  sz: string;
  side: string;
  time: number;
  startPosition: string;
  dir: string;         // "Open Long", "Close Long", etc.
  closedPnl: string;
  hash: string;
  oid: number;
  crossed: boolean;    // true = taker
  fee: string;         // negativo = rebate
  tid: number;
  feeToken: string;
  builderFee?: string;
}

// Order update
interface WsOrder {
  order: WsBasicOrder;
  status: string;       // ver tabla de statuses
  statusTimestamp: number;
}

// User events (fills, funding, liquidation)
type WsUserEvent = 
  | { fills: WsFill[] }
  | { funding: WsUserFunding }
  | { liquidation: WsLiquidation }
  | { nonUserCancel: WsNonUserCancel[] };

interface WsUserFunding {
  time: number;
  coin: string;
  usdc: string;         // negativo = pagaste
  szi: string;
  fundingRate: string;
}
```

### 9.4 Heartbeat y Timeouts

- El servidor cierra la conexión si no recibe ningún mensaje en **60 segundos**.
- Para mantener la conexión activa, enviar un ping periódico:

```json
{ "method": "ping" }
```

El servidor responde con `{ "channel": "pong" }`.

**Patrón recomendado**: Enviar ping cada 20-30 segundos. Si no se recibe pong en 10 segundos, reconectar.

---

## 10. Rate Limits

### IP-Based (por IP)

| Tipo | Peso | Límite |
|---|---|---|
| Requests REST | pesos acumulados | 1200/minuto |
| `exchange` actions | 1 + floor(batch_len/40) | — |
| `info`: `l2Book`, `allMids`, `clearinghouseState`, `orderStatus` | 2 | — |
| `info`: `userRole` | 60 | — |
| Resto de `info` | 20 | — |
| `info` con paginación (fills, funding, etc.) | +peso por 20 items | — |
| `candleSnapshot` | +peso por 60 items | — |
| Explorer endpoints | 40 | — |

### WebSocket (por IP)

| Límite | Valor |
|---|---|
| Conexiones simultáneas | 10 |
| Nuevas conexiones por minuto | 30 |
| Total subscripciones | 1000 |
| Usuarios únicos en subscripciones de usuario | 10 |
| Mensajes enviados a HL por minuto (todos los ws) | 2000 |
| Inflight post messages simultáneos | 100 |

### Address-Based (por dirección)

- **Lógica**: 1 request por 1 USDC de volumen acumulado desde el inicio de la cuenta.
- **Buffer inicial**: 10.000 requests.
- **Cuando se agota**: 1 request cada 10 segundos.
- **Cancelaciones**: límite aumentado a `min(limit + 100000, limit * 2)` para que siempre se puedan cancelar órdenes.
- **Batching**: Un batch de N órdenes = 1 request para IP, pero N requests para address.
- **Open orders**: Máx. 1000 + 1 por cada 5M USDC de volumen, tope en 5000.

### Congestion Dinámica

Durante alta congestión, las direcciones están limitadas a usar 2x su porcentaje de maker share del día anterior.

---

## 11. Errores Comunes y Order Statuses

### Order Statuses

| Status | Descripción |
|---|---|
| `open` | Colocada exitosamente, en el book |
| `filled` | Completamente llenada |
| `canceled` | Cancelada por el usuario |
| `triggered` | Trigger order activada |
| `rejected` | Rechazada al momento de colocarla |
| `marginCanceled` | Cancelada por margen insuficiente |
| `selfTradeCanceled` | Cancelada por self-trade prevention |
| `reduceOnlyCanceled` | Cancelada porque no reduce posición |
| `siblingFilledCanceled` | TP/SL cancelado por fill del sibling |
| `delistedCanceled` | Cancelada por delisting del asset |
| `liquidatedCanceled` | Cancelada por liquidación |
| `scheduledCancel` | Cancelada por dead man's switch |
| `openInterestCapCanceled` | Cancelada por cap de OI |
| `tickRejected` | Precio inválido (no respeta tick size) |
| `minTradeNtlRejected` | Notional < mínimo ($10) |
| `perpMarginRejected` | Margen insuficiente |
| `badAloPxRejected` | Post-only haría match inmediato |
| `iocCancelRejected` | IOC no pudo hacer match |
| `badTriggerPxRejected` | Precio de TP/SL inválido |
| `marketOrderNoLiquidityRejected` | Sin liquidez para market order |
| `oracleRejected` | Precio demasiado lejos del oracle |

### Errores Comunes en el Exchange

- **"Order must have minimum value of $10."**: El notional de la orden (precio × tamaño) es menor a $10.
- **"Order was never placed, already canceled, or filled."**: Al intentar cancelar una orden que ya no existe.
- Respuestas con `"status": "ok"` pero `"error"` dentro del array de statuses son errores a nivel de orden, no de request.

---

## 12. Herramientas del Ecosistema

### Analytics de Perpetuos

| Herramienta | URL | Descripción |
|---|---|---|
| ASXN HyperScreener | https://hyperscreener.asxn.xyz | Más completa para perps analytics |
| Coinalyze | https://coinalyze.net/markets/?exchange=H | OI y funding histórico |
| Velo | https://velo.xyz/futures/ | Comparativa multi-exchange |
| Laevitas | https://app.laevitas.ch/exchanges/perpswaps/HYPERLIQUID | Screener de perps |
| DefiLlama | https://defillama.com/perps/chains/hyperliquid | TVL y estadísticas generales |

### Explorers

| Herramienta | URL |
|---|---|
| Official Explorer | https://app.hyperliquid.xyz/explorer |
| HypurrScan | https://hypurrscan.io |
| Flowscan | https://flowscan.xyz |

### Indexación y Datos Históricos

| Herramienta | URL | Uso |
|---|---|---|
| Hydromancer | https://docs.hydromancer.xyz | APIs no rate-limited, datos históricos |
| Allium | https://docs.allium.so/historical-chains/supported-blockchains/hyperliquid | SQL sobre datos históricos |
| SonarX | https://docs.sonarx.com/datasets/HYPERLIQUID | Datasets |
| HypeDexer (Enigma) | https://www.hypedexer.com | Indexación |

### Custodios y Multisig (para institucionales)

| Servicio | URL | Tipo |
|---|---|---|
| Anchorage Digital | https://anchorage.com | Custodio institucional |
| FalconX | https://falconx.io | Custodio |
| HyperSig | https://www.hypersig.xyz | Multisig |
| Tholos | https://www.tholos.app | MPC |

---

## 13. Patrones Recomendados para Bots

### Arquitectura General

```
Bot Process
├── Nonce Manager (atomic counter, inicializado en Date.now())
├── Market Data Layer
│   ├── WebSocket (real-time: l2Book, trades, allMids)
│   └── REST polling (info endpoint como fallback)
├── Account State Layer
│   ├── WS: clearinghouseState, openOrders, userFills
│   └── REST: clearinghouseState (para reconciliación)
├── Order Manager
│   ├── Place/Cancel/Modify via /exchange
│   ├── CLOID tracking para status local
│   └── Dead Man's Switch (scheduleCancel, refresh cada 30s)
└── API Wallet (agent key separada del master wallet)
```

### Batching de Órdenes

```python
# Patrón recomendado del SDK: batch órdenes cada 100ms
# Separar ALO (maker) de IOC/GTC (taker) en batches distintos
# porque los validadores priorizan batches solo-ALO

while True:
    await asyncio.sleep(0.1)  # 100ms
    
    alo_orders = [o for o in pending if o.tif == "Alo"]
    gtc_ioc_orders = [o for o in pending if o.tif != "Alo"]
    
    if alo_orders:
        await exchange.place_batch(alo_orders)
    if gtc_ioc_orders:
        await exchange.place_batch(gtc_ioc_orders)
```

### Gestión del Nonce

```python
import time
from threading import Lock

class NonceManager:
    def __init__(self):
        self._counter = int(time.time() * 1000)
        self._lock = Lock()
    
    def next(self) -> int:
        with self._lock:
            now = int(time.time() * 1000)
            if now > self._counter:
                self._counter = now
            else:
                self._counter += 1
            return self._counter
```

### Dead Man's Switch para Bots

```python
async def maintain_dead_mans_switch(exchange, interval=30, ahead=60):
    """Renueva el dead man's switch periódicamente."""
    while True:
        expiry = int(time.time() * 1000) + (ahead * 1000)
        await exchange.schedule_cancel(time=expiry)
        await asyncio.sleep(interval)
```

### Reconexión WebSocket

```python
async def websocket_with_reconnect(url, subscriptions, handler):
    while True:
        try:
            async with websockets.connect(url) as ws:
                for sub in subscriptions:
                    await ws.send(json.dumps({"method": "subscribe", "subscription": sub}))
                
                # Heartbeat task
                async def ping():
                    while True:
                        await asyncio.sleep(20)
                        await ws.send(json.dumps({"method": "ping"}))
                
                asyncio.create_task(ping())
                
                async for msg in ws:
                    data = json.loads(msg)
                    if data.get("channel") != "pong":
                        await handler(data)
        except Exception as e:
            print(f"WS disconnected: {e}. Reconnecting in 1s...")
            await asyncio.sleep(1)
```

### Obtener Asset Index para Perpetuos

```python
def get_perp_asset_index(coin: str) -> int:
    """Obtiene el índice del asset para usar en /exchange."""
    resp = requests.post(
        "https://api.hyperliquid.xyz/info",
        json={"type": "meta", "dex": ""},
        headers={"Content-Type": "application/json"}
    ).json()
    
    for i, asset in enumerate(resp["universe"]):
        if asset["name"] == coin:
            return i
    raise ValueError(f"Coin {coin} not found in universe")

# BTC = 0, ETH = 1, etc. (verificar en mainnet antes de operar)
```

### Consejos de Latencia

- Usar WebSocket para market data en lugar de polling REST.
- Colocar el bot geográficamente cerca de los servidores de Hyperliquid (región US-East).
- Usar gRPC via Dwellir para menor latencia en datos.
- Batches de órdenes ALO separados de IOC/GTC (los validadores los priorizan).
- No reenviar cancelaciones cuyo resultado ya fue retornado por la API (evita desperdiciar rate limit durante alta congestión).

---

## Apéndice: Testnet

Para operar en testnet:

1. Usar `https://api.hyperliquid-testnet.xyz` (REST) y `wss://api.hyperliquid-testnet.xyz/ws` (WS).
2. En todos los actions, cambiar `"hyperliquidChain": "Mainnet"` por `"hyperliquidChain": "Testnet"`.
3. Obtener fondos de testnet desde la interfaz en https://app.hyperliquid-testnet.xyz.
4. Los asset indices pueden diferir entre mainnet y testnet — siempre consultar `meta` en la red objetivo.
5. Las mismas claves privadas/wallets funcionan en ambas redes (son independientes).

---

*Fuentes: Documentación oficial de Hyperliquid (https://hyperliquid.gitbook.io/hyperliquid-docs), actualizada a junio 2026.*
