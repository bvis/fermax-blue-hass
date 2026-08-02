"""Tests for the Fermax Blue config flow."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

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

    def test_url_validation_rejects_http(self):
        """Test that credential URLs must use HTTPS."""
        from custom_components.fermax_blue.config_flow import _https_url

        with pytest.raises(vol.Invalid):
            _https_url("http://auth.example.com/token")

    def test_url_validation_accepts_https(self):
        """Test that HTTPS URLs are accepted."""
        from custom_components.fermax_blue.config_flow import _https_url

        result = _https_url("https://auth.example.com/token")
        assert result == "https://auth.example.com/token"

    def test_url_validation_rejects_garbage(self):
        """Test that invalid URLs are rejected."""
        from custom_components.fermax_blue.config_flow import _https_url

        with pytest.raises(vol.Invalid):
            _https_url("not-a-url")

    def test_credentials_schema_uses_serializable_url_fields(self):
        """Test credential URL fields do not use custom callable validators in the form."""
        from custom_components.fermax_blue.config_flow import STEP_CREDENTIALS_SCHEMA, _https_url

        schema_by_key = {
            marker.schema: validator for marker, validator in STEP_CREDENTIALS_SCHEMA.schema.items()
        }

        assert schema_by_key[CONF_FERMAX_AUTH_URL] is str
        assert schema_by_key[CONF_FERMAX_BASE_URL] is str
        assert _https_url not in schema_by_key.values()

    @pytest.mark.asyncio
    async def test_config_flow_rejects_non_https_urls_after_submit(self):
        """Test submitted credential URLs are still HTTPS-validated."""
        from custom_components.fermax_blue.config_flow import FermaxBlueConfigFlow

        flow = FermaxBlueConfigFlow()
        result = await flow._async_validate_and_create(
            {
                CONF_USERNAME: "user@example.com",
                CONF_PASSWORD: "password",
                CONF_FERMAX_AUTH_URL: "http://auth.example.com/oauth/token",
                CONF_FERMAX_BASE_URL: "https://api.example.com",
                CONF_FERMAX_AUTH_BASIC: "Basic dGVzdDp0ZXN0",
                CONF_FIREBASE_API_KEY: "AIzaTestKey",
                CONF_FIREBASE_SENDER_ID: "123456789012",
                CONF_FIREBASE_APP_ID: "1:123456789012:android:abcdef1234",
                CONF_FIREBASE_PROJECT_ID: "fermax-test",
                CONF_FIREBASE_PACKAGE_NAME: "com.fermax.blue.app",
            }
        )

        assert result["type"] == "form"
        assert result["step_id"] == "credentials"
        assert result["errors"][CONF_FERMAX_AUTH_URL] == "invalid_url"

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


def _credentials_payload() -> dict:
    """Return a complete set of config-flow credentials."""
    return {
        CONF_USERNAME: "User@Example.com",
        CONF_PASSWORD: "secret",
        CONF_FERMAX_AUTH_URL: "https://oauth-pro-duoxme.fermax.io/oauth/token",
        CONF_FERMAX_BASE_URL: "https://pro-duoxme.fermax.io",
        CONF_FERMAX_AUTH_BASIC: "Basic abc",
        CONF_FIREBASE_API_KEY: "AIza-key",
        CONF_FIREBASE_APP_ID: "1:1:android:1",
        CONF_FIREBASE_SENDER_ID: "1",
        CONF_FIREBASE_PROJECT_ID: "proj",
        CONF_FIREBASE_PACKAGE_NAME: "com.fermax.blue.app",
    }


def _make_flow():
    """Build a config flow with the HA plumbing stubbed out."""
    from unittest.mock import MagicMock

    from custom_components.fermax_blue.config_flow import FermaxBlueConfigFlow

    flow = FermaxBlueConfigFlow()
    flow.hass = MagicMock()
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_show_form = MagicMock(side_effect=lambda **kwargs: {"type": "form", **kwargs})
    flow.async_create_entry = MagicMock(
        side_effect=lambda **kwargs: {"type": "create_entry", **kwargs}
    )
    return flow


class TestConfigFlowSteps:
    """The interactive steps of the config flow."""

    @pytest.mark.asyncio
    async def test_user_step_shows_the_form_first(self):
        result = await _make_flow().async_step_user()

        assert result["type"] == "form"
        assert result["step_id"] == "user"

    @pytest.mark.asyncio
    async def test_user_step_advances_to_credentials(self):
        flow = _make_flow()

        result = await flow.async_step_user(
            {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"}
        )

        assert result["step_id"] == "credentials"
        assert flow._user_data[CONF_USERNAME] == "user@example.com"

    @pytest.mark.asyncio
    async def test_credentials_step_shows_the_form_first(self):
        result = await _make_flow().async_step_credentials()

        assert result["type"] == "form"
        assert result["step_id"] == "credentials"

    @pytest.mark.asyncio
    async def test_http_urls_are_rejected_without_calling_the_api(self, monkeypatch):
        """Plain HTTP would send the OAuth Basic header in clear text."""
        from custom_components.fermax_blue import config_flow

        api_factory = MagicMock()
        monkeypatch.setattr(config_flow, "FermaxBlueApi", api_factory)

        data = _credentials_payload()
        data[CONF_FERMAX_AUTH_URL] = "http://oauth-pro-duoxme.fermax.io/oauth/token"

        result = await _make_flow()._async_validate_and_create(data)

        assert result["errors"] == {CONF_FERMAX_AUTH_URL: "invalid_url"}
        api_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_auth_is_reported_on_the_credentials_step(self, monkeypatch):
        from custom_components.fermax_blue import config_flow

        api = MagicMock()
        api.authenticate = AsyncMock(side_effect=FermaxAuthError("bad"))
        monkeypatch.setattr(config_flow, "FermaxBlueApi", MagicMock(return_value=api))

        result = await _make_flow()._async_validate_and_create(_credentials_payload())

        assert result["step_id"] == "credentials"
        assert result["errors"] == {"base": "invalid_auth"}

    @pytest.mark.asyncio
    async def test_unexpected_errors_map_to_cannot_connect(self, monkeypatch):
        from custom_components.fermax_blue import config_flow

        api = MagicMock()
        api.authenticate = AsyncMock(side_effect=OSError("network down"))
        monkeypatch.setattr(config_flow, "FermaxBlueApi", MagicMock(return_value=api))

        result = await _make_flow()._async_validate_and_create(_credentials_payload())

        assert result["errors"] == {"base": "cannot_connect"}

    @pytest.mark.asyncio
    async def test_account_without_pairings_is_rejected(self, monkeypatch):
        from custom_components.fermax_blue import config_flow

        api = MagicMock()
        api.authenticate = AsyncMock()
        api.get_pairings = AsyncMock(return_value=[])
        monkeypatch.setattr(config_flow, "FermaxBlueApi", MagicMock(return_value=api))

        result = await _make_flow()._async_validate_and_create(_credentials_payload())

        assert result["errors"] == {"base": "no_devices"}

    @pytest.mark.asyncio
    async def test_successful_setup_creates_the_entry(self, monkeypatch):
        from custom_components.fermax_blue import config_flow

        pairing = MagicMock()
        pairing.tag = "Home"
        api = MagicMock()
        api.authenticate = AsyncMock()
        api.get_pairings = AsyncMock(return_value=[pairing])
        monkeypatch.setattr(config_flow, "FermaxBlueApi", MagicMock(return_value=api))
        flow = _make_flow()

        result = await flow._async_validate_and_create(_credentials_payload())

        assert result["type"] == "create_entry"
        assert result["title"] == "Fermax Blue (Home)"
        # The unique id is case-insensitive so the same account cannot be added twice
        flow.async_set_unique_id.assert_awaited_once_with("user@example.com")
        flow._abort_if_unique_id_configured.assert_called_once()


class TestOptionsFlow:
    """The options flow (scan interval, retention)."""

    @pytest.mark.asyncio
    async def test_shows_the_form_first(self):
        from unittest.mock import MagicMock

        from custom_components.fermax_blue.config_flow import FermaxBlueOptionsFlow

        flow = FermaxBlueOptionsFlow()
        flow.hass = MagicMock()
        flow.handler = "entry_1"
        flow.hass.config_entries.async_get_entry.return_value = MagicMock(options={})
        flow.async_show_form = MagicMock(side_effect=lambda **kwargs: {"type": "form", **kwargs})

        result = await flow.async_step_init()

        assert result["step_id"] == "init"

    @pytest.mark.asyncio
    async def test_submitting_saves_the_options(self):
        from unittest.mock import MagicMock

        from custom_components.fermax_blue.config_flow import FermaxBlueOptionsFlow

        flow = FermaxBlueOptionsFlow()
        flow.hass = MagicMock()
        flow.handler = "entry_1"
        flow.hass.config_entries.async_get_entry.return_value = MagicMock(options={})
        flow.async_create_entry = MagicMock(
            side_effect=lambda **kwargs: {"type": "create_entry", **kwargs}
        )

        result = await flow.async_step_init({"scan_interval": 10})

        assert result["data"] == {"scan_interval": 10}
