"""
strategies/ — Estrategias de trading
======================================
Define el contrato abstracto BaseStrategy y las implementaciones concretas.

Componentes:
  - BaseStrategy:       Clase abstracta que define la interfaz on_candle().
  - Signal / SignalSide: Estructuras de datos para las señales de trading.
  - BollingerDualBandStrategy: Estrategia con Bandas de Bollinger Dual
    (parámetros BUY y SELL independientes, modo last_close_weighted).

Toda estrategia recibe velas (Candle) y retorna una lista de señales (List[Signal]).
"""