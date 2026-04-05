"""Test fixtures for Fermax Blue tests."""

from unittest.mock import AsyncMock

import pytest

from custom_components.fermax_blue.api import (
    AccessDoor,
    DeviceInfo,
    DivertResponse,
    FermaxBlueApi,
    Pairing,
)


@pytest.fixture
def mock_api():
    """Return a mocked FermaxBlueApi."""
    api = AsyncMock(spec=FermaxBlueApi)
    api.authenticate = AsyncMock(return_value="fake_token")
    api.is_authenticated = True
    api.get_pairings = AsyncMock(
        return_value=[
            Pairing(
                device_id="test_device_001",
                tag="Test Home",
                installation_id="inst_test",
                access_doors={
                    "GENERAL": AccessDoor(
                        name="GENERAL",
                        title="Portal",
                        access_id={"block": 100, "subblock": -1, "number": 0},
                        visible=True,
                    ),
                    "ZERO": AccessDoor(
                        name="ZERO",
                        title="Door X",
                        access_id={"block": 200, "subblock": -1, "number": 0},
                        visible=False,
                    ),
                },
            )
        ]
    )
    api.get_device_info = AsyncMock(
        return_value=DeviceInfo(
            device_id="test_device_001",
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
    )
    api.open_door = AsyncMock(return_value=True)
    api.auto_on = AsyncMock(
        return_value=DivertResponse(
            reason="call_starting",
            divert_service="blueStream",
            code=1.0,
            description="Auto on is starting",
            directed_to="test_fcm_token",
            local_address="00 00 42",
            remote_address="AA F0 00",
        )
    )
    api.change_video_source = AsyncMock(
        return_value=DivertResponse(
            reason="call_starting",
            divert_service="blueStream",
            code=1.0,
            description="Change video source",
            directed_to="test_fcm_token",
        )
    )
    api.get_call_log = AsyncMock(return_value=[])
    api.get_call_photo = AsyncMock(return_value=None)
    api.register_app_token = AsyncMock(return_value=True)
    api.open_door_incall = AsyncMock(return_value=True)
    api.get_dnd_status = AsyncMock(return_value=False)
    api.set_dnd = AsyncMock()
    api.press_f1 = AsyncMock()
    api.call_guard = AsyncMock()
    api.ack_notification = AsyncMock()
    api.set_photo_caller = AsyncMock()
    api.get_opening_history = AsyncMock(return_value=[])
    return api
