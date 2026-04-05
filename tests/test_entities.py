"""Tests for entity platforms."""

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.fermax_blue.api import (
    AccessDoor,
    DeviceInfo,
    Pairing,
)
from custom_components.fermax_blue.coordinator import FermaxBlueCoordinator


@pytest.fixture
def mock_coordinator():
    """Return a mock coordinator."""
    coordinator = MagicMock(spec=FermaxBlueCoordinator)
    coordinator.pairing = Pairing(
        device_id="test_dev",
        tag="Test Home",
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
    coordinator.device_info = DeviceInfo(
        device_id="test_dev",
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
    coordinator.data = {
        "connection_state": "Connected",
        "status": "ACTIVATED",
        "wireless_signal": 4,
    }
    coordinator.notification_listener = MagicMock()
    coordinator.notification_listener.fcm_token = "test_fcm"
    coordinator.notification_listener.is_started = True
    coordinator.dnd_enabled = False
    coordinator.last_opening = None
    coordinator.last_call = None
    coordinator.call_log = []
    return coordinator


class TestEntityAvailability:
    """Test entity availability based on device connection."""

    def test_available_when_connected(self, mock_coordinator):
        from custom_components.fermax_blue.entity import FermaxBlueEntity

        entity = FermaxBlueEntity(mock_coordinator)
        assert entity.available is True

    def test_unavailable_when_disconnected(self, mock_coordinator):
        from custom_components.fermax_blue.entity import FermaxBlueEntity

        mock_coordinator.data = {"connection_state": "Disconnected"}
        entity = FermaxBlueEntity(mock_coordinator)
        assert entity.available is False

    def test_unavailable_when_no_data(self, mock_coordinator):
        from custom_components.fermax_blue.entity import FermaxBlueEntity

        mock_coordinator.data = None
        entity = FermaxBlueEntity(mock_coordinator)
        assert entity.available is False


class TestDndSwitch:
    """Test Do Not Disturb switch."""

    def test_dnd_switch_unique_id(self, mock_coordinator):
        from custom_components.fermax_blue.switch import FermaxDndSwitch

        switch = FermaxDndSwitch(mock_coordinator)
        assert switch.unique_id == "test_dev_dnd"

    @pytest.mark.asyncio
    async def test_dnd_turn_on(self, mock_coordinator):
        from custom_components.fermax_blue.switch import FermaxDndSwitch

        mock_coordinator.set_dnd = AsyncMock()
        switch = FermaxDndSwitch(mock_coordinator)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()
        mock_coordinator.set_dnd.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_dnd_turn_off(self, mock_coordinator):
        from custom_components.fermax_blue.switch import FermaxDndSwitch

        mock_coordinator.set_dnd = AsyncMock()
        switch = FermaxDndSwitch(mock_coordinator)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()
        mock_coordinator.set_dnd.assert_called_once_with(False)


class TestPhotoCallerSwitch:
    """Test Photo Caller switch."""

    def test_photo_caller_unique_id(self, mock_coordinator):
        from custom_components.fermax_blue.switch import FermaxPhotoCallerSwitch

        switch = FermaxPhotoCallerSwitch(mock_coordinator)
        assert switch.unique_id == "test_dev_photo_caller"

    @pytest.mark.asyncio
    async def test_photo_caller_turn_on(self, mock_coordinator):
        from custom_components.fermax_blue.switch import FermaxPhotoCallerSwitch

        mock_coordinator.set_photo_caller = AsyncMock()
        switch = FermaxPhotoCallerSwitch(mock_coordinator)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()
        mock_coordinator.set_photo_caller.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_photo_caller_turn_off(self, mock_coordinator):
        from custom_components.fermax_blue.switch import FermaxPhotoCallerSwitch

        mock_coordinator.set_photo_caller = AsyncMock()
        switch = FermaxPhotoCallerSwitch(mock_coordinator)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()
        mock_coordinator.set_photo_caller.assert_called_once_with(False)


class TestF1Button:
    """Test F1 auxiliary button."""

    def test_f1_unique_id(self, mock_coordinator):
        from custom_components.fermax_blue.button import FermaxF1Button

        button = FermaxF1Button(mock_coordinator)
        assert button.unique_id == "test_dev_f1"

    @pytest.mark.asyncio
    async def test_f1_press(self, mock_coordinator):
        from custom_components.fermax_blue.button import FermaxF1Button

        mock_coordinator.press_f1 = AsyncMock()
        button = FermaxF1Button(mock_coordinator)

        await button.async_press()
        mock_coordinator.press_f1.assert_called_once()


class TestCallGuardButton:
    """Test Call Guard button."""

    def test_call_guard_unique_id(self, mock_coordinator):
        from custom_components.fermax_blue.button import FermaxCallGuardButton

        button = FermaxCallGuardButton(mock_coordinator)
        assert button.unique_id == "test_dev_call_guard"

    @pytest.mark.asyncio
    async def test_call_guard_press(self, mock_coordinator):
        from custom_components.fermax_blue.button import FermaxCallGuardButton

        mock_coordinator.call_guard = AsyncMock()
        button = FermaxCallGuardButton(mock_coordinator)

        await button.async_press()
        mock_coordinator.call_guard.assert_called_once()


class TestDoorbellEvent:
    """Test doorbell event entity."""

    def test_doorbell_event_types(self, mock_coordinator):
        from custom_components.fermax_blue.event import FermaxDoorbellEvent

        event = FermaxDoorbellEvent(mock_coordinator)
        assert "ring" in event.event_types
        assert event.unique_id == "test_dev_doorbell_event"


class TestLastOpeningSensor:
    """Test last door opening sensor."""

    def test_last_opening_unique_id(self, mock_coordinator):
        from custom_components.fermax_blue.sensor import FermaxLastOpeningSensor

        sensor = FermaxLastOpeningSensor(mock_coordinator)
        assert sensor.unique_id == "test_dev_last_opening"

    def test_last_opening_none(self, mock_coordinator):
        from custom_components.fermax_blue.sensor import FermaxLastOpeningSensor

        mock_coordinator.last_opening = None
        sensor = FermaxLastOpeningSensor(mock_coordinator)
        assert sensor.native_value is None

    def test_last_opening_value(self, mock_coordinator):
        from custom_components.fermax_blue.api import OpeningRecord
        from custom_components.fermax_blue.sensor import FermaxLastOpeningSensor

        mock_coordinator.last_opening = OpeningRecord(
            timestamp="2026-04-05T10:30:00Z",
            user="John",
            door="Portal",
        )
        sensor = FermaxLastOpeningSensor(mock_coordinator)
        assert sensor.native_value == "2026-04-05T10:30:00Z"

    def test_last_opening_extra_attrs(self, mock_coordinator):
        from custom_components.fermax_blue.api import OpeningRecord
        from custom_components.fermax_blue.sensor import FermaxLastOpeningSensor

        mock_coordinator.last_opening = OpeningRecord(
            timestamp="2026-04-05T10:30:00Z",
            user="John",
            door="Portal",
            guest_email="guest@test.com",
        )
        sensor = FermaxLastOpeningSensor(mock_coordinator)
        attrs = sensor.extra_state_attributes
        assert attrs["user"] == "John"
        assert attrs["door"] == "Portal"
        assert attrs["guest_email"] == "guest@test.com"


class TestLastCallSensor:
    """Test last call sensor."""

    def test_last_call_unique_id(self, mock_coordinator):
        from custom_components.fermax_blue.sensor import FermaxLastCallSensor

        mock_coordinator.last_call = None
        mock_coordinator.call_log = []
        sensor = FermaxLastCallSensor(mock_coordinator)
        assert sensor.unique_id == "test_dev_last_call"

    def test_last_call_none(self, mock_coordinator):
        from custom_components.fermax_blue.sensor import FermaxLastCallSensor

        mock_coordinator.last_call = None
        mock_coordinator.call_log = []
        sensor = FermaxLastCallSensor(mock_coordinator)
        assert sensor.native_value is None

    def test_last_call_value(self, mock_coordinator):
        from datetime import datetime

        from custom_components.fermax_blue.api import CallLogEntry
        from custom_components.fermax_blue.sensor import FermaxLastCallSensor

        call = CallLogEntry(
            call_id="abc123",
            device_id="test_dev",
            call_date=datetime(2026, 4, 5, 10, 30, tzinfo=UTC),
            answered=False,
        )
        mock_coordinator.last_call = call
        mock_coordinator.call_log = [call]
        sensor = FermaxLastCallSensor(mock_coordinator)
        assert "2026-04-05" in sensor.native_value
        attrs = sensor.extra_state_attributes
        assert attrs["call_id"] == "abc123"
        assert attrs["answered"] is False
        assert attrs["recent_calls"] == 1
