"""Tests for the Fermax Blue API client."""

from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from custom_components.fermax_blue.api import (
    FermaxAuthError,
    FermaxBlueApi,
    OpeningRecord,
)


@pytest.fixture
def api():
    """Return a FermaxBlueApi instance."""
    return FermaxBlueApi(
        "test@example.com",
        "testpass123",
        auth_url="https://oauth.example.com/token",
        base_url="https://api.example.com",
        auth_basic="Basic dGVzdDp0ZXN0",
    )


@pytest.fixture
def authenticated_api(api):
    """Return an authenticated API instance."""
    api._access_token = "valid_token"
    api._token_expires_at = 9999999999
    return api


def _mock_response(status_code, **kwargs):
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code,
        request=httpx.Request("GET", "https://test.com"),
        **kwargs,
    )


_DEVICE_INFO_JSON = {
    "deviceId": "dev1",
    "connectionState": "Connected",
    "status": "ACTIVATED",
    "family": "MONITOR",
    "type": "VEO-XL",
    "subtype": "WIFI",
    "unitNumber": 42,
    "photocaller": True,
    "streamingMode": "video_call",
    "isMonitor": True,
    "wirelessSignal": 4,
}


class TestAuthentication:
    """Test authentication flow."""

    @pytest.mark.asyncio
    async def test_successful_auth(self, api):
        """Test successful authentication returns token."""
        resp = _mock_response(
            200,
            json={
                "access_token": "test_token_123",
                "expires_in": 3600,
                "token_type": "bearer",
            },
        )

        with patch("httpx.AsyncClient.post", return_value=resp):
            token = await api.authenticate()

        assert token == "test_token_123"
        assert api.is_authenticated

    @pytest.mark.asyncio
    async def test_invalid_credentials(self, api):
        """Test authentication with invalid credentials raises error."""
        resp = _mock_response(
            401,
            json={
                "error": "invalid_grant",
                "error_description": "Bad credentials",
            },
        )

        with (
            patch("httpx.AsyncClient.post", return_value=resp),
            pytest.raises(FermaxAuthError, match="Bad credentials"),
        ):
            await api.authenticate()

    @pytest.mark.asyncio
    async def test_token_expiry_detected(self, api):
        """Test that expired tokens are detected."""
        import time

        api._access_token = "old_token"
        api._token_expires_at = time.time() - 100

        assert not api.is_authenticated

    @pytest.mark.asyncio
    async def test_token_not_set(self, api):
        """Test unauthenticated state."""
        assert not api.is_authenticated

    @pytest.mark.asyncio
    async def test_auto_reauthentication(self, api):
        """Test that expired tokens trigger re-authentication."""
        import time

        api._access_token = "expired"
        api._token_expires_at = time.time() - 100

        auth_resp = _mock_response(
            200,
            json={
                "access_token": "new_token",
                "expires_in": 3600,
            },
        )
        data_resp = _mock_response(200, json=_DEVICE_INFO_JSON)

        with (
            patch("httpx.AsyncClient.post", return_value=auth_resp),
            patch("httpx.AsyncClient.get", return_value=data_resp),
        ):
            info = await api.get_device_info("dev1")

        assert info.device_id == "dev1"
        assert api._access_token == "new_token"


