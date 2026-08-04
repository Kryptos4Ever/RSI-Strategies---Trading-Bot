"""
tests/environments/real_operation/conftest_real_operation.py — Fixtures compartidos para tests reales
=====================================================================================================
Proporciona:
  - Función de confirmación explícita del usuario
  - Helpers de logging formateado
  - Constantes compartidas (MIN_ORDER_USD, etc.)
"""
from __future__ import annotations

import os
import sys
import time
import datetime
import logging

import pytest

# Asegurar que el raíz del proyecto esté en el path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTES
# ══════════════════════════════════════════════════════════════════════════════

MIN_ORDER_USD = 10.0  # Valor mínimo de operación para todos los exchanges
MARKET_ORDER_USD = 13.0  # Valor para la compra market inicial (mayor que MIN para cubrir desvíos)
WAIT_AFTER_BUY = 2.0   # Segundos a esperar tras comprar para confirmar apertura
WAIT_AFTER_SELL = 2.0  # Segundos a esperar tras vender para confirmar cierre


# ══════════════════════════════════════════════════════════════════════════════
# CONFIRMACIÓN EXPLÍCITA DEL USUARIO
# ══════════════════════════════════════════════════════════════════════════════

def require_confirmation(exchange_name: str, test_name: str) -> None:
    """
    Solicita confirmación explícita al usuario antes de ejecutar un test real.
    Si el usuario no responde exactamente 'si', salta el test con pytest.skip().
    Si el usuario responde 'si', el test continúa.
    """
    print(f"\n{'='*70}")
    print(f"  ⚠️  TEST DE OPERACIÓN REAL — {exchange_name}")
    print(f"  Test: {test_name}")
    print(f"  Este test realizará OPERACIONES REALES con fondos en {exchange_name}.")
    print(f"  Valor mínimo por orden: ~${MIN_ORDER_USD}")
    print(f"{'='*70}")
    respuesta = input("  ¿Confirmás que querés ejecutar este test? (escribí 'si' para continuar): ")
    if respuesta.strip().lower() != "si":
        pytest.skip(f"Test omitido por el usuario (respondió: '{respuesta}')")


# ══════════════════════════════════════════════════════════════════════════════
# LOGGING DE EVENTOS
# ══════════════════════════════════════════════════════════════════════════════

_event_log: list[dict] = []


def log_event(event: str, **kwargs) -> None:
    """Registra un evento con timestamp ISO y lo imprime."""
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    entry = {"ts": ts, "event": event, **kwargs}
    _event_log.append(entry)
    pairs = "  ".join(f"{k}={v}" for k, v in kwargs.items())
    print(f"\n[{ts}] [{event}] {pairs}", flush=True)


def print_separator(title: str = "") -> None:
    line = "=" * 70
    if title:
        print(f"\n{line}")
        print(f"  {title}")
        print(line, flush=True)
    else:
        print(line, flush=True)


def print_summary(initial_balance: float, final_balance: float, events_count: int) -> None:
    """Imprime un resumen formateado del test."""
    delta = final_balance - initial_balance
    print(f"\n{'='*70}")
    print(f"  RESUMEN DEL TEST")
    print(f"  Balance inicial : ${initial_balance:.4f}")
    print(f"  Balance final   : ${final_balance:.4f}")
    print(f"  Delta PnL       : ${delta:+.4f}")
    print(f"  Eventos log     : {events_count}")
    print(f"{'='*70}\n")


@pytest.fixture(autouse=True)
def reset_event_log():
    """Resetea el log de eventos antes de cada test."""
    _event_log.clear()
    yield


def get_event_log() -> list[dict]:
    """Retorna el log de eventos acumulado."""
    return list(_event_log)

if __name__ == '__main__':
    pytest.main([__file__, '-s', '-v'])
