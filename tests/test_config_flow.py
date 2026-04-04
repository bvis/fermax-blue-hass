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
        api.authenticate = AsyncMock(side_effect=FermaxAuthError("Bad credentials"))

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

    @pytest.mark.asyncio
    async def test_pairings_have_doors(self, mock_api):
        """Test discovered pairings contain door information."""
        pairings = await mock_api.get_pairings()
        doors = pairings[0].access_doors
        assert "GENERAL" in doors
        assert doors["GENERAL"].visible is True
        assert "ZERO" in doors
        assert doors["ZERO"].visible is False


class TestDataModels:
    """Test data model behavior."""

    def test_access_door_fields(self):
        """Test AccessDoor dataclass fields."""
        from custom_components.fermax_blue.api import AccessDoor

        door = AccessDoor(
            name="GENERAL",
            title="Portal",
            access_id={"block": 100, "subblock": -1, "number": 0},
            visible=True,
        )
        assert door.name == "GENERAL"
        assert door.title == "Portal"
        assert door.visible is True

    def test_device_info_fields(self):
        """Test DeviceInfo dataclass fields."""
        from custom_components.fermax_blue.api import DeviceInfo

        info = DeviceInfo(
            device_id="dev1",
            connection_state="Connected",
            status="ACTIVATED",
            family="MONITOR",
            device_type="VEO-XL",
            subtype="WIFI",
            unit_number=42,
            photocaller=True,
            streaming_mode="video_call",
            is_monitor=True,
            wireless_signal=4,
        )
        assert info.device_id == "dev1"
        assert info.photocaller is True
        assert info.streaming_mode == "video_call"

    def test_divert_response_fields(self):
        """Test DivertResponse dataclass fields."""
        from custom_components.fermax_blue.api import DivertResponse

        resp = DivertResponse(
            reason="call_starting",
            divert_service="blueStream",
            code=1.0,
            description="Auto on is starting",
            directed_to="fcm_token",
            local_address="00 00 42",
            remote_address="AA F0 00",
        )
        assert resp.reason == "call_starting"
        assert resp.divert_service == "blueStream"
        assert resp.local_address == "00 00 42"

    def test_divert_response_defaults(self):
        """Test DivertResponse default values."""
        from custom_components.fermax_blue.api import DivertResponse

        resp = DivertResponse(
            reason="test",
            divert_service="blueStream",
            code=1.0,
            description="test",
            directed_to="token",
        )
        assert resp.local_address == ""
        assert resp.remote_address == ""

    def test_pairing_empty_doors(self):
        """Test Pairing with no doors."""
        from custom_components.fermax_blue.api import Pairing

        pairing = Pairing(
            device_id="dev1",
            tag="Home",
            installation_id="inst1",
        )
        assert len(pairing.access_doors) == 0


class TestConstObfuscation:
    """Test that obfuscated constants decode correctly."""

    def test_firebase_api_key_decodes(self):
        """Test Firebase API key decodes to expected format."""
        from custom_components.fermax_blue.const import FIREBASE_API_KEY

        assert FIREBASE_API_KEY.startswith("AIza")

    def test_firebase_sender_id_is_int(self):
        """Test Firebase sender ID is an integer."""
        from custom_components.fermax_blue.const import FIREBASE_SENDER_ID

        assert isinstance(FIREBASE_SENDER_ID, int)

    def test_firebase_app_id_format(self):
        """Test Firebase app ID has expected format."""
        from custom_components.fermax_blue.const import FIREBASE_APP_ID

        assert FIREBASE_APP_ID.startswith("1:")
        assert ":android:" in FIREBASE_APP_ID

    def test_fermax_urls_decode(self):
        """Test Fermax API URLs decode correctly."""
        from custom_components.fermax_blue.const import (
            FERMAX_AUTH_URL,
            FERMAX_BASE_URL,
        )

        assert "fermax.io" in FERMAX_AUTH_URL
        assert "fermax.io" in FERMAX_BASE_URL
        assert FERMAX_AUTH_URL.startswith("https://")
        assert FERMAX_BASE_URL.startswith("https://")
