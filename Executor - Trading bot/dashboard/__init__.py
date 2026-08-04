"""
dashboard/ — Servidor web para monitoreo en vivo
=================================================
Contiene el servidor HTTP que expone los datos del engine
en tiempo real a través de un dashboard web.

Cada instancia del engine inicia su propio servidor en un puerto
dedicado (preasignado por modo en .env o por argumento CLI).
"""