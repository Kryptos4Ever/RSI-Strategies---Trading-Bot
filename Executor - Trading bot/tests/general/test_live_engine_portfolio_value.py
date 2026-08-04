from __future__ import annotations

import ast
from pathlib import Path


def test_live_engine_passes_price_to_portfolio_value():
    source_path = Path(__file__).resolve().parents[2] / "engine" / "live_engine.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    calls_without_price = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "portfolio_value":
            if not node.args and not node.keywords:
                calls_without_price.append(node.lineno)

    assert calls_without_price == []
