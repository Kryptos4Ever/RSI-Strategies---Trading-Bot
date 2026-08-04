"""
engine/ — Motor de ejecución
==============================
Contiene el LiveEngine que orquesta el loop principal asíncrono.

El engine construye los actores según el modo, inicializa la estrategia,
ejecuta el loop de velas y maneja el shutdown graceful.
"""