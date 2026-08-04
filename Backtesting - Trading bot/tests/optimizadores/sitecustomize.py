"""Permite ejecutar tests directamente desde esta carpeta."""
from __future__ import annotations

import sys
from pathlib import Path


def _add_repo_root_to_path() -> None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists() and (parent / "Backtest_Dual_Bands.py").exists():
            root = str(parent)
            if root not in sys.path:
                sys.path.insert(0, root)
            return


_add_repo_root_to_path()
