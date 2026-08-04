"""
actors/ - Actores del sistema de trading.

Cada actor modela una entidad independiente con una responsabilidad unica.

Clases base compartidas:
  - price_feed: PriceFeed, AsyncFeed
  - wallet: Wallet, AsyncWallet, TradeRecord, AggregatePosition
  - order_book: OrderBook, AsyncOrderBook, Order, OrderSide, OrderStatus
  - clock: LiveClock

Implementaciones por entorno:
  - papper/
  - hyperliquid_mainnet/
  - hyperliquid_testnet/
"""
