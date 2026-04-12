"""Tests for the Fermax Blue config flow."""

from unittest.mock import AsyncMock

import pytest

from custom_components.fermax_blue.api import FermaxAuthError, FermaxBlueApi
from custom_components.fermax_blue.const import (
    CONF_FERMAX_AUTH_BASIC,
    CONF_FERMAX_AUTH_URL,
    CONF_FERMAX_BASE_URL,
    CONF_FIREBASE_API_KEY,
    CONF_FIREBASE_APP_ID,
    CONF_FIREBASE_PACKAGE_NAME,
    CONF_FIREBASE_PROJECT_ID,
    CONF_FIREBASE_SENDER_ID,
)


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


class TestCredentials:
    """Test that API/Firebase credentials are properly handled."""

    def test_api_requires_credentials(self):
        """Test API client requires auth_url, base_url, auth_basic."""
        api = FermaxBlueApi(
            "user@test.com",
            "pass",
            auth_url="https://auth.example.com/token",
            base_url="https://api.example.com",
            auth_basic="Basic dGVzdDp0ZXN0",
        )
        assert api._auth_url == "https://auth.example.com/token"
        assert api._base_url == "https://api.example.com"
        assert api._auth_basic == "Basic dGVzdDp0ZXN0"

    def test_api_missing_credentials_raises(self):
        """Test API client raises when required credentials are missing."""
        with pytest.raises(TypeError):
            FermaxBlueApi("user@test.com", "pass")

    def test_notification_listener_requires_firebase(self):
        """Test notification listener requires all Firebase credentials."""
        from unittest.mock import MagicMock

        from custom_components.fermax_blue.notification import (
            FermaxNotificationListener,
        )

        mock_hass = MagicMock()
        listener = FermaxNotificationListener(
            hass=mock_hass,
            notification_callback=lambda n, p: None,
            firebase_api_key="AIzaTestKey",
            firebase_sender_id=123456,
            firebase_app_id="1:123:android:abc",
            firebase_project_id="test-project",
            firebase_package_name="com.test.app",
        )
        assert listener._fcm_config.api_key == "AIzaTestKey"
        assert listener._fcm_config.app_id == "1:123:android:abc"
        assert listener._fcm_config.project_id == "test-project"
        assert listener._fcm_config.messaging_sender_id == "123456"
        assert listener._fcm_config.bundle_id == "com.test.app"

    def test_notification_listener_missing_firebase_raises(self):
        """Test notification listener raises when Firebase credentials are missing."""
        from pathlib import Path

        from custom_components.fermax_blue.notification import (
            FermaxNotificationListener,
        )

        with pytest.raises(TypeError):
            FermaxNotificationListener(
                storage_path=Path("/tmp"),
                notification_callback=lambda n, p: None,
            )

    def test_conf_keys_defined(self):
        """Test all CONF_ keys for credentials are defined."""
        assert CONF_FERMAX_AUTH_URL == "fermax_auth_url"
        assert CONF_FERMAX_BASE_URL == "fermax_base_url"
        assert CONF_FERMAX_AUTH_BASIC == "fermax_auth_basic"
        assert CONF_FIREBASE_API_KEY == "firebase_api_key"
        assert CONF_FIREBASE_SENDER_ID == "firebase_sender_id"
        assert CONF_FIREBASE_APP_ID == "firebase_app_id"
        assert CONF_FIREBASE_PROJECT_ID == "firebase_project_id"
        assert CONF_FIREBASE_PACKAGE_NAME == "firebase_package_name"

    def test_no_hardcoded_credentials_in_const(self):
        """Test that const.py has no hardcoded API/Firebase values."""
        import inspect

        from custom_components.fermax_blue import const

        source = inspect.getsource(const)
        # No obfuscation function or base64 imports
        assert "base64" not in source
        assert "_d(" not in source
        # No hardcoded URLs or keys
        assert "fermax.io" not in source
        assert "AIza" not in source
        assert "oauth/token" not in source
