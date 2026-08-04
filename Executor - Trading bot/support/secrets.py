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