class TestRetryLogic:
    """Test request retry with backoff."""

    @pytest.mark.asyncio
    async def test_retry_on_500(self, authenticated_api):
        fail_resp = _mock_response(500, text="error")
        ok_resp = _mock_response(200, json=_DEVICE_INFO_JSON)
        with (
            patch("httpx.AsyncClient.get", side_effect=[fail_resp, ok_resp]),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            info = await authenticated_api.get_device_info("dev1")
        assert info.device_id == "dev1"

    @pytest.mark.asyncio
    async def test_no_retry_on_401(self, authenticated_api):
        resp = _mock_response(401, json={"error": "unauthorized"})
        with (
            patch("httpx.AsyncClient.get", return_value=resp),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await authenticated_api.get_device_info("dev1")

    @pytest.mark.asyncio
    async def test_retry_exhausted(self, authenticated_api):
        fail_resp = _mock_response(500, text="error")
        with (
            patch("httpx.AsyncClient.get", return_value=fail_resp),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await authenticated_api.get_device_info("dev1")

    @pytest.mark.asyncio
    async def test_retry_on_connection_error(self, authenticated_api):
        ok_resp = _mock_response(200, json=_DEVICE_INFO_JSON)
        with (
            patch(
                "httpx.AsyncClient.get",
                side_effect=[httpx.ConnectError("fail"), ok_resp],
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            info = await authenticated_api.get_device_info("dev1")
        assert info.device_id == "dev1"


class TestPairings:
    """Test pairing retrieval."""

    @pytest.mark.asyncio
    async def test_get_pairings(self, authenticated_api):
        """Test fetching paired devices."""
        resp = _mock_response(
            200,
            json=[
                {
                    "deviceId": "device_123",
                    "tag": "My Home",
                    "installationId": "inst_001",
                    "accessDoorMap": {
                        "GENERAL": {
                            "title": "Portal",
                            "accessId": {"block": 100, "subblock": -1, "number": 0},
                            "visible": True,
                        },
                        "ZERO": {
                            "title": "Door X",
                            "accessId": {"block": 200, "subblock": -1, "number": 0},
                            "visible": False,
                        },
                    },
                }
            ],
        )

        with patch("httpx.AsyncClient.get", return_value=resp):
            pairings = await authenticated_api.get_pairings()

        assert len(pairings) == 1
        assert pairings[0].device_id == "device_123"
        assert pairings[0].tag == "My Home"
        assert len(pairings[0].access_doors) == 2
        assert pairings[0].access_doors["GENERAL"].visible is True
        assert pairings[0].access_doors["ZERO"].visible is False

    @pytest.mark.asyncio
    async def test_empty_pairings(self, authenticated_api):
        """Test when no devices are paired."""
        resp = _mock_response(200, json=[])

        with patch("httpx.AsyncClient.get", return_value=resp):
            pairings = await authenticated_api.get_pairings()

        assert len(pairings) == 0


class TestDoorControl:
    """Test door opening."""

    @pytest.mark.asyncio
    async def test_open_door_success(self, authenticated_api):
        """Test successful door opening."""
        resp = _mock_response(200, text="la puerta abierta")

        with patch("httpx.AsyncClient.post", return_value=resp):
            result = await authenticated_api.open_door(
                "device_123",
                {"block": 100, "subblock": -1, "number": 0},
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_open_door_failure(self, authenticated_api):
        """Test door opening failure."""
        resp = _mock_response(500, text="error")

        with (
            patch("httpx.AsyncClient.post", return_value=resp),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await authenticated_api.open_door(
                "device_123",
                {"block": 100, "subblock": -1, "number": 0},
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_open_door_incall_success(self, authenticated_api):
        resp = _mock_response(200, text="ok")
        with patch("httpx.AsyncClient.post", return_value=resp) as mock_post:
            result = await authenticated_api.open_door_incall(
                "dev1", room_id="room_123", fcm_token="tok", call_as="dev1"
            )
        assert result is True
        call_args = mock_post.call_args
        body = call_args.kwargs.get("json", {})
        assert body["deviceId"] == "dev1"
        assert body["roomId"] == "room_123"
        assert body["unitId"] == "dev1"

    @pytest.mark.asyncio
    async def test_open_door_incall_failure(self, authenticated_api):
        resp = _mock_response(500, text="error")
        with patch("httpx.AsyncClient.post", return_value=resp):
            result = await authenticated_api.open_door_incall("dev1")
        assert result is False


class TestAutoOn:
    """Test camera preview (auto-on)."""

    @pytest.mark.asyncio
    async def test_auto_on_success(self, authenticated_api):
        """Test successful auto-on request."""
        resp = _mock_response(
            200,
            json={
                "reason": "call_starting",
                "divertService": "blueStream",
                "code": 1.0,
                "description": "Auto on is starting",
                "directedTo": "fcm_token_123",
                "additional_info": {
                    "local": {"address": "00 00 42"},
                    "remote": {"address": "AA F0 00"},
                },
            },
        )

        with patch("httpx.AsyncClient.post", return_value=resp):
            result = await authenticated_api.auto_on("device_123", "fcm_token_123")

        assert result is not None
        assert result.reason == "call_starting"
        assert result.divert_service == "blueStream"
        assert result.description == "Auto on is starting"
        assert result.directed_to == "fcm_token_123"
        assert result.local_address == "00 00 42"
        assert result.remote_address == "AA F0 00"

    @pytest.mark.asyncio
    async def test_auto_on_failure(self, authenticated_api):
        """Test auto-on when server returns error."""
        resp = _mock_response(500, json={"title": "Internal Server Error"})

        with (
            patch("httpx.AsyncClient.post", return_value=resp),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await authenticated_api.auto_on("device_123", "fcm_token_123")

        assert result is None

    @pytest.mark.asyncio
    async def test_change_video_source(self, authenticated_api):
        """Test video source change request."""
        resp = _mock_response(
            200,
            json={
                "reason": "call_starting",
                "divertService": "blueStream",
                "code": 1.0,
                "description": "Change video source",
                "directedTo": "fcm_token_123",
            },
        )

        with patch("httpx.AsyncClient.post", return_value=resp):
            result = await authenticated_api.change_video_source("device_123", "fcm_token_123")

        assert result is not None
        assert result.reason == "call_starting"

    @pytest.mark.asyncio
    async def test_change_video_source_failure(self, authenticated_api):
        """Test video source change failure."""
        resp = _mock_response(500, text="error")

        with (
            patch("httpx.AsyncClient.post", return_value=resp),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await authenticated_api.change_video_source("device_123", "fcm_token_123")

        assert result is None


class TestAppToken:
    """Test FCM token registration."""

    @pytest.mark.asyncio
    async def test_register_token_success(self, authenticated_api):
        """Test successful token registration."""
        resp = _mock_response(200, json={"message": "ok"})

        with patch("httpx.AsyncClient.post", return_value=resp):
            result = await authenticated_api.register_app_token("fcm_token", active=True)

        assert result is True

    @pytest.mark.asyncio
    async def test_register_token_conflict(self, authenticated_api):
        """Test token registration conflict."""
        resp = _mock_response(409, json={"title": "Conflict"})

        with patch("httpx.AsyncClient.post", return_value=resp):
            result = await authenticated_api.register_app_token("fcm_token", active=True)

        assert result is False

    @pytest.mark.asyncio
    async def test_deactivate_token(self, authenticated_api):
        """Test token deactivation."""
        resp = _mock_response(200, json={"message": "ok"})

        with patch("httpx.AsyncClient.post", return_value=resp):
            result = await authenticated_api.register_app_token("fcm_token", active=False)

        assert result is True


class TestDeviceControls:
    """Test DND, F1, call guard, photo caller."""

    @pytest.mark.asyncio
    async def test_get_dnd_enabled(self, authenticated_api):
        resp = _mock_response(200, json={"muted": True})
        with patch("httpx.AsyncClient.get", return_value=resp):
            result = await authenticated_api.get_dnd_status("dev1", "fcm123")
        assert result is True

    @pytest.mark.asyncio
    async def test_get_dnd_disabled(self, authenticated_api):
        resp = _mock_response(200, json={"muted": False})
        with patch("httpx.AsyncClient.get", return_value=resp):
            result = await authenticated_api.get_dnd_status("dev1", "fcm123")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_dnd(self, authenticated_api):
        resp = _mock_response(200, text="ok")
        with patch("httpx.AsyncClient.post", return_value=resp):
            await authenticated_api.set_dnd("dev1", "fcm123", enabled=True)

    @pytest.mark.asyncio
    async def test_press_f1(self, authenticated_api):
        resp = _mock_response(200, text="ok")
        with patch("httpx.AsyncClient.post", return_value=resp):
            await authenticated_api.press_f1("dev1")

    @pytest.mark.asyncio
    async def test_press_f1_failure(self, authenticated_api):
        resp = _mock_response(403, text="forbidden")
        with (
            patch("httpx.AsyncClient.post", return_value=resp),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await authenticated_api.press_f1("dev1")

    @pytest.mark.asyncio
    async def test_call_guard(self, authenticated_api):
        resp = _mock_response(200, text="ok")
        with patch("httpx.AsyncClient.post", return_value=resp):
            await authenticated_api.call_guard("dev1")

    @pytest.mark.asyncio
    async def test_enable_photo_caller(self, authenticated_api):
        resp = _mock_response(200, text="ok")
        with patch("httpx.AsyncClient.put", return_value=resp) as mock_put:
            await authenticated_api.set_photo_caller("dev1", enabled=True)
        call_args = mock_put.call_args
        assert call_args.kwargs["params"]["value"] == "true"

    @pytest.mark.asyncio
    async def test_disable_photo_caller(self, authenticated_api):
        resp = _mock_response(200, text="ok")
        with patch("httpx.AsyncClient.put", return_value=resp) as mock_put:
            await authenticated_api.set_photo_caller("dev1", enabled=False)
        call_args = mock_put.call_args
        assert call_args.kwargs["params"]["value"] == "false"


class TestCallLog:
    """Test call log, photo, and notification ACK."""

    @pytest.mark.asyncio
    async def test_get_call_log_empty(self, authenticated_api):
        """Test empty call log."""
        resp = _mock_response(200, json=[])

        with patch("httpx.AsyncClient.get", return_value=resp):
            entries = await authenticated_api.get_call_log("fcm_token")

        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_get_call_log_error(self, authenticated_api):
        """Test call log fetch error returns empty list."""
        resp = _mock_response(500, text="error")

        with (
            patch("httpx.AsyncClient.get", return_value=resp),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            entries = await authenticated_api.get_call_log("fcm_token")

        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_get_photo_not_found(self, authenticated_api):
        """Test photo retrieval when not found."""
        resp = _mock_response(404, text="not found")

        with patch("httpx.AsyncClient.get", return_value=resp):
            photo = await authenticated_api.get_call_photo("photo_123")

        assert photo is None

    @pytest.mark.asyncio
    async def test_ack_call_notification(self, authenticated_api):
        resp = _mock_response(200, text="ok")
        with patch("httpx.AsyncClient.post", return_value=resp) as mock_post:
            await authenticated_api.ack_notification("msg1", is_call=True)
        call_args = mock_post.call_args
        assert "/callmanager/api/v1/message/ack" in call_args.args[0]

    @pytest.mark.asyncio
    async def test_ack_info_notification(self, authenticated_api):
        resp = _mock_response(200, text="ok")
        with patch("httpx.AsyncClient.post", return_value=resp) as mock_post:
            await authenticated_api.ack_notification("msg1", is_call=False)
        call_args = mock_post.call_args
        assert "/notification/api/v1/message/ack" in call_args.args[0]


class TestOpeningHistory:
    """Test opening history retrieval."""

    @pytest.mark.asyncio
    async def test_get_openings(self, authenticated_api):
        resp = _mock_response(
            200,
            json={
                "openDoorRegistry": [
                    {
                        "id": "abc",
                        "email": "user@test.com",
                        "instant": "2024-01-01T10:00:00Z",
                        "accessName": "G",
                        "accessType": "GENERAL",
                        "guestEmail": None,
                    },
                    {
                        "id": "def",
                        "email": "guest@test.com",
                        "instant": "2024-01-01T11:00:00Z",
                        "accessName": "G",
                        "accessType": "GENERAL",
                        "guestEmail": "guest@test.com",
                    },
                ]
            },
        )
        with patch("httpx.AsyncClient.get", return_value=resp):
            result = await authenticated_api.get_opening_history("dev1")
        assert len(result) == 2
        assert isinstance(result[0], OpeningRecord)
        assert result[0].user == "user@test.com"
        assert result[0].timestamp == "2024-01-01T10:00:00Z"
        assert result[1].guest_email == "guest@test.com"

    @pytest.mark.asyncio
    async def test_get_openings_empty(self, authenticated_api):
        resp = _mock_response(200, json={"openDoorRegistry": []})
        with patch("httpx.AsyncClient.get", return_value=resp):
            result = await authenticated_api.get_opening_history("dev1")
        assert result == []


class TestClientLifecycle:
    """Test HTTP client lifecycle."""

    @pytest.mark.asyncio
    async def test_client_creation(self, api):
        """Test persistent client is created."""
        client = await api._get_client()
        assert client is not None
        assert not client.is_closed

    @pytest.mark.asyncio
    async def test_client_reuse(self, api):
        """Test same client is reused."""
        client1 = await api._get_client()
        client2 = await api._get_client()
        assert client1 is client2

    @pytest.mark.asyncio
    async def test_client_close(self, api):
        """Test client close."""
        await api._get_client()
        await api.close()
        assert api._client is None


class TestFrozenModels:
    def test_device_info_is_frozen(self):
        from custom_components.fermax_blue.api import DeviceInfo

        info = DeviceInfo(
            device_id="dev1",
            connection_state="Connected",
            status="OK",
            family="BLUE",
            device_type="Monitor",
            subtype="",
            unit_number=1,
            photocaller=True,
            streaming_mode="bluestream",
            is_monitor=True,
            wireless_signal=3,
        )
        with pytest.raises(FrozenInstanceError):
            info.status = "Offline"

    def test_pairing_is_frozen(self):
        from custom_components.fermax_blue.api import Pairing

        p = Pairing(device_id="d1", tag="tag", installation_id="inst1")
        with pytest.raises(FrozenInstanceError):
            p.tag = "new_tag"

    def test_opening_record_is_frozen(self):
        from custom_components.fermax_blue.api import OpeningRecord

        record = OpeningRecord(timestamp="2026-01-01", user="u", door="d")
        with pytest.raises(FrozenInstanceError):
            record.user = "other"
