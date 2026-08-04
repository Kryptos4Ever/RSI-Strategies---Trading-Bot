"""Versioned store for live execution results.

`live_results_{environment}.json` is the operational source of truth.  The
schema uses `usd_*` names for account-level notional values.  Old result files
are intentionally not migrated; deleting the results file is the supported reset
path during this restructuring.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
ACCOUNT_CURRENCY = "USD"


def _windows_safe_replace(src: Path, dst: Path, max_retries: int = 3, delay: float = 0.2) -> None:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(delay)
    try:
        shutil.copy2(src, dst)
        try:
            src.unlink()
        except OSError:
            pass
    except Exception as copy_exc:
        raise (last_exc or copy_exc) from copy_exc


def collateral_currency_for_environment(environment: str | None, explicit: str | None = None) -> str:
    """Return the actual collateral currency for an environment.

    The account/reporting unit remains USD.  Hyperliquid collateral defaults to
    USDC; future environments can pass an explicit value such as USDT.
    """
    if explicit:
        return explicit.upper()
    env = (environment or "").lower()
    if env.startswith("hyperliquid"):
        return "USDC"
    return "USD"


def normalize_payload(
    payload: dict[str, Any] | None,
    *,
    environment: str | None = None,
    symbol: str | None = None,
    collateral_currency: str | None = None,
) -> dict[str, Any]:
    """Normalize legacy and current live-results payloads to schema v2."""
    raw = deepcopy(payload or {})
    summary = dict(raw.get("summary") or {})
    config = dict(summary.get("config") or raw.get("config") or {})
    env = environment or summary.get("environment") or raw.get("environment")
    sym = symbol or summary.get("symbol") or raw.get("symbol")
    collateral = collateral_currency_for_environment(
        env,
        collateral_currency or raw.get("collateral_currency") or summary.get("collateral_currency"),
    )

    initial_capital = raw.get("initial_capital_usd", summary.get("saldo_inicial_usd", 0.0))

    trades = list(raw.get("trade_history", []))
    history = sorted(raw.get("history_candles", []), key=lambda c: c.get("ts", 0))
    checkpoints = sorted(raw.get("checkpoints", []), key=lambda c: c.get("ts", 0))

    summary["account_currency"] = ACCOUNT_CURRENCY
    summary["collateral_currency"] = collateral

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "environment": env,
        "symbol": sym,
        "account_currency": ACCOUNT_CURRENCY,
        "collateral_currency": collateral,
        "initial_capital_usd": float(initial_capital or 0.0),
        "summary": summary,
        "trade_history": trades,
        "history_candles": history,
        "action_logs": raw.get("action_logs", []),
        "checkpoints": checkpoints,
        "risk_state": raw.get("risk_state"),
        "pending_orders": raw.get("pending_orders", []),
        "last_closed_ts": raw.get("last_closed_ts"),
        "metadata": raw.get("metadata", {}),
    }
    return normalized


def merge_candles(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ts: dict[int, dict[str, Any]] = {}
    for candle in existing + incoming:
        if not isinstance(candle, dict) or "ts" not in candle:
            continue
        ts = int(candle["ts"])
        merged = dict(by_ts.get(ts, {}))
        merged.update(candle)
        by_ts[ts] = merged
    return [by_ts[ts] for ts in sorted(by_ts)]


class ResultsStore:
    """Read/write helper for the unified live results JSON file."""

    def __init__(
        self,
        path: str | Path,
        *,
        environment: str | None = None,
        symbol: str | None = None,
        collateral_currency: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.environment = environment
        self.symbol = symbol
        self.collateral_currency = collateral_currency_for_environment(environment, collateral_currency)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return normalize_payload(
                {},
                environment=self.environment,
                symbol=self.symbol,
                collateral_currency=self.collateral_currency,
            )
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            data = {}
        return normalize_payload(
            data,
            environment=self.environment,
            symbol=self.symbol,
            collateral_currency=self.collateral_currency,
        )

    def save(self, payload: dict[str, Any]) -> None:
        normalized = normalize_payload(
            payload,
            environment=self.environment,
            symbol=self.symbol,
            collateral_currency=self.collateral_currency,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False, default=str)
        _windows_safe_replace(temp_path, self.path)

    def update(self, **changes: Any) -> dict[str, Any]:
        payload = self.load()
        for key, value in changes.items():
            if key == "history_candles":
                payload[key] = merge_candles(payload.get(key, []), value or [])
            elif key == "trade_history":
                incoming = list(value or [])
                existing = payload.get("trade_history", [])
                if not incoming and existing:
                    pass  # Preservar trades existentes si la actualización envía lista vacía
                else:
                    payload[key] = incoming
            elif key == "summary":
                summary = dict(payload.get("summary") or {})
                summary.update(value or {})
                payload[key] = summary
            else:
                payload[key] = value
        self.save(payload)
        return self.load()
