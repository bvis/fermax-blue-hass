"""Tests for the Fermax Blue config flow."""

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.fermax_blue.api import FermaxAuthError
from custom_components.fermax_blue.const import DOMAIN


class TestConfigFlow:
    """Test the config flow."""

    @pytest.mark.asyncio
    async def test_form_displayed(self, hass):
        """Test that the user form is shown."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == "form"
        assert result["step_id"] == "user"

    @pytest.mark.asyncio
    async def test_successful_config(self, hass, mock_api):
        """Test a successful configuration."""
        with patch(
            "custom_components.fermax_blue.config_flow.FermaxBlueApi",
            return_value=mock_api,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "user"},
                data={
                    "username": "test@example.com",
                    "password": "testpass",
                },
            )

        assert result["type"] == "create_entry"
        assert result["title"] == "Fermax Blue (Test Home)"

    @pytest.mark.asyncio
    async def test_invalid_auth(self, hass):
        """Test error on invalid credentials."""
        api = AsyncMock()
        api.authenticate = AsyncMock(side_effect=FermaxAuthError("Bad credentials"))

        with patch(
            "custom_components.fermax_blue.config_flow.FermaxBlueApi",
            return_value=api,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "user"},
                data={
                    "username": "wrong@example.com",
                    "password": "wrong",
                },
            )

        assert result["type"] == "form"
        assert result["errors"] == {"base": "invalid_auth"}
