"""
logger.py — Logger transversal del sistema
═══════════════════════════════════════════
Usa loguru si está disponible (soporta kwargs como `db=path`),
fallback a logging estándar con wrapper que ignora kwargs extra.

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
        """Inicializa el logger con el nombre dado y crea un logger interno de logging."""
        import logging
        self._logger = logging.getLogger(name)
        self._configured = False

    def _ensure_configured(self) -> None:
        """Configura el handler de salida estándar si aún no se ha configurado."""
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
        """Registra un mensaje con el nivel dado, ignorando kwargs extra no soportados por logging estándar."""
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
        """Registra un mensaje con nivel INFO."""
        self._log(20, msg, *args, **kwargs)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Registra un mensaje con nivel DEBUG."""
        self._log(10, msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Registra un mensaje con nivel WARNING."""
        self._log(30, msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Registra un mensaje con nivel ERROR."""
        self._log(40, msg, *args, **kwargs)

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Registra un mensaje con nivel CRITICAL."""
        self._log(50, msg, *args, **kwargs)


# ── Inicializar loguru si está disponible ────────────────────────────────
_loguru_logger = None  # variable global, siempre existe
_HAS_LOGURU = False
try:
    from loguru import logger as _loguru_impl  # noqa: F401
    _HAS_LOGURU = True
    _loguru_impl.remove()
    _loguru_impl.add(sys.stderr, colorize=True)
    _loguru_logger = _loguru_impl
except ImportError:
    pass


# ── Función única get_logger (fuera del try/except, sin shadowing) ───────
def get_logger(name: str = __name__):
    """Retorna un logger (loguru si está disponible, fallback a logging estándar)."""
    if _HAS_LOGURU:
        return _loguru_logger
    return _FallbackLogger(name)


# ── Shared state for rolling carriage return status lines ────────────────
_status_line_active = False


def set_status_line_active(active: bool) -> None:
    """Activa o desactiva el modo de línea de estado para el wrapper StatusLineAwareStream."""
    global _status_line_active
    _status_line_active = active


class StatusLineAwareStream:
    """
    Wrapper para sys.stdout / sys.stderr que, solo cuando hay una línea de
    estado activa, escribe automáticamente un '\\n' antes de los logs.
    Cuando NO hay status line activa (99% del tiempo, incluidos tests),
    los print() pasan directamente sin intervención ni flush innecesario.
    """
    def __init__(self, stream) -> None:
        self._stream = stream

    def write(self, data: str) -> int:
        global _status_line_active
        if _status_line_active:
            if data and not data.startswith("\r") and data != "\n":
                # Imprime un salto de linea para empujar el log
                self._stream.write("\n")
                self._flush_unwrap()
                _status_line_active = False
            result = self._stream.write(data)
            self._flush_unwrap()
            return result
        # Sin status line activa: paso directo, sin intervención
        return self._stream.write(data)

    def _flush_unwrap(self) -> None:
        """Flush del stream real, no de nosotros mismos."""
        try:
            self._stream.flush()
        except (IOError, ValueError):
            pass

    def flush(self) -> None:
        try:
            self._stream.flush()
        except (IOError, ValueError):
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


sys.stdout = StatusLineAwareStream(sys.stdout)
sys.stderr = StatusLineAwareStream(sys.stderr)
