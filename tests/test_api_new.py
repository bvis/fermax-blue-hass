"""Tests for new API methods and retry logic."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from custom_components.fermax_blue.api import FermaxBlueApi, OpeningRecord


@pytest.fixture
def api():
    return FermaxBlueApi("test@example.com", "testpass123")


@pytest.fixture
def authenticated_api(api):
    api._access_token = "valid_token"
    api._token_expires_at = 9999999999
    return api


def _mock_response(status_code, **kwargs):
    return httpx.Response(
        status_code,
        request=httpx.Request("GET", "https://test.com"),
        **kwargs,
    )


class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_retry_on_500(self, authenticated_api):
        fail_resp = _mock_response(500, text="error")
        ok_resp = _mock_response(
            200,
            json={
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
            },
        )
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
        ok_resp = _mock_response(
            200,
            json={
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
            },
        )
        with (
            patch(
                "httpx.AsyncClient.get",
                side_effect=[httpx.ConnectError("fail"), ok_resp],
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            info = await authenticated_api.get_device_info("dev1")
        assert info.device_id == "dev1"


class TestDndApi:
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


class TestF1Api:
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


class TestCallGuardApi:
    @pytest.mark.asyncio
    async def test_call_guard(self, authenticated_api):
        resp = _mock_response(200, text="ok")
        with patch("httpx.AsyncClient.post", return_value=resp):
            await authenticated_api.call_guard("dev1")


class TestAckApi:
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


class TestPhotoCallerApi:
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


class TestOpeningsApi:
    @pytest.mark.asyncio
    async def test_get_openings(self, authenticated_api):
        resp = _mock_response(
            200,
            json={
                "entries": [
                    {
                        "timestamp": "2024-01-01T10:00:00",
                        "user": "John",
                        "door": "Main",
                        "guestEmail": None,
                    },
                    {
                        "timestamp": "2024-01-01T11:00:00",
                        "user": "Jane",
                        "door": "Side",
                        "guestEmail": "guest@test.com",
                    },
                ]
            },
        )
        with patch("httpx.AsyncClient.get", return_value=resp):
            result = await authenticated_api.get_opening_history("dev1", "user1")
        assert len(result) == 2
        assert isinstance(result[0], OpeningRecord)
        assert result[0].user == "John"
        assert result[1].guest_email == "guest@test.com"

    @pytest.mark.asyncio
    async def test_get_openings_empty(self, authenticated_api):
        resp = _mock_response(200, json={"entries": []})
        with patch("httpx.AsyncClient.get", return_value=resp):
            result = await authenticated_api.get_opening_history("dev1", "user1")
        assert result == []
