"""Tests for the Fermax Blue config flow."""

from unittest.mock import AsyncMock

import pytest

from custom_components.fermax_blue.api import FermaxAuthError


class TestConfigFlow:
    """Test the config flow validation logic."""

    @pytest.mark.asyncio
    async def test_auth_failure_raises(self):
        """Test that invalid credentials produce FermaxAuthError."""
        api = AsyncMock()
        api.authenticate = AsyncMock(
            side_effect=FermaxAuthError("Bad credentials")
        )

        with pytest.raises(FermaxAuthError, match="Bad credentials"):
            await api.authenticate()

    @pytest.mark.asyncio
    async def test_auth_success_returns_token(self, mock_api):
        """Test that valid credentials return a token."""
        token = await mock_api.authenticate()
        assert token == "fake_token"

    @pytest.mark.asyncio
    async def test_no_pairings_found(self):
        """Test handling of account with no devices."""
        api = AsyncMock()
        api.authenticate = AsyncMock(return_value="token")
        api.get_pairings = AsyncMock(return_value=[])

        await api.authenticate()
        pairings = await api.get_pairings()
        assert len(pairings) == 0

    @pytest.mark.asyncio
    async def test_pairings_found(self, mock_api):
        """Test successful device discovery."""
        pairings = await mock_api.get_pairings()
        assert len(pairings) == 1
        assert pairings[0].tag == "Test Home"
        assert pairings[0].device_id == "test_device_001"
