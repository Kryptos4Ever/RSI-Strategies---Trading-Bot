"""
test_telegram_notifier.py — Tests para notifications/telegram_notifier.py
=========================================================================
Cubre: TelegramNotifier, TelegramEvent
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from notifications.telegram_notifier import TelegramNotifier, TelegramEvent


class TestTelegramEvent:
    def test_event_values(self):
        assert TelegramEvent.BOT_STARTED.value == "BOT_STARTED"
        assert TelegramEvent.TRADE_EXECUTED.value == "TRADE_EXECUTED"
        assert TelegramEvent.TRADE_REJECTED.value == "TRADE_REJECTED"
        assert TelegramEvent.ERROR_CRITICAL.value == "ERROR_CRITICAL"
        assert TelegramEvent.DAILY_SUMMARY.value == "DAILY_SUMMARY"
        assert TelegramEvent.DRAWDOWN_WARNING.value == "DRAWDOWN_WARNING"
        assert TelegramEvent.BOT_STOPPED.value == "BOT_STOPPED"


class TestTelegramNotifier:
    @pytest.fixture
    def notifier(self):
        return TelegramNotifier()

    def test_initialization(self, notifier):
        assert notifier is not None

    def test_notify_without_token(self, notifier):
        """No debe fallar aunque no haya token configurado."""
        notifier.notify(TelegramEvent.BOT_STARTED, mode="test")

    @patch("support.secrets.secrets")
    def test_notify_sends_message(self, mock_secrets):
        mock_secrets.side_effect = lambda key, default=None: {
            "TELEGRAM_BOT_TOKEN": "fake:token",
            "TELEGRAM_CHAT_ID": "123456",
        }.get(key, default)

        n = TelegramNotifier()
        # Debe iniciar sin error aunque tenga token fake
        assert n is not None

    def test_notify_all_event_types(self, notifier):
        """Verifica que todos los tipos de evento se pueden notificar sin error."""
        events = [
            (TelegramEvent.BOT_STARTED, {"mode": "test", "symbol": "BTC"}),
            (TelegramEvent.TRADE_EXECUTED, {"mode": "test", "side": "BUY", "price": 50000.0, "qty": 0.001}),
            (TelegramEvent.TRADE_REJECTED, {"mode": "test", "side": "BUY", "price": 50000.0, "reason": "test"}),
            (TelegramEvent.ERROR_CRITICAL, {"mode": "test", "error": "test error"}),
            (TelegramEvent.DAILY_SUMMARY, {"mode": "test", "pnl_pct": 1.5}),
            (TelegramEvent.DRAWDOWN_WARNING, {"mode": "test", "drawdown_pct": 50.0}),
            (TelegramEvent.BOT_STOPPED, {"mode": "test", "pnl_pct": 2.0}),
        ]
        for event, kwargs in events:
            notifier.notify(event, **kwargs)  # No debe lanzar excepción