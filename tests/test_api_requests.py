"""What each API call puts on the wire, and what it reads from the answer.

The client talks to a fake transport that records every request, so the URL,
query, headers and body of each call are checked, not just its return value.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import httpx

from custom_components.fermax_blue.api import (
    AccessDoor,
    CallLogEntry,
    DeviceInfo,
    DivertResponse,
    FermaxBlueApi,
    OpeningRecord,
    Pairing,
)
from custom_components.fermax_blue.const import APP_HEADERS

AUTH_URL = "https://oauth.example.com/oauth/token"
BASE_URL = "https://api.example.com"
ACCESS_ID = {"block": 100, "subblock": -1, "number": 0}


class _Wire:
    """A FermaxBlueApi on a transport that answers from a script and keeps the requests."""

    def __init__(self, *answers: httpx.Response, authenticated: bool = True) -> None:
        self.requests: list[httpx.Request] = []
        self._answers = list(answers)
        self.api = FermaxBlueApi(
            "user+tag@example.com",
            "p@ss w&d=1",
            httpx.AsyncClient(transport=httpx.MockTransport(self._handle)),
            auth_url=AUTH_URL,
            base_url=BASE_URL,
            auth_basic="Basic Y2xpZW50OnNlY3JldA==",
        )
        if authenticated:
            self.api._access_token = "tok-1"
            self.api._token_expires_at = 9999999999

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._answers.pop(0) if self._answers else httpx.Response(200, json={})

    @property
    def sent(self) -> httpx.Request:
        (request,) = self.requests
        return request

    def assert_call(
        self, method: str, path: str, *, query: dict[str, str] | None = None, body: Any = None
    ) -> None:
        request = self.sent
        assert request.method == method
        assert str(request.url.copy_with(query=None)) == f"{BASE_URL}{path}"
        assert dict(request.url.params) == (query or {})
        assert (json.loads(request.content) if request.content else None) == body


def _json(data: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=data)


async def test_login_sends_the_escaped_credentials_as_a_form() -> None:
    wire = _Wire(_json({"access_token": "tok-9", "expires_in": 600}), authenticated=False)
    with patch("custom_components.fermax_blue.api.time.time", return_value=1000.0):
        assert await wire.api.authenticate() == "tok-9"
    request = wire.sent
    assert request.method == "POST"
    assert str(request.url) == AUTH_URL
    assert request.content == (
        b"grant_type=password&password=p%40ss%20w%26d%3D1&username=user%2Btag%40example.com"
    )
    assert request.headers["authorization"] == "Basic Y2xpZW50OnNlY3JldA=="
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    for name, value in APP_HEADERS.items():
        assert request.headers[name] == value
    # Renewed a minute before the platform's own deadline.
    assert wire.api._token_expires_at == 1000.0 + 600 - 60


async def test_login_assumes_an_hour_when_the_answer_gives_no_lifetime() -> None:
    wire = _Wire(_json({"access_token": "tok-9"}), authenticated=False)
    with patch("custom_components.fermax_blue.api.time.time", return_value=1000.0):
        await wire.api.authenticate()
    assert wire.api._token_expires_at == 1000.0 + 3600 - 60


async def test_api_calls_carry_the_token_and_the_app_headers() -> None:
    wire = _Wire(_json([]))
    await wire.api.get_pairings()
    request = wire.sent
    assert request.headers["authorization"] == "Bearer tok-1"
    assert request.headers["content-type"] == "application/json"
    for name, value in APP_HEADERS.items():
        assert request.headers[name] == value


async def test_pairings_are_read_whole() -> None:
    panel_door = {"block": 1, "subblock": 2, "number": 3}
    wire = _Wire(
        _json(
            [
                {
                    "deviceId": "dev1",
                    "tag": "Home",
                    "installationId": "inst1",
                    "master": True,
                    "accessDoorMap": {
                        "GENERAL": {"title": "Portal", "accessId": ACCESS_ID, "visible": True},
                        "ZERO": {"accessId": {"block": 200, "subblock": -1, "number": 0}},
                    },
                    "panelAccessDoors": [
                        {"deviceId": "panel1", "doorId": panel_door, "title": "Garage"},
                        {"deviceId": "panel2", "doorId": panel_door, "isVisible": True},
                    ],
                },
                {"deviceId": "dev2"},
            ]
        )
    )
    pairings = await wire.api.get_pairings()
    wire.assert_call("GET", "/pairing/api/v4/pairings/me")
    assert pairings == [
        Pairing(
            device_id="dev1",
            tag="Home",
            installation_id="inst1",
            master=True,
            access_doors={
                "GENERAL": AccessDoor("GENERAL", "Portal", ACCESS_ID, True),
                "ZERO": AccessDoor(
                    "ZERO", "ZERO", {"block": 200, "subblock": -1, "number": 0}, False
                ),
                "panel1_1_2_3": AccessDoor("panel1_1_2_3", "Garage", panel_door, False, "panel1"),
                "panel2_1_2_3": AccessDoor(
                    "panel2_1_2_3", "panel2_1_2_3", panel_door, True, "panel2"
                ),
            },
        ),
        Pairing(device_id="dev2", tag="", installation_id="", access_doors={}, master=False),
    ]


async def test_device_info_is_read_whole() -> None:
    wire = _Wire(
        _json(
            {
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
        )
    )
    info = await wire.api.get_device_info("dev1")
    wire.assert_call("GET", "/deviceaction/api/v1/device/dev1")
    assert info == DeviceInfo(
        "dev1", "Connected", "ACTIVATED", "MONITOR", "VEO-XL", "WIFI", 42, True, "video_call",
        True, 4,
    )  # fmt: skip


async def test_device_info_fills_in_what_the_answer_leaves_out() -> None:
    wire = _Wire(_json({"deviceId": "dev1"}))
    assert await wire.api.get_device_info("dev1") == DeviceInfo(
        "dev1", "Unknown", "Unknown", "Unknown", "Unknown", "", 0, False, "", False, 0
    )


async def test_open_door_posts_the_door_to_the_device() -> None:
    wire = _Wire()
    assert await wire.api.open_door("dev1", ACCESS_ID) is True
    wire.assert_call("POST", "/deviceaction/api/v1/device/dev1/directed-opendoor", body=ACCESS_ID)


async def test_open_door_on_a_panel_names_the_unit() -> None:
    wire = _Wire()
    assert await wire.api.open_door("dev1", ACCESS_ID, panel_id="panel1") is True
    wire.assert_call(
        "POST",
        "/deviceaction/api/v1/device/panel1/directed-opendoor",
        query={"unitId": "dev1"},
        body=ACCESS_ID,
    )


_IN_CALL = {"deviceId": "dev1", "roomId": "room1", "appTokenId": "fcm1", "unitId": "unit1"}


async def test_in_call_actions_go_to_their_own_endpoints() -> None:
    wire = _Wire()
    assert await wire.api.open_door_incall("dev1", "room1", "fcm1", "unit1") is True
    wire.assert_call("POST", "/deviceaction/api/v1/device/incall/opendoor", body=_IN_CALL)

    wire = _Wire()
    assert await wire.api.press_f1_incall("dev1", "room1", "fcm1", "unit1") is True
    wire.assert_call("POST", "/deviceaction/api/v1/device/incall/f1", body=_IN_CALL)

    divert = {
        "reason": "r", "divertService": "blue", "code": 1.0, "description": "d", "directedTo": "t"
    }  # fmt: skip
    wire = _Wire(_json(divert))
    answer = await wire.api.change_video_source_incall("dev1", "room1", "fcm1", "unit1")
    wire.assert_call("POST", "/deviceaction/api/v1/device/incall/changevideosource", body=_IN_CALL)
    assert answer == DivertResponse("r", "blue", 1.0, "d", "t")


async def test_auto_on_asks_for_the_stream_and_reads_the_answer() -> None:
    wire = _Wire(
        _json(
            {
                "reason": "call_starting",
                "divertService": "blue",
                "code": 1.0,
                "description": "Auto on",
                "directedTo": "fcm1",
                "additional_info": {
                    "local": {"address": "10.0.0.1"},
                    "remote": {"address": "1.2.3.4"},
                },
            }
        )
    )
    answer = await wire.api.auto_on("dev1", "fcm1", call_as="unit1")
    wire.assert_call(
        "POST",
        "/deviceaction/api/v2/device/dev1/autoon",
        body={"directedToBluestream": "fcm1", "directedToSippo": None, "callAs": "unit1"},
    )
    assert answer == DivertResponse(
        "call_starting", "blue", 1.0, "Auto on", "fcm1", "10.0.0.1", "1.2.3.4"
    )


async def test_auto_on_reads_an_empty_answer_as_blanks() -> None:
    wire = _Wire(_json({}))
    assert await wire.api.auto_on("dev1", "fcm1") == DivertResponse("", "", 0, "", "", "", "")
    assert json.loads(wire.sent.content)["callAs"] is None


async def test_change_video_source_request_and_answer() -> None:
    wire = _Wire(_json({"reason": "r", "code": 2.0}))
    answer = await wire.api.change_video_source("dev1", "fcm1")
    wire.assert_call(
        "POST",
        "/deviceaction/api/v2/device/dev1/changevideosource",
        body={"directedToBluestream": "fcm1", "directedToSippo": None, "callAs": None},
    )
    assert answer == DivertResponse("r", "", 2.0, "", "")


async def test_call_log_is_asked_for_this_app_and_read_whole() -> None:
    wire = _Wire(
        _json(
            [
                {
                    "id": "call1",
                    "deviceId": "dev1",
                    "callDate": "2026-01-02T03:04:05+00:00",
                    "photoId": "photo1",
                    "answered": True,
                },
                {"callDate": "2026-01-03T00:00:00+00:00"},
            ]
        )
    )
    entries = await wire.api.get_call_log("fcm1")
    wire.assert_call(
        "GET",
        "/callmanager/api/v1/callregistry/participant",
        query={"appToken": "fcm1", "callRegistryType": "all"},
    )
    assert entries == [
        CallLogEntry("call1", "dev1", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "photo1", True),
        CallLogEntry("", "", datetime(2026, 1, 3, tzinfo=UTC), None, False),
    ]


async def test_call_photo_is_fetched_by_id_and_decoded() -> None:
    wire = _Wire(_json({"image": {"data": base64.b64encode(b"\xff\xd8jpeg").decode()}}))
    assert await wire.api.get_call_photo("photo1") == b"\xff\xd8jpeg"
    wire.assert_call("GET", "/callmanager/api/v1/photocall", query={"photoId": "photo1"})


async def test_call_photo_without_image_data_is_none() -> None:
    assert await _Wire(_json({"image": {}})).api.get_call_photo("photo1") is None
    assert await _Wire(_json({})).api.get_call_photo("photo1") is None


async def test_register_app_token_sends_the_token_and_its_state() -> None:
    for active in (True, False):
        wire = _Wire()
        assert await wire.api.register_app_token("fcm1", active=active) is True
        request = wire.sent
        assert request.method == "POST"
        assert request.url.path == "/notification/api/v1/apptoken"
        body = json.loads(request.content)
        assert (body["token"], body["active"], body["os"]) == ("fcm1", active, "Android")


async def test_register_app_token_tries_once_more_on_a_conflict() -> None:
    wire = _Wire(_json({}, 409), _json({}))
    assert await wire.api.register_app_token("fcm1") is True
    assert [r.url.path for r in wire.requests] == ["/notification/api/v1/apptoken"] * 2
    assert wire.requests[0].content == wire.requests[1].content

    wire = _Wire(_json({}, 409), _json({}, 409))
    assert await wire.api.register_app_token("fcm1") is False
    assert len(wire.requests) == 2

    wire = _Wire(_json({}, 400))
    assert await wire.api.register_app_token("fcm1") is False
    assert len(wire.requests) == 1


async def test_do_not_disturb_is_read_and_set_for_this_app() -> None:
    for answer, expected in ((True, True), (False, False), ({"muted": True}, True), ({}, False)):
        wire = _Wire(_json(answer))
        assert await wire.api.get_dnd_status("dev1", "fcm1") is expected
        wire.assert_call(
            "GET", "/notification/api/v1/mutedevice/me", query={"deviceId": "dev1", "token": "fcm1"}
        )

    wire = _Wire()
    await wire.api.set_dnd("dev1", "fcm1", enabled=True)
    wire.assert_call(
        "POST",
        "/notification/api/v1/mutedevice/me",
        body={"deviceId": "dev1", "token": "fcm1", "muted": True},
    )


async def test_device_buttons_and_settings_hit_their_endpoints() -> None:
    wire = _Wire()
    await wire.api.press_f1("dev1")
    assert (wire.sent.method, wire.sent.url.path) == ("POST", "/deviceaction/api/v1/device/dev1/f1")

    wire = _Wire()
    await wire.api.call_guard("dev1")
    assert (wire.sent.method, wire.sent.url.path) == (
        "POST",
        "/deviceaction/api/v1/device/dev1/callguard",
    )

    for enabled, value in ((True, "true"), (False, "false")):
        wire = _Wire()
        await wire.api.set_photo_caller("dev1", enabled=enabled)
        assert wire.sent.method == "PUT"
        assert wire.sent.url.path == "/deviceaction/api/v1/dev1/photocaller"
        assert dict(wire.sent.url.params) == {"value": value}


async def test_notifications_are_acknowledged_on_the_matching_service() -> None:
    body = {"attended": True, "fcmMessageId": "msg1"}
    wire = _Wire()
    await wire.api.ack_notification("msg1", is_call=True)
    wire.assert_call("POST", "/callmanager/api/v1/message/ack", body=body)

    wire = _Wire()
    await wire.api.ack_notification("msg1", is_call=False)
    wire.assert_call("POST", "/notification/api/v1/message/ack", body=body)


async def test_opening_history_request_and_records() -> None:
    wire = _Wire(
        _json(
            {
                "openDoorRegistry": [
                    {
                        "instant": "2026-01-02T03:04:05Z",
                        "email": "a@example.com",
                        "accessName": "Portal",
                        "accessType": "GENERAL",
                        "guestEmail": "g@example.com",
                    },
                    {"accessType": "ZERO"},
                    {},
                ]
            }
        )
    )
    records = await wire.api.get_opening_history("dev1", master=True)
    wire.assert_call(
        "GET",
        "/rexistro/api/v1/opendoorregistry",
        query={"deviceId": "dev1", "userId": "user+tag@example.com"},
    )
    assert records == [
        OpeningRecord("2026-01-02T03:04:05Z", "a@example.com", "Portal", "g@example.com"),
        OpeningRecord("", "", "ZERO", None),
        OpeningRecord("", "", "", None),
    ]
