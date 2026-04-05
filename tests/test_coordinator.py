"""Tests for the Fermax Blue coordinator."""

from unittest.mock import AsyncMock, MagicMock

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


class TestCoordinatorDnd:
    """Test DND coordination."""

    @pytest.mark.asyncio
    async def test_set_dnd_calls_api(self, mock_hass, mock_api, pairing):
        coord = FermaxBlueCoordinator(mock_hass, mock_api, pairing)
        coord.notification_listener = MagicMock()
        coord.notification_listener.fcm_token = "tok"

        await coord.set_dnd(True)
        mock_api.set_dnd.assert_called_once_with("dev1", "tok", enabled=True)
        assert coord.dnd_enabled is True

    @pytest.mark.asyncio
    async def test_set_dnd_no_listener(self, mock_hass, mock_api, pairing):
        coord = FermaxBlueCoordinator(mock_hass, mock_api, pairing)
        coord.notification_listener = None

        await coord.set_dnd(True)
        mock_api.set_dnd.assert_not_called()


class TestCoordinatorF1:
    """Test F1 coordination."""

    @pytest.mark.asyncio
    async def test_press_f1_calls_api(self, mock_hass, mock_api, pairing):
        coord = FermaxBlueCoordinator(mock_hass, mock_api, pairing)
        await coord.press_f1()
        mock_api.press_f1.assert_called_once_with("dev1")


class TestCoordinatorCallGuard:
    """Test call guard coordination."""

    @pytest.mark.asyncio
    async def test_call_guard_calls_api(self, mock_hass, mock_api, pairing):
        coord = FermaxBlueCoordinator(mock_hass, mock_api, pairing)
        await coord.call_guard()
        mock_api.call_guard.assert_called_once_with("dev1")


class TestCoordinatorPhotoCaller:
    """Test photo caller coordination."""

    @pytest.mark.asyncio
    async def test_set_photo_caller_calls_api(self, mock_hass, mock_api, pairing):
        coord = FermaxBlueCoordinator(mock_hass, mock_api, pairing)
        coord.device_info = DeviceInfo(
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

        await coord.set_photo_caller(True)
        mock_api.set_photo_caller.assert_called_once_with("dev1", enabled=True)
        assert coord.device_info.photocaller is True


class TestCoordinatorScanInterval:
    """Test configurable scan interval."""

    def test_default_interval(self, mock_hass, mock_api, pairing):
        coord = FermaxBlueCoordinator(mock_hass, mock_api, pairing)
        assert coord.update_interval.total_seconds() == 300  # 5 min

    def test_custom_interval(self, mock_hass, mock_api, pairing):
        coord = FermaxBlueCoordinator(mock_hass, mock_api, pairing, scan_interval=10)
        assert coord.update_interval.total_seconds() == 600  # 10 min
