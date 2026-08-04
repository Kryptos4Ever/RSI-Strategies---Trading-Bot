"""
actors/ — Actores del sistema de trading
=========================================
Cada actor modela una entidad independiente con una responsabilidad única,
siguiendo un patrón similar al modelo Actor:

  - price_feed:      Fuente de datos de mercado (SQLite, WebSocket Binance/Hyperliquid)
  - clock:           Reloj que gobierna el ciclo de ejecución (local o en vivo)
  - wallet:          Gestión de saldos, posiciones y registro de trades
  - order_book:      Ejecución de órdenes, comisiones y guardias de validación
  - binance_feed:    WebSocket y REST para datos de Binance
  - binance_order_book:  Órdenes reales en Binance
  - binance_wallet:  Wallet real conectada a Binance
  - hyperliquid_feed:    WebSocket y REST para datos de Hyperliquid
  - hyperliquid_order_book: Órdenes reales en Hyperliquid
  - hyperliquid_wallet:  Wallet real conectada a Hyperliquid
  - dashboard_server:    Servidor HTTP embebido para el dashboard web
  - exchange_client:     Cliente unificado para exchanges (en desarrollo)
  - live_clock:          Reloj para modo live (actualmente no implementado)
"""