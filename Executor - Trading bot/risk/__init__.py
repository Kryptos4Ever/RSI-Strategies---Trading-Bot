"""
risk/ — Gestión de riesgo del sistema de trading
==================================================
Evalúa y controla el riesgo de las operaciones antes de ejecutarlas.

Componentes:
  - RiskManager: Evaluador multicapa con max drawdown, límite de trades diarios,
    cooldown entre trades, circuit breaker y stop loss individual.
  - build_risk_manager(): Factory que construye el RiskManager según el modo
    (backtest con controles mínimos, live con todos los controles desde .env).

El RiskManager es agnóstico a la estrategia y al exchange. Decide únicamente
si una señal puede ejecutarse basándose en reglas de riesgo configurables.
"""