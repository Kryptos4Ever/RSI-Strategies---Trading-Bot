"""
Acceso a credenciales y variables de configuracion.

Lee primero desde variables de entorno y luego desde .env.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


class CredentialNotFound(Exception):
    """No se encontro la credencial solicitada."""


_secrets_cache: dict[str, str] = {}
_env_loaded = False

# ── Mapeo de temporalidades a segundos ─────────────────────────────────────
# Usado por el LiveEngine para calcular warm-up y patrones de velas.
_TIMEFRAME_SECONDS_DEFAULT = {
    "1m": 60, "3m": 180, "5m": 300,
    "15m": 900, "30m": 1800, "1h": 3600,
    "2h": 7200, "4h": 14400, "1d": 86400,
    "1w": 604800,
}


def _load_env() -> None:
    """Carga variables del archivo .env una sola vez."""
    global _env_loaded
    if _env_loaded:
        return

    for p in [Path.cwd(), Path(__file__).parent.parent]:
        env_file = p / ".env"
        if env_file.exists():
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    _secrets_cache[key.strip()] = val.strip().strip("\"'")
            break
    _env_loaded = True


def secrets(key: str, default: Optional[str] = None) -> str:
    """Obtiene un valor de configuracion por nombre."""
    val = os.environ.get(key)
    if val is not None:
        return val
    _load_env()
    val = _secrets_cache.get(key)
    if val is not None:
        return val
    if default is not None:
        return default
    raise CredentialNotFound(f"Credencial '{key}' no encontrada en entorno ni .env")


def timeframe_seconds(timeframe: str) -> int:
    """
    Convierte un timeframe (ej: '1h', '15m', '4h') a segundos.

    Valores soportados: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 1d, 1w.
    Si el timeframe no es reconocido, retorna 3600 (1h) como fallback
    seguro y no lanza excepcion (evita romper el bot en produccion).
    """
    tf = (timeframe or "").strip().lower()
    return _TIMEFRAME_SECONDS_DEFAULT.get(tf, 3600)