"""Tests for the Fermax Blue coordinator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fermax_blue.api import (
    AccessDoor,
    DeviceInfo,
    FermaxBlueApi,
    Pairing,
)
from custom_components.fermax_blue.coordinator import FermaxBlueCoordinator


@pytest.fixture
def mock_hass():
    """Return a mock HomeAssistant instance."""
    hass = MagicMock()
    hass.async_create_task = MagicMock()
    return hass


@pytest.fixture
def mock_api():
    """Return a mock API."""
    api = AsyncMock(spec=FermaxBlueApi)
    api.get_device_info = AsyncMock(
        return_value=DeviceInfo(
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
    )
    api.get_dnd_status = AsyncMock(return_value=False)
    api.set_dnd = AsyncMock()
    api.press_f1 = AsyncMock()
    api.call_guard = AsyncMock()
    api.set_photo_caller = AsyncMock()
    api.get_opening_history = AsyncMock(return_value=[])
    api.ack_notification = AsyncMock()
    return api


@pytest.fixture
def pairing():
    """Return a test pairing."""
    return Pairing(
        device_id="dev1",
        tag="Home",
        installation_id="inst_1",
        access_doors={
            "GENERAL": AccessDoor(
                name="GENERAL",
                title="Portal",
                access_id={"block": 100, "subblock": -1, "number": 0},
                visible=True,
            ),
        },
    )


@pytest.fixture
def coordinator(mock_hass, mock_api, pairing):
    """Create a coordinator with patched HA internals."""
    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coord = FermaxBlueCoordinator.__new__(FermaxBlueCoordinator)
        coord.api = mock_api
        coord.pairing = pairing
        coord.hass = mock_hass
        coord.device_info = None
        coord.notification_listener = None
        coord._last_photo = None
        coord._last_photo_id = None
        coord._doorbell_ringing = False
        coord._camera_active = False
        coord._last_divert_response = None
        coord._photo_fetch_pending = False
        coord._doorbell_reset_unsub = None
        coord._camera_timeout_unsub = None
        coord._dnd_enabled = None
        coord._last_opening = None
        coord.update_interval = None
    return coord


class TestCoordinatorDnd:
    """Test DND coordination."""

    @pytest.mark.asyncio
    async def test_set_dnd_calls_api(self, coordinator, mock_api):
        coordinator.notification_listener = MagicMock()
        coordinator.notification_listener.fcm_token = "tok"

        await coordinator.set_dnd(True)
        mock_api.set_dnd.assert_called_once_with("dev1", "tok", enabled=True)
        assert coordinator.dnd_enabled is True

    @pytest.mark.asyncio
    async def test_set_dnd_no_listener(self, coordinator, mock_api):
        coordinator.notification_listener = None

        await coordinator.set_dnd(True)
        mock_api.set_dnd.assert_not_called()


class TestCoordinatorF1:
    """Test F1 coordination."""

    @pytest.mark.asyncio
    async def test_press_f1_calls_api(self, coordinator, mock_api):
        await coordinator.press_f1()
        mock_api.press_f1.assert_called_once_with("dev1")


class TestCoordinatorCallGuard:
    """Test call guard coordination."""

    @pytest.mark.asyncio
    async def test_call_guard_calls_api(self, coordinator, mock_api):
        await coordinator.call_guard()
        mock_api.call_guard.assert_called_once_with("dev1")


class TestCoordinatorPhotoCaller:
    """Test photo caller coordination."""

    @pytest.mark.asyncio
    async def test_set_photo_caller_calls_api(self, coordinator, mock_api):
        coordinator.device_info = DeviceInfo(
            device_id="dev1",
            connection_state="Connected",
            status="ACTIVATED",
            family="MONITOR",
            device_type="VEO-XL",
            subtype="WIFI",
            unit_number=42,
            photocaller=False,
            streaming_mode="video_call",
            is_monitor=True,
            wireless_signal=4,
        )

        await coordinator.set_photo_caller(True)
        mock_api.set_photo_caller.assert_called_once_with("dev1", enabled=True)
        assert coordinator.device_info.photocaller is True


class TestCoordinatorScanInterval:
    """Test configurable scan interval."""

    def test_default_interval(self, mock_hass, mock_api, pairing):
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__"
        ) as mock_init:
            FermaxBlueCoordinator(mock_hass, mock_api, pairing)
            call_kwargs = mock_init.call_args
            assert call_kwargs.kwargs["update_interval"].total_seconds() == 300

    def test_custom_interval(self, mock_hass, mock_api, pairing):
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__"
        ) as mock_init:
            FermaxBlueCoordinator(mock_hass, mock_api, pairing, scan_interval=10)
            call_kwargs = mock_init.call_args
            assert call_kwargs.kwargs["update_interval"].total_seconds() == 600
