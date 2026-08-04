"""Runner compartido para ejecutar tests con ``python test_x.py``."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


class _TerminalExplanationPlugin:
    def pytest_collection_modifyitems(self, config, items):
        print("\nTests detectados:")
        for item in items:
            obj = getattr(item, "obj", None)
            doc = inspect.getdoc(obj) if obj is not None else None
            detail = f" - {doc.splitlines()[0]}" if doc else ""
            print(f"  - {item.nodeid}{detail}")
        print()

    def pytest_runtest_logstart(self, nodeid, location):
        print(f"\nEjecutando: {nodeid}")


def run_current_test_file(file_path: str) -> int:
    path = Path(file_path).resolve()
    module_doc = ""
    try:
        text = path.read_text(encoding="utf-8")
        if text.startswith('"""'):
            module_doc = text.split('"""', 2)[1].strip().splitlines()[0]
    except OSError:
        pass

    print("=" * 78)
    print("Ejecucion directa de test")
    print(f"Archivo : {path.name}")
    print(f"Carpeta : {path.parent}")
    if module_doc:
        print(f"Objetivo: {module_doc}")
    print("Modo    : pytest -vv -s --tb=short sobre este archivo")
    print("=" * 78)

    return pytest.main(
        [str(path), "-vv", "-s", "--tb=short"],
        plugins=[_TerminalExplanationPlugin()],
    )
