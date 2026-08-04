"""
test_time_utils.py — Tests unitarios para support/time_utils.py
================================================================
Cubre: support/time_utils.py
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from support.time_utils import (
    to_epoch_s, to_epoch_ms, to_datetime, to_iso, to_date_str,
    now_epoch_s, epoch_s_from_date_str,
)


class TestToEpochS:
    """Tests para la función central to_epoch_s."""

    def test_int_ms(self):
        assert to_epoch_s(1_700_000_000_000) == 1_700_000_000

    def test_int_s(self):
        assert to_epoch_s(1_700_000_000) == 1_700_000_000

    def test_float_ms(self):
        assert to_epoch_s(1_700_000_000_999.0) == 1_700_000_000

    def test_float_s(self):
        assert to_epoch_s(1_700_000_000.9) == 1_700_000_000

    def test_str_iso_with_t(self):
        """2024-01-15T08:00:00 → epoch."""
        result = to_epoch_s("2024-01-15T08:00:00")
        expected = int(datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc).timestamp())
        assert result == expected

    def test_str_iso_with_space(self):
        result = to_epoch_s("2024-01-15 08:00:00")
        expected = int(datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc).timestamp())
        assert result == expected

    def test_str_date_only(self):
        result = to_epoch_s("2024-01-15")
        expected = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp())
        assert result == expected

    def test_str_with_z_suffix(self):
        result = to_epoch_s("2024-01-15T08:00:00Z")
        expected = int(datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc).timestamp())
        assert result == expected

    def test_datetime_naive(self):
        dt = datetime(2024, 1, 15, 8, 0, 0)
        expected = int(datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc).timestamp())
        assert to_epoch_s(dt) == expected

    def test_datetime_aware(self):
        from datetime import timedelta
        dt = datetime(2024, 1, 15, 5, 0, 0, tzinfo=timezone(timedelta(hours=-3)))
        expected = int(datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc).timestamp())
        assert to_epoch_s(dt) == expected

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Formato de fecha no reconocido"):
            to_epoch_s("not-a-date")

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError):
            to_epoch_s([1, 2, 3])


class TestConversionHelpers:

    def test_to_epoch_ms(self):
        assert to_epoch_ms(1_700_000_000) == 1_700_000_000_000

    def test_to_datetime(self):
        expected = int(datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc).timestamp())
        dt = to_datetime(expected)
        assert isinstance(dt, datetime)
        assert dt.year == 2024 and dt.month == 1 and dt.day == 15
        assert dt.hour == 8
        assert dt.tzinfo is not None

    def test_to_iso(self):
        expected = int(datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc).timestamp())
        assert to_iso(expected) == "2024-01-15T08:00:00Z"

    def test_to_date_str(self):
        expected = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp())
        assert to_date_str(expected) == "2024-01-15"

    def test_now_epoch_s_close_to_time(self):
        import time
        now = now_epoch_s()
        assert abs(now - int(time.time())) <= 2

    def test_epoch_s_from_date_str(self):
        result = epoch_s_from_date_str("2024-01-15")
        expected = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp())
        assert result == expected