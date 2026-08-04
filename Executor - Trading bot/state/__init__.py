"""
state/ — Persistencia del estado del sistema
==============================================
Guarda y restaura el estado del bot entre ejecuciones, permitiendo
que sobreviva reinicios sin perder información.

Componentes:
  - StateManager:     Clase abstracta base.
  - MemoryStateManager:  Estado en memoria (útil para backtests).
  - JSONStateManager:    Estado persistido en archivos JSONL.
  - build_state_manager(): Factory que construye el gestor adecuado.
  - Checkpoint:      Snapshot del estado en un momento dado (wallet, riesgo, metadata).
"""