"""
state/ — Persistencia del estado del sistema
==============================================
Guarda checkpoints del estado del bot durante ejecución en memoria.

Componentes:
  - StateManager:        Clase abstracta base.
  - MemoryStateManager:  Estado en memoria (útil para backtests).
  - Checkpoint:          Snapshot del estado en un momento dado (wallet, riesgo, metadata).
"""