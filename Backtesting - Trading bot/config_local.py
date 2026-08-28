"""
config_local.py — Configuración del Backtest
═════════════════════════════════════════════
Este archivo contiene la configuración compartida del sistema de backtesting.
Puede editarse directamente o sobreescribir variables vía variables de entorno.

═══ VARIABLES DE ENTORNO ═══
  TRADING_DB_PATH   → Ruta a la base de datos primaria (se resuelve en price_feed.py)

═══ USO DESDE CLI ═══
  Todos los parámetros de Backtest.py tienen flags -- que SOBREESCRIBEN
  los valores de config_local.py. Por ejemplo:
    python Backtest.py --strategy rsi_wilder \\
                       --start 2023-01-01 --end 2025-01-01 \\
                       --capital 5000 --commission 0.05

═══ TEMPORALIDADES ═══
  PRIMARY_TIMEFRAME:   Temporalidad principal (velas que ve la estrategia).
                       Puede ser "1h", "15m", "5m".
  SECONDARY_TIMEFRAME: Temporalidad secundaria para ordenar ejecución
                       cuando hay múltiples señales en una misma vela primaria.
                       Puede ser "15m", "5m", "1m".
                       Debe ser ESTRICTAMENTE MENOR que PRIMARY_TIMEFRAME.
                       Si se deja vacío o igual, no se usa resolución secundaria.

"""
# ══════════════════════════════════════════════════════════════════════════════
# TEMPORALIDADES
# ══════════════════════════════════════════════════════════════════════════════
PRIMARY_TIMEFRAME   = "15m"   # "1h", "15m" o "5m"
SECONDARY_TIMEFRAME = "5m"   # "15m", "5m", "1m" o "" para desactivar


# ══════════════════════════════════════════════════════════════════════════════
# ARCHIVOS DE SALIDA
# ══════════════════════════════════════════════════════════════════════════════
RESULTS_JSON = "backtest_results.json"
# Ruta donde se guarda el resumen del backtest en formato JSON.
# Lo usa JSONWallet.flush() para escribir el historial de trades + summary.
# El dashboard.html consume este archivo.

# ══════════════════════════════════════════════════════════════════════════════
# RANGO TEMPORAL
# ══════════════════════════════════════════════════════════════════════════════
FECHA_INICIO = '2021-11-10'  # Formato YYYY-MM-DD
FECHA_FIN    = '2026-08-10'  # Formato YYYY-MM-DD

# ── Referencias rápidas de fechas útiles para backtesting ────────────────────
#   Bottom Bear 2018  : '2018-12-10'   → Fin del mercado bajista 2018
#   Pre COVID         : '2019-06-27'   → Mercado plano pre-pandemia
#   Inicio Bull 2020  : '2020-03-17'   → Inicio del bull run post-COVID
#   TOP1 2021         : '2021-04-14'   → Primer pico del ciclo 2021
#   TOP2 2021         : '2021-11-10'   → Segundo pico (ATH) del ciclo 2021
#   Bottom Bear 2022  : '2022-11-22'   → Fondo del mercado bajista 2022
#   Inicio Bull 2023  : '2023-01-01'   → Inicio de la recuperación 2023
#   Inicio Bull 2024  : '2024-01-01'   → Rally 2024 (ETF approvals)
#   TOP 2025          : '2025-10-06'   → Último dato disponible

# ══════════════════════════════════════════════════════════════════════════════
# PARÁMETROS GENERALES DEL BACKTEST
# ══════════════════════════════════════════════════════════════════════════════
SYMBOL             = "BTCUSDT"           # Par de trading
SALDO_USD_INICIAL = 1000.0              # Capital inicial en USD
COMMISSION_PCT     = 0.02                # Comisión en % (0.02 = 0.02%)