# 🤖 RSI Strategies - Trading Bot

Monorepositorio de trading algorítmico sobre **BTC/USDT** con estrategias **RSI Mean Reversion**, dividido en dos subproyectos independientes (cada uno con su propio repositorio git):

| Subproyecto | Descripción | README |
|---|---|---|
| 📊 [Backtesting - Trading bot](./Backtesting%20-%20Trading%20bot/) | Motor de backtesting con datos históricos de SQLite, optimizador de parámetros y dashboard interactivo de resultados. | [README](./Backtesting%20-%20Trading%20bot/README.md) |
| ⚡ [Executor - Trading bot](./Executor%20-%20Trading%20bot/) | Ejecución en vivo (paper trading y real vía Hyperliquid Perps) con dashboard en tiempo real y notificaciones por Telegram. | [README](./Executor%20-%20Trading%20bot/README.md) |

---

## 🔄 Flujo de trabajo

```
┌──────────────┐     ┌────────────────────┐     ┌─────────────────────┐
│  DB/         │     │  Backtesting       │     │  Executor           │
│  Descarga de │────▶│  Validar estrategi │────▶│  Ejecución en vivo  │
│  velas OHLCV │     │  + optimización    │     │  (papper / HL)      │
└──────────────┘     └────────────────────┘     └─────────────────────┘
  Binance API          SQLite + Engine            Hyperliquid API
                       de actores                 (WebSocket + REST)
```

1. **Descargar datos** → scripts de `DB/` en el subrepo de Backtesting descargan velas de BTC/USDT desde Binance a bases SQLite (5m, 15m, 1h).
2. **Backtest** → validar la estrategia sobre el histórico y visualizar resultados en el dashboard.
3. **Optimizar** → buscar parámetros óptimos con `Optimizador_RSI_Standard.py` / `Optimizador_RSI_Wilder.py`.
4. **Ejecutar en vivo** → el Executor usa las **mismas estrategias** (archivos de `strategies/` mantenidos idénticos entre ambos repos) para operar en paper o real.

---

## 🧠 Estrategias

Ambos subrepos comparten el mismo contrato de estrategia (`BaseStrategy`) y las mismas implementaciones:

| Estrategia | Clase | Nombre corto | Descripción |
|---|---|---|---|
| RSI Wilder | `RSIWilderStrategy` | `rsi_wilder` / `rsi` | Mean Reversion con RSI de Wilder's Smoothing (LONG + SHORT) |
| RSI Standard | `RSIStandardStrategy` | `rsi_standard` | Mean Reversion con RSI estándar (Cutler's RSI) |

Parámetros por defecto (comunes): `rsi_period=14`, `oversold_threshold=30.0`, `overbought_threshold=70.0`, `reduce_long=50.0`, `reduce_short=50.0`, `max_positions=3`.

> ⚠️ **Paridad de estrategias:** los archivos bajo `strategies/` deben mantenerse idénticos entre ambos subrepos. Existe un test (`test_live_engine_backtest_parity.py`) que lo verifica.

---

## ⚙️ Requisitos generales

- **Python 3.10+**
- **pip**
- Windows recomendado (los lanzadores y `.bat` están pensados para Windows, pero el código es multiplataforma)

### Instalación rápida

```bash
# Backtesting
cd "Backtesting - Trading bot"
pip install -r requirements.txt

# Executor (en otro entorno o proyecto separado)
cd "Executor - Trading bot"
pip install -r requirements.txt
```

### Inicio rápido

```bash
# 1. Backtest con la estrategia por defecto (rsi_wilder)
cd "Backtesting - Trading bot"
python Backtest.py --strategy rsi_wilder

# 2. Ver resultados en el dashboard
.\Iniciar_Dashboard.bat

# 3. Paper trading en vivo con RSI Wilder
cd "..\Executor - Trading bot"
python Executor_RSI_Wilder.py --papper
```

---

## 🗂️ Estructura del monorepositorio

```
RSI Strategies - Trading Bot/
├── README.md                        # Este archivo
├── Backtesting - Trading bot/       # Subrepo: motor de backtesting (ver su README)
│   ├── Backtest.py                  # Lanzador único de backtests (--strategy)
│   ├── Optimizador_RSI_*.py         # Optimización de parámetros
│   ├── actors/ · engine/ · risk/ · state/ · strategies/
│   ├── DB/                          # Descarga de datos (Binance → SQLite)
│   └── tests/
└── Executor - Trading bot/          # Subrepo: ejecución en vivo (ver su README)
    ├── main.py                      # Lanzador único del executor (--mode)
    ├── Executor_RSI_*.py            # Lanzadores rápidos por estrategia
    ├── actors/ · engine/ · risk/ · state/ · strategies/
    ├── dashboard/                   # Servidor del dashboard en vivo
    ├── notifications/               # Notificador de Telegram
    └── tests/
```

---

## 🧪 Tests

Cada subrepo tiene su propia suite de pytest:

```bash
cd "Backtesting - Trading bot" && python -m pytest tests/ -v --tb=short
cd "Executor - Trading bot"    && python -m pytest tests/ -v --tb=short
```

---

## ⚠️ Aviso

Software de uso privado/educativo. Operar con trading algorítmico en entornos reales implica riesgo de pérdida de capital. Probar siempre en modo `papper` o `hyperliquid_testnet` antes de usar mainnet.

## 📄 Licencia

Proyecto privado - Kryptos4Ever