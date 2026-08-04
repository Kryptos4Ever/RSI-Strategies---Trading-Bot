"""
state_manager.py — Gestor de estado persistente
══════════════════════════════════════════════════
Responsabilidad única: guardar y restaurar checkpoints del estado del sistema.

G4 — JSONStateManager: persiste checkpoints en disco (sobrevive reinicios).
REFACTOR ASYNC: escritura a disco via aiofiles para no bloquear el event loop.
risk_state añadido al Checkpoint para persistencia del Circuit Breaker.
"""

from __future__ import annotations

import asyncio
import json
import os
import aiofiles
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List

from state.results_store import ResultsStore


@dataclass
class Checkpoint:
    """
    Instantánea del estado del sistema en un momento dado.

    Incluye risk_state para persistencia del RiskManager entre reinicios.
    Garantiza que el Circuit Breaker no se pierda si el proceso cae.
    """
    ts:              int
    close_price:     float
    usd_balance:     float
    btc_balance:     float
    btc_en_pos:      float
    positions_count: int
    portfolio_value: float
    metadata:        dict | None = None
    risk_state:      dict | None = None  # Nuevo: estado del RiskManager

    @classmethod
    def from_wallet(cls, wallet, close_price: float, ts: int,
                    metadata: dict | None = None,
                    risk_state: dict | None = None) -> "Checkpoint":
        """Crea un checkpoint a partir del estado actual de la wallet."""
        return cls(
            ts=ts,
            close_price=close_price,
            usd_balance=wallet.get_usd_balance(),
            btc_balance=wallet.get_btc_balance(),
            btc_en_pos=wallet.btc_en_posiciones(),
            positions_count=wallet.positions_count,
            portfolio_value=wallet.portfolio_value(close_price),
            metadata=metadata,
            risk_state=risk_state,
        )

    def to_dict(self) -> dict:
        """Convierte el checkpoint a diccionario (serializable a JSON)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Checkpoint":
        return cls(
            ts=d["ts"],
            close_price=d["close_price"],
            usd_balance=d["usd_balance"],
            btc_balance=d["btc_balance"],
            btc_en_pos=d["btc_en_pos"],
            positions_count=d["positions_count"],
            portfolio_value=d["portfolio_value"],
            metadata=d.get("metadata"),
            risk_state=d.get("risk_state"),  # Nuevo: carga el estado de riesgo
        )


class StateManager:
    """Guarda y recupera checkpoints del estado del sistema."""

    def save(self, checkpoint: Checkpoint) -> None:
        """Persiste un checkpoint."""

    async def save_async(self, checkpoint: Checkpoint) -> None:
        """Persiste un checkpoint asíncronamente."""
        self.save(checkpoint)

    def load_latest(self) -> Optional[Checkpoint]:
        """Retorna el último checkpoint guardado, o None si no existe."""

    def history(self) -> List[Checkpoint]:
        """Retorna el historial completo de checkpoints."""
        return []

    def clear(self) -> None:
        """Limpia todos los checkpoints."""


class MemoryStateManager(StateManager):
    """Checkpoints en memoria — sin persistencia. Rápido para backtest."""

    def __init__(self) -> None:
        """Inicializa el gestor en memoria con historial vacío."""
        self._history: list[Checkpoint] = []

    def save(self, checkpoint: Checkpoint) -> None:
        """Persiste un checkpoint en memoria."""
        self._history.append(checkpoint)

    async def save_async(self, checkpoint: Checkpoint) -> None:
        """Persiste un checkpoint en memoria de forma asíncrona."""
        self.save(checkpoint)

    def load_latest(self) -> Optional[Checkpoint]:
        """Retorna el último checkpoint guardado, o None si no existe."""
        return self._history[-1] if self._history else None

    def history(self) -> List[Checkpoint]:
        """Retorna el historial completo de checkpoints."""
        return list(self._history)

    def clear(self) -> None:
        """Limpia todos los checkpoints en memoria."""
        self._history.clear()


class JSONStateManager(StateManager):
    """
    Checkpoints persistidos en un archivo JSON (líneas JSONL).
    G4: Sobrevive reinicios del proceso live.

    Formato: una línea JSON por checkpoint (JSONL).
    Al iniciar, carga el historial previo del archivo.
    """

    def __init__(self, path: str | Path, max_checkpoints: int = 5_000) -> None:
        """
        Args:
            path: Ruta al archivo .jsonl donde se guardarán los checkpoints.
            max_checkpoints: Máximo de checkpoints a mantener en memoria.
                             Cuando se supera, los más antiguos se descartan
                             en memoria (pero siguen en disco para auditoría).
        """
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._max = max_checkpoints
        self._history: list[Checkpoint] = []
        self._file_handle = None
        self._compact_lock = asyncio.Lock()
        self._load_existing()
        self._file_handle = open(self._path, "a", encoding="utf-8")

    def _load_existing(self) -> None:
        """Carga los checkpoints existentes del archivo al iniciar."""
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        self._history.append(Checkpoint.from_dict(d))
                    except (json.JSONDecodeError, KeyError):
                        continue  # Línea corrupta → ignorar
            # Mantener solo los últimos N
            if len(self._history) > self._max:
                self._history = self._history[-self._max:]
        except OSError:
            pass

    def save(self, checkpoint: Checkpoint) -> None:
        """Añade el checkpoint al historial en memoria y lo escribe en disco (síncrono).
        Usar en backtest donde no hay event loop activo."""
        self._history.append(checkpoint)
        # Descartar los más antiguos de memoria si supera el límite
        if len(self._history) > self._max:
            self._history.pop(0)
        # Escribir al archivo
        if self._file_handle:
            try:
                self._file_handle.write(json.dumps(checkpoint.to_dict()) + "\n")
                self._file_handle.flush()
            except OSError:
                pass

    async def save_async(self, checkpoint: Checkpoint) -> None:
        """Añade el checkpoint al historial en memoria y lo escribe en disco de forma
        asíncrona. Mantiene el file handle abierto (como save() síncrono) para evitar
        abrir/cerrar el archivo en cada escritura. Usar en modo live."""
        self._history.append(checkpoint)
        if len(self._history) > self._max:
            self._history.pop(0)
        # Asegurar que el file handle está abierto
        if self._file_handle is None:
            try:
                self._file_handle = open(self._path, "a", encoding="utf-8")
            except OSError:
                return
        try:
            # Usar el thread executor de asyncio para no bloquear el event loop
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, self._file_handle.write, json.dumps(checkpoint.to_dict()) + "\n"
            )
            await loop.run_in_executor(None, self._file_handle.flush)
        except OSError:
            pass

    def load_latest(self) -> Optional[Checkpoint]:
        """Retorna el último checkpoint guardado desde disco, o None si no existe."""
        return self._history[-1] if self._history else None

    def history(self) -> List[Checkpoint]:
        """Retorna el historial completo de checkpoints cargados desde disco."""
        return list(self._history)

    def clear(self) -> None:
        """Limpia el historial en memoria y trunca el archivo."""
        self._history.clear()
        if self._file_handle:
            try:
                self._file_handle.close()
            except OSError:
                pass
        try:
            with open(self._path, "w", encoding="utf-8"):
                pass  # Truncar
            self._file_handle = open(self._path, "a", encoding="utf-8")
        except OSError:
            pass

    def close(self) -> None:
        """Cierra el file handle (llamar al finalizar el proceso)."""
        if self._file_handle:
            try:
                self._file_handle.flush()
                self._file_handle.close()
                self._file_handle = None
            except OSError:
                pass

    def __del__(self) -> None:
        """Cierra el file handle al destruir el objeto."""
        self.close()

    def compact(self) -> None:
        """
        Re-escribe el archivo manteniendo solo los últimos max_checkpoints de forma segura y atómica.
        """
        if not self._path.exists():
            return
        temp_path = self._path.with_suffix(".tmp")
        try:
            self.close()
            with open(temp_path, "w", encoding="utf-8") as f:
                for ckpt in self._history:
                    f.write(json.dumps(ckpt.to_dict()) + "\n")
            os.replace(temp_path, self._path)
        except OSError:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
        finally:
            try:
                self._file_handle = open(self._path, "a", encoding="utf-8")
            except OSError:
                pass

    async def compact_async(self) -> None:
        """
        Versión asíncrona de compact() para usar en modo live.
        Re-escribe el archivo sin bloquear el event loop de forma segura y atómica.
        Usa asyncio.Lock para evitar que dos corrutinas ejecuten compact simultáneamente.
        """
        if not self._path.exists():
            return
        async with self._compact_lock:
            temp_path = self._path.with_suffix(".tmp")
            try:
                self.close()
                async with aiofiles.open(temp_path, "w", encoding="utf-8") as f:
                    for ckpt in self._history:
                        await f.write(json.dumps(ckpt.to_dict()) + "\n")
                os.replace(temp_path, self._path)
            except OSError:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass
            finally:
                try:
                    self._file_handle = open(self._path, "a", encoding="utf-8")
                except OSError:
                    pass

    @property
    def checkpoint_count(self) -> int:
        """Retorna la cantidad de checkpoints en memoria."""
        return len(self._history)

    @property
    def file_path(self) -> Path:
        """Retorna la ruta al archivo de checkpoints."""
        return self._path


class ResultsStateManager(StateManager):
    """Stores checkpoints inside the unified live results JSON file."""

    def __init__(self, path: str | Path, max_checkpoints: int = 5_000) -> None:
        self._store = ResultsStore(path)
        self._max = max_checkpoints
        self._history: list[Checkpoint] = []
        self._load_existing()

    def _load_existing(self) -> None:
        payload = self._store.load()
        self._history = []
        for entry in payload.get("checkpoints", []):
            try:
                self._history.append(Checkpoint.from_dict(entry))
            except (KeyError, TypeError, ValueError):
                continue
        if len(self._history) > self._max:
            self._history = self._history[-self._max:]

    def _write(self) -> None:
        checkpoints = [checkpoint.to_dict() for checkpoint in self._history[-self._max:]]
        risk_state = checkpoints[-1].get("risk_state") if checkpoints else None
        last_closed_ts = checkpoints[-1].get("ts") if checkpoints else None
        self._store.update(
            checkpoints=checkpoints,
            risk_state=risk_state,
            last_closed_ts=last_closed_ts,
        )

    def save(self, checkpoint: Checkpoint) -> None:
        self._history.append(checkpoint)
        if len(self._history) > self._max:
            self._history = self._history[-self._max:]
        self._write()

    async def save_async(self, checkpoint: Checkpoint) -> None:
        self.save(checkpoint)

    def load_latest(self) -> Optional[Checkpoint]:
        return self._history[-1] if self._history else None

    def history(self) -> List[Checkpoint]:
        return list(self._history)

    def clear(self) -> None:
        self._history.clear()
        self._write()

    def compact(self) -> None:
        self._write()

    async def compact_async(self) -> None:
        self.compact()

    def close(self) -> None:
        pass


def build_state_manager(
    mode: str = "memory",
    path: str | Path | None = None,
    max_checkpoints: int = 5_000,
) -> StateManager:
    """
    Factory para construir el StateManager adecuado al modo de ejecución.

    Args:
        mode: "memory" para backtest, "json" para live/testnet.
        path: Ruta del archivo JSONL (requerido si mode="json").
        max_checkpoints: Máximo de checkpoints en memoria para mode="json".
    """
    if mode == "json":
        if path is None:
            path = "state_checkpoints.jsonl"
        return JSONStateManager(path=path, max_checkpoints=max_checkpoints)
    if mode == "results":
        if path is None:
            path = "live_results.json"
        return ResultsStateManager(path=path, max_checkpoints=max_checkpoints)
    return MemoryStateManager()
