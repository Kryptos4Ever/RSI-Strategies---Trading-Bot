# ⚡ Trading Bot Executor

Executor en vivo para estrategias **RSI Mean Reversion** sobre BTC/USDT, construido sobre la misma arquitectura de actores que el repositorio de backtesting (del cual importa las estrategias sin modificarlas).

## 📋 Tabla de Contenidos

- [Entornos soportados](#entornos-soportados)
- [Lanzadores](#lanzadores)
- [Instalación](#instalación)
- [Configuración (.env)](#configuración-env)
- [Estrategias](#estrategias)
- [Dashboard en vivo](#dashboard-en-vivo)
- [Persistencia](#persistencia)
- [Gestión de riesgo](#gestión-de-riesgo)
- [Notificaciones (Telegram)](#notificaciones-telegram)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Tests](#tests)

---

## 🌍 Entornos soportados

| Entorno | Descripción | Puerto dashboard |
|---|---|---|
| `papper` | Paper trading: wallet local simulada y market data público (WebSocket + REST) | `8001` |
| `hyperliquid_testnet` | Ejecución en Hyperliquid **testnet** (perps) | `8005` |
| `hyperliquid_mainnet` | Ejecución real en Hyperliquid **mainnet** (perps) | `8004` |

Los entornos `binance_spot` y `binance_testnet` fueron retirados. Binance solo persiste como proveedor público de market data para `papper`.

## 🚀 Lanzadores

### `main.py` — Lanzador único (recomendado)

Orquestador puro: construye todos los actores y los inyecta en `LiveEngine`.

```bash
python main.py --mode papper
python main.py --mode hyperliquid_testnet
python main.py --mode hyperliquid_mainnet --max-posiciones 5
```

Parámetros principales: `--mode` (`papper` | `hyperliquid_testnet` | `hyperliquid_mainnet`), `--symbol`, `--max-posiciones`, `--slot-factor`, `--order-type`, `--timeframe`, `--dashboard-port`.

### `Executor_RSI_Wilder.py` / `Executor_RSI_Standard.py` — Lanzadores rápidos

Ajustan una estrategia concreta y aceptan flags de entorno directos:

```bash
python Executor_RSI_Wilder.py
python Executor_RSI_Wilder.py --hyperliquid_mainnet
python Executor_RSI_Wilder.py --papper --rsi-period 14 --oversold 30 --overbought 70
python Executor_RSI_Wilder.py --timeframe 15m
```

Flags disponibles: `--papper`, `--hyperliquid_mainnet`, `--hyperliquid_testnet`, `--env`, `--modo`, `--max-pos`, `--slot-factor`, `--rsi-period`, `--oversold`, `--overbought`, `--reduce-long`, `--reduce-short`, `--timeframe`.

Resolución de entorno: flag directo > `--env` > constante `ENVIRONMENT` del script.

---

## 📦 Instalación

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Configurar variables de entorno
copy .env.example .env    # (Windows) y editar los valores

# 3. Verificar instalación
python -m pytest tests/ -v --tb=short
```

---

## 🔧 Configuración (.env)

Todas las variables están documentadas en `.env.example`. Las principales:

| Variable | Default | Descripción |
|---|---|---|
| `SYMBOL` | `BTCUSDT` | Par de trading |
| `TIMEFRAME` | `1h` | Temporalidad de velas (1m…1w). Sobrescribible con `--timeframe` |
| `PAPPER_SALDO_INICIAL` | `100.0` | Capital inicial (solo papper) |
| `PAPPER_COMMISSION_PCT` | `0.02` | Comisión % (solo papper) |
| `HL_ACCOUNT_ADDRESS` / `HL_SECRET_KEY` | — | Credenciales Hyperliquid mainnet |
| `HL_TESTNET_ACCOUNT_ADDRESS` / `HL_TESTNET_SECRET_KEY` | — | Credenciales Hyperliquid testnet |
| `HL_SYMBOL` | `BTC` | Símbolo en formato Hyperliquid |
| `HL_LEVERAGE` | `1` | Apalancamiento |
| `HL_DMS_MARGIN_SECONDS` | `120` | Dead Man's Switch: cancela órdenes al final de la vela (máx. 10/día) |
| `TIMEZONE` | `-3` | Offset UTC para horarios mostrados |
| `RISK_*` | ver `.env.example` | Controles de riesgo (drawdown, trades/día, cooldown, circuit breaker, stop loss) |

> En los modos online el saldo inicial se descubre automáticamente desde la API del exchange; `PAPPER_SALDO_INICIAL` se ignora.

---

## 🧠 Estrategias

Las estrategias bajo `strategies/` deben mantenerse **idénticas** al repositorio de backtesting (verificado por `tests/general/test_live_engine_backtest_parity.py`). No modificarlas localmente sin replicar el cambio en el otro repo.

| Estrategia | Clase | Registro |
|---|---|---|
| RSI Wilder | `strategies.rsi_wilder:RSIWilderStrategy` | `rsi` |
| RSI Standard | `strategies.rsi_standard:RSIStandardStrategy` | `rsi_standard` |

El executor calcula órdenes límite al inicio de cada vela usando solo información disponible en ese momento.

---

## 📈 Dashboard en vivo

Cada entorno levanta su propio servidor de dashboard (`dashboard/server.py`) con gráficos en tiempo real (lightweight-charts) en el puerto correspondiente (ver tabla de entornos). También se puede acceder directamente a `live_dashboard.html`.

---

## 💾 Persistencia

- Fuente de verdad por entorno: `live_results_{environment}.json` (en algunos lanzadores `live_results_wilder_{mode}.json`).
- Al reiniciar, el estado de wallet y RiskManager se restaura desde ese archivo.
- **Borrar el archivo resetea el estado operativo del entorno.**

---

## 🛡️ Gestión de riesgo

Configurable vía variables `RISK_*` en `.env`:

| Variable | Descripción |
|---|---|
| `RISK_MAX_DRAWDOWN_PCT` | Drawdown máximo antes de detener el trading (%) |
| `RISK_MAX_TRADES_PER_DAY` | Máximo de trades por día |
| `RISK_COOLDOWN_SECONDS` | Segundos mínimos entre trades |
| `RISK_CIRCUIT_BREAKER_N` | Pausa tras N señales rechazadas seguidas |
| `RISK_STOP_LOSS_PCT` | Stop loss por posición (% desde entrada) |

---

## 📣 Notificaciones (Telegram)

Configuración en `.env` (`TELEGRAM_*`): token de bot, chat id, nivel mínimo de eventos y topics separados por entorno. Para autodetectar los topics de un grupo con Foro:

```bash
python -m support.setup_telegram_topics
```

---

## 🗂️ Estructura del Proyecto

```
Executor - Trading bot/
├── main.py                              # Lanzador único (--mode)
├── Executor_RSI_Standard.py             # Lanzador rápido RSI Standard
├── Executor_RSI_Wilder.py               # Lanzador rápido RSI Wilder
├── .env.example                         # Plantilla de variables de entorno
├── requirements.txt                     # Dependencias Python
├── live_dashboard.html                  # Dashboard en vivo
├── actors/                              # Feeds, wallets y order books por entorno
│   ├── papper/ · hyperliquid_mainnet/ · hyperliquid_testnet/
├── engine/live_engine.py                # Motor de ejecución en vivo
├── risk/risk_manager.py                 # Controles de riesgo
├── state/state_manager.py               # Persistencia / checkpoints
├── strategies/                          # rsi_standard.py · rsi_wilder.py (paridad con Backtesting)
├── indicadores/                         # Indicadores técnicos
├── dashboard/                           # Servidor HTTP del dashboard
├── notifications/telegram_notifier.py   # Notificaciones Telegram
├── support/                             # Logger, secrets, time utils, setup Telegram topics
├── scripts/run_pytest.ps1               # Runner de tests
├── Guías de construcción/               # Documentación de construcción (Hyperliquid, etc.)
└── tests/                               # Suite pytest (general, environments, dashboards)
```

---

## 🧪 Tests

```bash
python -m pytest tests/ -v --tb=short
# o
pwsh scripts/run_pytest.ps1
```

> ⚠️ Los tests de `tests/environments/` pueden requerir credenciales reales (Hyperliquid) y están pensados como pruebas de integración manuales.

---

## ⚠️ Aviso

Operar en `hyperliquid_mainnet` implica riesgo real de pérdida de capital. Validar siempre en `papper` o `hyperliquid_testnet`.

## 📄 Licencia

Proyecto privado - Kryptos4Ever