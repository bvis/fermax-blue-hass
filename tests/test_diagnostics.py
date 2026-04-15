"""Tests for diagnostics module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.fermax_blue.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)


class TestDiagnostics:
    def test_redact_keys_include_sensitive_fields(self):
        assert "password" in TO_REDACT
        assert "username" in TO_REDACT
        assert "access_token" in TO_REDACT
        assert "fcm_token" in TO_REDACT
        assert "fermax_auth_basic" in TO_REDACT
        assert "firebase_api_key" in TO_REDACT

    @pytest.mark.asyncio
    async def test_diagnostics_includes_operational_data(self):
        mock_hass = MagicMock()
        mock_entry = MagicMock()
        mock_entry.data = {"username": "user@test.com", "password": "secret"}
        mock_entry.options = {"scan_interval": 5}
        mock_entry.entry_id = "test_entry"

        mock_coordinator = MagicMock()
        mock_coordinator.pairing = MagicMock()
        mock_coordinator.pairing.device_id = "dev1"
        mock_coordinator.pairing.tag = "MyDevice"
        mock_coordinator.notification_listener = MagicMock()
        mock_coordinator.notification_listener.is_started = True
        mock_coordinator.notification_listener.fcm_token = "token123"
        mock_coordinator.device_info = None
        mock_coordinator.data = {"status": "ok"}
        mock_coordinator.stream_session = None

        mock_hass.data = {"fermax_blue": {"test_entry": [mock_coordinator]}}

        result = await async_get_config_entry_diagnostics(mock_hass, mock_entry)

        assert result["config_entry"]["data"]["username"] == "**REDACTED**"
        assert result["config_entry"]["data"]["password"] == "**REDACTED**"
        assert result["config_entry"]["options"] == {"scan_interval": 5}
        assert len(result["devices"]) == 1
        device = result["devices"][0]
        assert device["device_id"] == "dev1"
        assert device["notification_listener"] == "running"
        assert device["fcm_token"] == "**REDACTED**"
