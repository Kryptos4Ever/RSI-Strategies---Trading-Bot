"""
Entorno Papper (paper trading).

Usa PapperWSFeed para datos en tiempo real, SimulatedOrderBook para ejecucion
simulada sin capital real y JSONWallet para persistencia local.
"""
from actors.papper.papper_feed        import *
from actors.papper.papper_order_book  import *
from actors.papper.papper_wallet      import *
