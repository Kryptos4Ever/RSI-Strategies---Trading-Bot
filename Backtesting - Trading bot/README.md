# 🤖 Backtesting - Trading Bot

Sistema de backtesting para estrategias de trading algorítmico sobre BTC/USDT, con dashboard interactivo para visualización de resultados.

## 📋 Tabla de Contenidos

- [Arquitectura](#arquitectura)
- [Requisitos](#requisitos)
- [Instalación](#instalación)
- [Configuración](#configuración)
- [Uso](#uso)
  - [Ejecutar un Backtest](#ejecutar-un-backtest)
  - [Ver Resultados (Dashboard)](#ver-resultados-dashboard)
- [Estrategias: RSI Mean Reversion](#estrategias-rsi-mean-reversion)
- [Estructura del Proyecto](#estructura-del-proyecto)
- [Tests](#tests)
- [Parámetros de Configuración](#parámetros-de-configuración)
- [Dashboard Interactivo](#dashboard-interactivo)
- [Mantenimiento](#mantenimiento)

---

## 🏗️ Arquitectura

El sistema sigue una **arquitectura de actores** con responsabilidades bien definidas:

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  PriceFeed  │────▶│    Clock     │────▶│   Strategy   │
│ (datos BTC) │     │ (iteración)  │     │  (señales)   │
└─────────────┘     └──────────────┘     └──────┬───────┘
                                                │
┌─────────────┐     ┌──────────────┐     ┌──────▼───────┐
│   Wallet    │◀────│  OrderBook   │◀────│  Backtest    │
│ (capital)   │     │ (ejecución)  │     │   Engine     │
└─────────────┘     └──────────────┘     └──────────────┘
       │                                         │
       ▼                                         ▼
┌─────────────┐                          ┌──────────────┐
│  State/     │                          │  RiskManager │
│  Checkpoint │                          │  (drawdown)  │
└─────────────┘                          └──────────────┘
```

### Componentes Principales

| Componente | Archivo | Responsabilidad |
|---|---|---|
| **PriceFeed** | `actors/price_feed.py` | Proveer velas OHLCV desde SQLite o CSV |
| **Clock** | `actors/clock.py` | Iterar sobre las velas en el rango temporal |
| **Strategy** | `strategies/` | Generar señales de trading (compra/venta) |
| **OrderBook** | `actors/order_book.py` | Ejecutar órdenes simuladas (Post-Only o GTC) |
| **Wallet** | `actors/wallet.py` | Gestionar capital, posiciones y PnL |
| **RiskManager** | `risk/risk_manager.py` | Controlar límites de riesgo (max drawdown) |
| **StateManager** | `state/state_manager.py` | Guardar checkpoints del estado del sistema |
| **BacktestEngine** | `engine/backtest_engine.py` | Orquestar el ciclo completo del backtest |

---

## ⚙️ Requisitos

- **Python 3.10+**
- **pip** (gestor de paquetes)
- **Navegador web moderno** (para el dashboard)
- **Datos históricos** de BTC/USDT en SQLite (ver sección de descarga)

### Dependencias

```
numpy>=1.21.0
pytest>=7.0.0
```

Ver `requirements.txt` para la lista completa.

---

## 📦 Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/Kryptos4Ever/RSI-Strategies---Trading-Bot.git
cd "Backtesting - Trading bot"

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Verificar instalación
python -m pytest tests/ -v --tb=short
```

---

## 🔧 Configuración

Toda la configuración global se encuentra en `config_local.py`:

```python
# Temporalidades
PRIMARY_TIMEFRAME   = "1h"    # "1h", "15m" o "5m"
SECONDARY_TIMEFRAME = "5m"    # "15m", "5m", "1m" o "" para desactivar

# Rango temporal del backtest
FECHA_INICIO = '2021-11-10'
FECHA_FIN    = '2026-08-10'

# Parámetros generales
SYMBOL             = "BTCUSDT"
SALDO_USD_INICIAL = 1000.0        # Capital inicial en USD
COMMISSION_PCT     = 0.02         # Comisión en % (0.02 = 0.02%)

# Archivo de salida
RESULTS_JSON = "backtest_results.json"
```

### Referencias de Fechas Útiles

| Período | Fecha | Descripción |
|---|---|---|
| Bottom Bear 2018 | 2018-12-10 | Fin del mercado bajista 2018 |
| Pre COVID | 2019-06-27 | Mercado plano pre-pandemia |
| Inicio Bull 2020 | 2020-03-17 | Inicio del bull run post-COVID |
| TOP1 2021 | 2021-04-14 | Primer pico del ciclo 2021 |
| TOP2 2021 | 2021-11-10 | ATH del ciclo 2021 |
| Bottom Bear 2022 | 2022-11-22 | Fondo del mercado bajista 2022 |
| Inicio Bull 2023 | 2023-01-01 | Inicio de la recuperación |
| Inicio Bull 2024 | 2024-01-01 | Rally 2024 (ETF approvals) |
| TOP 2025 | 2025-10-06 | Último dato disponible |

### Variables de Entorno

| Variable | Descripción |
|---|---|
| `TRADING_DB_PATH` | Ruta personalizada a la base de datos SQLite |

---

## 🚀 Uso

### 1. Descargar Datos Históricos

Primero, descarga los datos de BTC/USDT para la temporalidad deseada:

```bash
# Datos horarios (1H)
python "DB/BTCUSDT_1H_Binance_data_downloader_optimized.py"

# Datos de 15 minutos
python "DB/BTCUSDT_15M_Binance_data_downloader_optimized.py"

# Datos de 5 minutos
python "DB/BTCUSDT_5M_Binance_data_downloader_optimized.py"
```

Esto creará archivos SQLite en la carpeta `DB/` (ej: `DB/btc_1h.db`).

### 2. Ejecutar un Backtest

**Opción A — Lanzador único (recomendado),** con estrategia y parámetros por CLI:

```bash
# Estrategia por defecto (rsi_wilder)
python Backtest.py

# Elegir estrategia y personalizar parámetros
python Backtest.py --strategy rsi_standard --rsi-period 7
python Backtest.py --strategy rsi_wilder \
                   --start 2023-01-01 --end 2025-01-01 \
                   --capital 5000 --commission 0.05 \
                   --max-posiciones 3 --slot-factor 1.0 \
                   --modo-operacion limite_gtc
```

Estrategias disponibles: `rsi_wilder` (default) y `rsi_standard`. Los parámetros generales (temporalidades, fechas, capital, comisión) se toman de `config_local.py` y pueden sobrescribirse por CLI (`--start`, `--end`, `--capital`, `--commission`, `--db-path`, etc.).

**Opción B — Lanzadores específicos por estrategia:**

```bash
python Backtest_RSI_Standard.py
python Backtest_RSI_Wilder.py
```

Esto generará:
- **Salida en consola:** Configuración, progreso y resumen del backtest
- **Archivo JSON:** `backtest_results.json` con todos los resultados

### 3. Optimizar Parámetros (opcional)

```bash
python Optimizador_RSI_Standard.py
python Optimizador_RSI_Wilder.py
```

Búsqueda de parámetros óptimos con aceleración JIT (numba) y checkpoints de progreso.

### 4. Ver Resultados (Dashboard)

**Opción A — Automática (Windows):**
```bash
Iniciar_Dashboard.bat
```

**Opción B — Manual:**
```bash
python serve_dashboard.py
# Abre http://localhost:8000 en tu navegador
```

**Opción C — Directo:**
Abre `backtest_dashboard.html` directamente en el navegador. Si el archivo `backtest_results.json` está en el mismo directorio, se cargará automáticamente.

---

## 📈 Estrategias: RSI Mean Reversion

El sistema incluye dos variantes de la misma lógica de **Mean Reversion** basada en RSI: `rsi_wilder` (Wilder's Smoothing, por defecto) y `rsi_standard` (Cutler's RSI sobre cierres).

### Lógica

La estrategia **RSI Mean Reversion** opera bajo el principio de reversión a la media:

- **RSI < 50 (zona LONG):** El mercado está "frío" → buscar oportunidades de compra
- **RSI > 50 (zona SHORT):** El mercado está "caliente" → buscar oportunidades de venta
- **RSI = 50 (zona neutral):** No operar

### Reglas de Operación

```
┌─────────────────────────────────────────────────────────────────┐
│                    RSI < 50 (Zona LONG)                        │
├─────────────────────────────────────────────────────────────────┤
│ Sin posición  → OPEN_LONG  si RSI > oversold_threshold (30)    │
│ Posición LONG → ADD_LONG   si RSI > oversold_threshold (30)    │
│               → REDUCE_LONG  siempre (al precio RSI=50)        │
│ Posición SHORT → CLOSE_SHORT  siempre (al precio oversold)     │
├─────────────────────────────────────────────────────────────────┤
│                    RSI > 50 (Zona SHORT)                       │
├─────────────────────────────────────────────────────────────────┤
│ Sin posición  → OPEN_SHORT  si RSI < overbought_threshold (70) │
│ Posición SHORT → ADD_SHORT   si RSI < overbought_threshold (70)│
│                → REDUCE_SHORT  siempre (al precio RSI=50)      │
│ Posición LONG → CLOSE_LONG  siempre (al precio overbought)     │
└─────────────────────────────────────────────────────────────────┘
```

### Precios de Entrada/Salida

Los precios no son arbitrarios: se calculan usando la **fórmula inversa de Wilder** implementada en `RSIEngine.price_for_rsi()`. Dado el estado actual del RSI, esta función calcula qué precio provocaría que el RSI alcanzara el valor objetivo en la siguiente vela.

| Señal | Precio Objetivo |
|---|---|
| OPEN_LONG / ADD_LONG | `price_for_rsi(oversold_threshold)` → intenta comprar en el precio correspondiente a RSI=30 |
| CLOSE_SHORT | `price_for_rsi(oversold_threshold)` → cubre el short al precio RSI=30 |
| OPEN_SHORT / ADD_SHORT | `price_for_rsi(overbought_threshold)` → intenta vender en el precio correspondiente a RSI=70 |
| CLOSE_LONG | `price_for_rsi(overbought_threshold)` → vende el long al precio RSI=70 |
| REDUCE_LONG / REDUCE_SHORT | `price_for_rsi(reduce_long/reduce_short)` → reduce posición al precio RSI=50 |

### Modos de Ejecución

| Modo | Descripción |
|---|---|
| `limit_post_only` | Solo órdenes maker. La orden solo se ejecuta si actúa como liquidez (no consume del libro). |
| `limite_gtc` | Permite ejecución maker o taker. Si hay gap a favor, ejecuta al precio de apertura de la vela (taker). |

### Control de Duplicados

La estrategia mantiene registros de timestamps de velas donde ya emitió señales (`_fired_*` sets). Esto evita múltiples señales del mismo tipo en una misma vela, pero permite emitir señales en velas consecutivas si la condición persiste.

---

## 📁 Estructura del Proyecto

```
Backtesting - Trading bot/
│
├── Backtest.py                          # Lanzador único (CLI, --strategy rsi_wilder|rsi_standard)
├── Backtest_RSI_Standard.py             # Lanzador específico RSI Standard (Cutler)
├── Backtest_RSI_Wilder.py               # Lanzador específico RSI Wilder
├── Optimizador_RSI_Standard.py          # Optimización de parámetros RSI Standard
├── Optimizador_RSI_Wilder.py            # Optimización de parámetros RSI Wilder
├── config_local.py                      # Configuración global
├── requirements.txt                     # Dependencias Python
├── pyproject.toml                       # Configuración del proyecto
├── pytest.ini                           # Configuración de tests
│
├── actors/                              # Actores del sistema
│   ├── clock.py                         # Reloj / iterador de velas
│   ├── order_book.py                    # Libro de órdenes simulado
│   ├── price_feed.py                    # Fuente de datos (SQLite/CSV)
│   └── wallet.py                        # Billetera y gestión de capital
│
├── engine/
│   └── backtest_engine.py              # Motor principal del backtest
│
├── strategies/
│   ├── base_strategy.py                 # Clase base abstracta
│   ├── rsi_standard.py                  # Estrategia RSI Standard (Cutler's RSI)
│   └── rsi_wilder.py                    # Estrategia RSI Wilder (Wilder's Smoothing)
│
├── indicadores/
│   ├── __init__.py
│   ├── rsi.py                           # Cálculo RSI (Wilder Smoothing)
│   └── rsi_standard.py                  # Cálculo RSI estándar (Cutler)
│
├── risk/
│   ├── __init__.py
│   └── risk_manager.py                 # Gestión de riesgo (max drawdown)
│
├── state/
│   ├── __init__.py
│   └── state_manager.py                # Checkpoints en memoria
│
├── support/
│   ├── logger.py                       # Logging estructurado
│   ├── time_utils.py                   # Utilidades de tiempo
│   └── types.py                        # Tipos compartidos (Candle, Signal, etc.)
│
├── DB/                                  # Scripts de descarga de datos
│   ├── BTCUSDT_1H_Binance_data_downloader_optimized.py
│   ├── BTCUSDT_5M_Binance_data_downloader_optimized.py
│   ├── BTCUSDT_15M_Binance_data_downloader_optimized.py
│   └── export_db_to_json.py
│
├── tests/
│   ├── conftest.py                     # Fixtures compartidos
│   ├── strategies/
│   │   ├── test_rsi_standard.py          # Tests RSI Standard
│   │   ├── test_rsi_wilder.py            # Tests RSI Wilder
│   │   └── test_rsi_wilder_comprehensive.py  # Tests exhaustivos
│   ├── actors/
│   ├── engine/
│   ├── integration/
│   └── ...
│
├── Trading View/                        # Estrategias para TradingView
│
├── backtest_dashboard.html             # Dashboard interactivo
├── backtest_results.json               # Resultados del último backtest
├── serve_dashboard.py                  # Servidor local para el dashboard
├── Iniciar_Dashboard.bat               # Atajo para Windows
└── README.md                           # Este archivo
```

---

## 🧪 Tests

El sistema incluye **101 tests** que cubren:

### Categorías de Tests

| Categoría | Archivo | Tests | Descripción |
|---|---|---|---|
| Conversiones RSI↔RS | `test_rsi_wilder_comprehensive.py` | 7 | Roundtrip, valores clave, extremos |
| `price_for_rsi()` matemático | `test_rsi_wilder_comprehensive.py` | 11 | Verificación de fórmula inversa |
| `price_for_rsi()` casos extremos | `test_rsi_wilder_comprehensive.py` | 13 | G=0, L=0, period=2, period=20, precios extremos |
| Zonas y fronteras | `test_rsi_wilder_comprehensive.py` | 9 | RSI=30, 50, 70 exactos |
| Precios en señales | `test_rsi_wilder_comprehensive.py` | 8 | Precios correctos por tipo de señal |
| Combinaciones de señales | `test_rsi_wilder_comprehensive.py` | 10 | Según estado de wallet |
| Control de duplicados | `test_rsi_wilder_comprehensive.py` | 3 | Sets `_fired_*` |
| Ciclos completos | `test_rsi_wilder_comprehensive.py` | 6 | LONG/SHORT completos |
| Escenarios reales | `test_rsi_wilder_comprehensive.py` | 4 | Volatilidad extrema, mercado lateral |
| Tests básicos | `test_rsi_wilder.py` | 28 | RSIEngine + estrategia básica |

### Ejecutar Tests

```bash
# Todos los tests
python -m pytest

# Tests específicos de RSI
python -m pytest tests/strategies/ -v

# Tests con salida detallada
python -m pytest tests/strategies/ -v --tb=long

# Tests de todo el sistema
python -m pytest tests/ -v
```

---

## 📊 Dashboard Interactivo

El dashboard (`backtest_dashboard.html`) es una aplicación web autónoma que visualiza los resultados del backtest.

### Paneles

1. **Precio BTC + Señales:** Gráfico de velas OHLCV con señales de compra/venta superpuestas
2. **Portfolio vs Buy & Hold:** Evolución del capital de la estrategia vs hold pasivo
3. **Balance USDT:** Evolución del saldo disponible en USD
4. **Posición Neta BTC:** BTC en posiciones (positivo = LONG, negativo = SHORT)
5. **Posiciones Abiertas:** Número de posiciones abiertas simultáneas
6. **Drawdown:** Caída máxima de la estrategia vs buy & hold

### Métricas Clave

| Métrica | Descripción |
|---|---|
| **PnL Estrategia** | Rendimiento total del backtest |
| **Buy & Hold PnL** | Rendimiento de mantener BTC |
| **Alpha vs B&H** | Diferencia entre estrategia y hold pasivo |
| **Max Drawdown** | Caída máxima desde el pico |
| **Sharpe Ratio** | Retorno ajustado por riesgo (anualizado) |
| **Calmar Ratio** | Retorno anual / Max Drawdown |
| **Trades Ejecutados** | Compras, ventas y órdenes ignoradas |

### Funcionalidades

- **Cambio de tema:** Oscuro/Claro con un clic
- **Regla de medición:** Mide distancias entre precios en el gráfico principal
- **Sincronización de paneles:** Todos los gráficos comparten el mismo eje temporal
- **Filtros de tabla:** Busca y filtra transacciones por tipo o estado
- **Carga manual:** Arrastra archivos JSON si la carga automática falla

---

## ⚙️ Parámetros de Configuración Detallados

### Backtest.py / Estrategias RSI

Parámetros por defecto de las estrategias (`get_default_config`), sobrescribibles por CLI:

| Parámetro | Default | Descripción |
|---|---|---|
| `--rsi-period` | 14 | Período del cálculo RSI |
| `--oversold-threshold` | 30.0 | Límite de sobreventa (abrir LONG) |
| `--overbought-threshold` | 70.0 | Límite de sobrecompra (abrir SHORT) |
| `--reduce-long` / `--reduce-short` | 50.0 | Precio de reducción de posiciones (RSI) |
| `--max-posiciones` | 3 | Máximo de posiciones simultáneas |
| `--slot-factor` | 1.0 | Factor de pirámide (1.0 = igual) |
| `--modo-operacion` | `limit_post_only` | `limit_post_only` o `limite_gtc` |

### config_local.py

| Parámetro | Default | Descripción |
|---|---|---|
| `PRIMARY_TIMEFRAME` | `"1h"` | Temporalidad principal |
| `SECONDARY_TIMEFRAME` | `"5m"` | Temporalidad secundaria |
| `FECHA_INICIO` | `"2021-11-10"` | Fecha de inicio del backtest |
| `FECHA_FIN` | `"2022-11-22"` | Fecha de fin del backtest |
| `SYMBOL` | `"BTCUSDT"` | Par de trading |
| `SALDO_USD_INICIAL` | `1000.0` | Capital inicial en USD |
| `COMMISSION_PCT` | `0.02` | Comisión en porcentaje |
| `RESULTS_JSON` | `"backtest_results.json"` | Archivo de salida |

---

## 🔍 Mantenimiento

### Orden de Ejecución Recomendado

1. Descargar datos históricos → scripts en `DB/`
2. Ajustar parámetros → `config_local.py` y el lanzador
3. Ejecutar backtest → `python Backtest.py --strategy rsi_wilder` (u optimizar antes con `Optimizador_RSI_*.py`)
4. Ver resultados → abrir `backtest_results.json` o dashboard
5. Analizar, ajustar parámetros y repetir

### Notas Importantes

- **Slippage:** El sistema actualmente simula slippage = 0. Los resultados pueden ser ligeramente optimistas respecto a la realidad.
- **Comisiones:** Verificar que `COMMISSION_PCT` refleje las comisiones reales del exchange (Binance spot: ~0.1%, futures maker: ~0.02%).
- **Datos:** Asegurarse de que la base de datos cubra todo el rango temporal del backtest.
- **Reproducibilidad:** Todos los tests usan semillas fijas para generación de números aleatorios, garantizando resultados deterministas.

---

## 📄 Licencia

Proyecto privado - Kryptos4Ever

---

## 🤝 Contribuciones

Las contribuciones son bienvenidas. Por favor:

1. Hacer fork del repositorio
2. Crear una rama para tu feature (`git checkout -b feature/nueva-funcionalidad`)
3. Ejecutar los tests (`python -m pytest`)
4. Enviar un Pull Request