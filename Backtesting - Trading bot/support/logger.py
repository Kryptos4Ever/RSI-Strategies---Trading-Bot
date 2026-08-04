"""
logger.py — Logger transversal del sistema
═══════════════════════════════════════════
Usa loguru si está disponible (soporta kwargs como `db=path`),
fallback a logging estándar con wrapper que ignora kwargs extra.

CORRECCIÓN: Eliminada la redirección de sys.stdout/stderr que rompía
            los print() y la línea de estado intra-vela del bot live.
"""
from __future__ import annotations

import sys
from typing import Any


class _FallbackLogger:
    """
    Wrapper sobre logging.Logger que ignora keyword arguments extra.
    Esto permite que el código use `log.info("msg", db=path, table=table)`
    tanto con loguru (que lo soporta nativamente) como con logging estándar.
    """

    def __init__(self, name: str) -> None:
        import logging
        self._logger = logging.getLogger(name)
        self._configured = False

    def _ensure_configured(self) -> None:
        if self._configured:
            return
        import logging
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        ))
        self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)
        self._configured = True

    def _log(self, level: int, msg: str, *args: Any, **kwargs: Any) -> None:
        self._ensure_configured()
        # logging estándar no acepta kwargs extra como 'db', 'table', 'error', etc.
        extra = kwargs.pop("extra", None) if "extra" in kwargs else None
        # Incluir kwargs informativos como 'error', 'side', 'price', etc. en el mensaje
        if kwargs:
            extra_parts = []
            for k, v in kwargs.items():
                extra_parts.append(f"{k}={v!r}")
            msg = f"{msg} ({', '.join(extra_parts)})"
        if extra is not None:
            self._logger._log(level, msg, args, extra=extra)
        else:
            self._logger._log(level, msg, args)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(20, msg, *args, **kwargs)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(10, msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(30, msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(40, msg, *args, **kwargs)

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(50, msg, *args, **kwargs)


# ── Inicializar loguru si está disponible ────────────────────────────────
_loguru_logger = None  # variable global, siempre existe
_HAS_LOGURU = False
try:
    from loguru import logger as _loguru_impl  # noqa: F401
    _HAS_LOGURU = True
    _loguru_impl.remove()
    _loguru_impl.add(sys.stderr, level="INFO", colorize=True)
    _loguru_logger = _loguru_impl
except ImportError:
    pass


# ── Función única get_logger (fuera del try/except, sin shadowing) ───────
def get_logger(name: str = __name__):
    """Retorna un logger (loguru si está disponible, fallback a logging estándar)."""
    if _HAS_LOGURU:
        return _loguru_logger.bind(module=name)
    return _FallbackLogger(name)



