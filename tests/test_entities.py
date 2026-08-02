"""Tests for entity platforms."""

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestRingPreviewSwitch:
    """Test the ring preview switch and its restore behavior."""

    def test_ring_preview_unique_id(self, mock_coordinator):
        from custom_components.fermax_blue.switch import FermaxRingPreviewSwitch

        switch = FermaxRingPreviewSwitch(mock_coordinator)
        assert switch.unique_id == "test_dev_ring_preview"

    @pytest.mark.asyncio
    async def test_ring_preview_turn_on_off(self, mock_coordinator):
        from custom_components.fermax_blue.switch import FermaxRingPreviewSwitch

        switch = FermaxRingPreviewSwitch(mock_coordinator)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()
        assert mock_coordinator.ring_preview is True
        await switch.async_turn_off()
        assert mock_coordinator.ring_preview is False

    @pytest.mark.asyncio
    async def test_ring_preview_restores_last_state(self, mock_coordinator):
        from homeassistant.core import State

        from custom_components.fermax_blue.switch import FermaxRingPreviewSwitch

        switch = FermaxRingPreviewSwitch(mock_coordinator)
        switch.async_get_last_state = AsyncMock(return_value=State("switch.t", "on"))

        with patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            AsyncMock(),
        ):
            await switch.async_added_to_hass()

        assert mock_coordinator.ring_preview is True


class TestRestoredControls:
    """Stream duration and call mode keep their prior state across restarts."""

    @pytest.mark.asyncio
    async def test_stream_duration_restored(self, mock_coordinator):
        from custom_components.fermax_blue.number import FermaxStreamDurationNumber

        number = FermaxStreamDurationNumber(mock_coordinator)
        number.async_get_last_number_data = AsyncMock(return_value=MagicMock(native_value=60.0))

        with patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            AsyncMock(),
        ):
            await number.async_added_to_hass()

        assert mock_coordinator.stream_duration == 60

    @pytest.mark.asyncio
    async def test_call_mode_restored(self, mock_coordinator):
        from homeassistant.core import State

        from custom_components.fermax_blue.select import FermaxCallModeSelect

        select = FermaxCallModeSelect(mock_coordinator)
        select.async_get_last_state = AsyncMock(return_value=State("select.t", "record"))

        with patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            AsyncMock(),
        ):
            await select.async_added_to_hass()

        assert mock_coordinator.call_mode == "record"

    @pytest.mark.asyncio
    async def test_call_mode_ignores_invalid_state(self, mock_coordinator):
        from homeassistant.core import State

        from custom_components.fermax_blue.select import FermaxCallModeSelect

        mock_coordinator.call_mode = "notify_only"
        select = FermaxCallModeSelect(mock_coordinator)
        select.async_get_last_state = AsyncMock(return_value=State("select.t", "unavailable"))

        with patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            AsyncMock(),
        ):
            await select.async_added_to_hass()

        assert mock_coordinator.call_mode == "notify_only"


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


class TestEventAvailabilityDecoupledFromConnection:
    """Event entities must not flap to unavailable on transient disconnects.

    When a coordinator-bound entity goes unavailable and then recovers, an
    EventEntity restores its last event (e.g. ``ring``); HA fires state
    triggers on that recovery, producing a phantom doorbell ring on HA restart
    or a brief intercom disconnect (no FCM involved). Decoupling event
    availability from ``connection_state`` prevents the flap.
    """

    def test_doorbell_event_available_when_disconnected(self, mock_coordinator):
        from custom_components.fermax_blue.event import FermaxDoorbellEvent

        mock_coordinator.data = {"connection_state": "Disconnected"}
        assert FermaxDoorbellEvent(mock_coordinator).available is True

    def test_door_opened_event_available_when_disconnected(self, mock_coordinator):
        from custom_components.fermax_blue.event import FermaxDoorOpenedEvent

        mock_coordinator.data = {"connection_state": "Disconnected"}
        assert FermaxDoorOpenedEvent(mock_coordinator).available is True

    def test_camera_on_event_available_when_disconnected(self, mock_coordinator):
        from custom_components.fermax_blue.event import FermaxCameraOnEvent

        mock_coordinator.data = {"connection_state": "Disconnected"}
        assert FermaxCameraOnEvent(mock_coordinator).available is True

    def test_doorbell_event_available_with_no_data(self, mock_coordinator):
        from custom_components.fermax_blue.event import FermaxDoorbellEvent

        mock_coordinator.data = None
        assert FermaxDoorbellEvent(mock_coordinator).available is True

    def test_non_event_entity_still_tracks_connection(self, mock_coordinator):
        """Regression guard: only event entities are decoupled, not the base."""
        from custom_components.fermax_blue.entity import FermaxBlueEntity

        mock_coordinator.data = {"connection_state": "Disconnected"}
        assert FermaxBlueEntity(mock_coordinator).available is False


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

    def test_last_opening_extra_attrs_redacts_emails(self, mock_coordinator):
        from custom_components.fermax_blue.api import OpeningRecord
        from custom_components.fermax_blue.sensor import FermaxLastOpeningSensor

        mock_coordinator.last_opening = OpeningRecord(
            timestamp="2026-04-05T10:30:00Z",
            user="john.doe@example.com",
            door="Portal",
            guest_email="guest@test.com",
        )
        sensor = FermaxLastOpeningSensor(mock_coordinator)
        attrs = sensor.extra_state_attributes
        assert attrs["user"] == "j***e@e***.com"
        assert attrs["door"] == "Portal"
        assert attrs["guest_email"] == "g***t@t***.com"

    def test_last_opening_extra_attrs_non_email_user(self, mock_coordinator):
        from custom_components.fermax_blue.api import OpeningRecord
        from custom_components.fermax_blue.sensor import FermaxLastOpeningSensor

        mock_coordinator.last_opening = OpeningRecord(
            timestamp="2026-04-05T10:30:00Z",
            user="John",
            door="Portal",
        )
        sensor = FermaxLastOpeningSensor(mock_coordinator)
        attrs = sensor.extra_state_attributes
        assert attrs["user"] == "John"
        assert attrs["guest_email"] is None


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


class TestSensorDescriptors:
    """Test descriptor-based sensor configuration."""

    def test_all_sensors_created(self, mock_coordinator):
        from custom_components.fermax_blue.sensor import SENSOR_TYPES

        assert "wifi_signal" in SENSOR_TYPES
        assert "device_status" in SENSOR_TYPES
        assert "last_opening" in SENSOR_TYPES
        assert "last_call" in SENSOR_TYPES

    def test_wifi_signal_descriptor(self):
        from custom_components.fermax_blue.sensor import SENSOR_TYPES

        desc = SENSOR_TYPES["wifi_signal"]
        assert desc.icon is None  # Icon provided by icons.json
        assert desc.state_class is not None

    def test_binary_sensor_descriptors(self, mock_coordinator):
        from custom_components.fermax_blue.binary_sensor import BINARY_SENSOR_TYPES

        assert "connection" in BINARY_SENSOR_TYPES
        desc = BINARY_SENSOR_TYPES["connection"]
        assert desc.device_class is not None


class TestOptimisticSwitches:
    """Test optimistic state updates for DnD and PhotoCaller switches."""

    @pytest.mark.asyncio
    async def test_dnd_switch_optimistic_on(self, mock_coordinator):
        from custom_components.fermax_blue.switch import FermaxDndSwitch

        mock_coordinator.set_dnd = AsyncMock()
        switch = FermaxDndSwitch(mock_coordinator)
        mock_coordinator.dnd_enabled = False
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()
        mock_coordinator.set_dnd.assert_awaited_once_with(True)
        # After the call completes optimistic state is reset; coordinator value used
        assert switch.is_on is False

    @pytest.mark.asyncio
    async def test_dnd_switch_optimistic_off(self, mock_coordinator):
        from custom_components.fermax_blue.switch import FermaxDndSwitch

        mock_coordinator.set_dnd = AsyncMock()
        switch = FermaxDndSwitch(mock_coordinator)
        mock_coordinator.dnd_enabled = True
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()
        mock_coordinator.set_dnd.assert_awaited_once_with(False)
        assert switch.is_on is True

    @pytest.mark.asyncio
    async def test_photo_caller_switch_optimistic(self, mock_coordinator):
        from custom_components.fermax_blue.switch import FermaxPhotoCallerSwitch

        mock_coordinator.set_photo_caller = AsyncMock()
        switch = FermaxPhotoCallerSwitch(mock_coordinator)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()
        mock_coordinator.set_photo_caller.assert_awaited_once_with(True)
        # After completion, optimistic state is cleared; falls back to coordinator
        assert switch.is_on is True  # device_info.photocaller = True in fixture


class TestCameraStreamingDepsGuard:
    """Camera turn_on must not request auto-on when live-video deps are missing."""

    def _make_camera(self, mock_coordinator):
        from custom_components.fermax_blue.camera import FermaxCamera

        return FermaxCamera(mock_coordinator)

    @pytest.mark.asyncio
    async def test_turn_on_skipped_without_deps(self, mock_coordinator):
        camera = self._make_camera(mock_coordinator)

        with patch(
            "custom_components.fermax_blue.camera.streaming_deps_available",
            return_value=False,
        ):
            await camera.async_turn_on()

        mock_coordinator.start_camera_preview.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turn_on_starts_preview_with_deps(self, mock_coordinator):
        camera = self._make_camera(mock_coordinator)
        mock_coordinator.start_camera_preview = AsyncMock(return_value=None)

        with patch(
            "custom_components.fermax_blue.camera.streaming_deps_available",
            return_value=True,
        ):
            await camera.async_turn_on()

        mock_coordinator.start_camera_preview.assert_awaited_once()


class TestDoorLock:
    """The lock platform is what actually opens doors."""

    def _make_lock(self, mock_coordinator):
        from custom_components.fermax_blue.lock import FermaxDoorLock

        return FermaxDoorLock(mock_coordinator, "GENERAL", "Portal")

    def test_unique_id_and_name(self, mock_coordinator):
        lock = self._make_lock(mock_coordinator)

        assert lock.unique_id == "test_dev_GENERAL_lock"
        assert lock.name == "Portal"

    def test_falls_back_to_door_name_without_title(self, mock_coordinator):
        from custom_components.fermax_blue.lock import FermaxDoorLock

        lock = FermaxDoorLock(mock_coordinator, "GENERAL", "")
        assert lock.name == "GENERAL"

    def test_starts_locked(self, mock_coordinator):
        assert self._make_lock(mock_coordinator).is_locked is True

    @pytest.mark.asyncio
    async def test_unlock_opens_the_door_and_reports_unlocked(self, mock_coordinator):
        mock_coordinator.open_door = AsyncMock(return_value=True)
        lock = self._make_lock(mock_coordinator)
        lock.hass = MagicMock()
        lock.async_write_ha_state = MagicMock()

        with patch("custom_components.fermax_blue.lock.async_call_later"):
            await lock.async_unlock()

        mock_coordinator.open_door.assert_awaited_once_with("GENERAL")
        assert lock.is_locked is False

    @pytest.mark.asyncio
    async def test_stays_locked_when_the_api_call_fails(self, mock_coordinator):
        """A failed open must not tell the user the door is open."""
        mock_coordinator.open_door = AsyncMock(return_value=False)
        lock = self._make_lock(mock_coordinator)
        lock.hass = MagicMock()
        lock.async_write_ha_state = MagicMock()

        with patch("custom_components.fermax_blue.lock.async_call_later") as call_later:
            await lock.async_unlock()

        assert lock.is_locked is True
        call_later.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_relocks_after_the_timer_fires(self, mock_coordinator):
        from custom_components.fermax_blue.lock import AUTO_LOCK_SECONDS

        mock_coordinator.open_door = AsyncMock(return_value=True)
        lock = self._make_lock(mock_coordinator)
        lock.hass = MagicMock()
        lock.async_write_ha_state = MagicMock()

        with patch("custom_components.fermax_blue.lock.async_call_later") as call_later:
            await lock.async_unlock()
            assert call_later.call_args[0][1] == AUTO_LOCK_SECONDS
            auto_lock = call_later.call_args[0][2]

        auto_lock(None)

        assert lock.is_locked is True

    @pytest.mark.asyncio
    async def test_second_unlock_cancels_the_pending_relock(self, mock_coordinator):
        """Otherwise the first timer relocks the door mid-way through the second opening."""
        mock_coordinator.open_door = AsyncMock(return_value=True)
        lock = self._make_lock(mock_coordinator)
        lock.hass = MagicMock()
        lock.async_write_ha_state = MagicMock()
        first_unsub = MagicMock()

        with patch(
            "custom_components.fermax_blue.lock.async_call_later",
            side_effect=[first_unsub, MagicMock()],
        ):
            await lock.async_unlock()
            await lock.async_unlock()

        first_unsub.assert_called_once()
        assert lock.is_locked is False

    @pytest.mark.asyncio
    async def test_lock_is_a_local_no_op(self, mock_coordinator):
        """Doors auto-lock physically; locking must not call the API."""
        mock_coordinator.open_door = AsyncMock(return_value=True)
        lock = self._make_lock(mock_coordinator)
        lock.hass = MagicMock()
        lock.async_write_ha_state = MagicMock()

        with patch("custom_components.fermax_blue.lock.async_call_later"):
            await lock.async_unlock()
        await lock.async_lock()

        assert lock.is_locked is True
        mock_coordinator.open_door.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_setup_entry_creates_one_lock_per_door(self, mock_coordinator):
        from custom_components.fermax_blue.api import AccessDoor
        from custom_components.fermax_blue.const import DOMAIN
        from custom_components.fermax_blue.lock import async_setup_entry

        mock_coordinator.pairing.access_doors["ZAGUAN"] = AccessDoor(
            name="ZAGUAN",
            title="Zaguan",
            access_id={"block": 100, "subblock": -1, "number": 1},
            visible=False,
        )
        entry = MagicMock()
        entry.entry_id = "entry_1"
        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_1": [mock_coordinator]}}
        added = []

        await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

        # Doors are created regardless of the unreliable `visible` flag
        assert sorted(lock.unique_id for lock in added) == [
            "test_dev_GENERAL_lock",
            "test_dev_ZAGUAN_lock",
        ]


class TestConnectionBinarySensor:
    """Connectivity sensor must report unknown, not offline, when data is missing."""

    def _make_sensor(self, mock_coordinator):
        from custom_components.fermax_blue.binary_sensor import FermaxConnectionSensor

        return FermaxConnectionSensor(mock_coordinator)

    def test_unique_id_and_device_class(self, mock_coordinator):
        from homeassistant.components.binary_sensor import BinarySensorDeviceClass

        sensor = self._make_sensor(mock_coordinator)

        assert sensor.unique_id == "test_dev_connection"
        assert sensor.device_class == BinarySensorDeviceClass.CONNECTIVITY

    def test_on_when_connected(self, mock_coordinator):
        assert self._make_sensor(mock_coordinator).is_on is True

    def test_off_when_disconnected(self, mock_coordinator):
        mock_coordinator.data = {"connection_state": "Disconnected"}
        assert self._make_sensor(mock_coordinator).is_on is False

    def test_unknown_without_data(self, mock_coordinator):
        """None (unknown) is honest; False would claim the intercom is offline."""
        mock_coordinator.data = None
        assert self._make_sensor(mock_coordinator).is_on is None

    @pytest.mark.asyncio
    async def test_setup_entry_creates_the_descriptor_sensors(self, mock_coordinator):
        from custom_components.fermax_blue.binary_sensor import (
            BINARY_SENSOR_TYPES,
            async_setup_entry,
        )
        from custom_components.fermax_blue.const import DOMAIN

        entry = MagicMock()
        entry.entry_id = "entry_1"
        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_1": [mock_coordinator]}}
        added = []

        await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

        assert len(added) == len(BINARY_SENSOR_TYPES)


class TestOpenDoorButton:
    """Per-door open button — the other path that opens a door."""

    def _make_button(self, mock_coordinator):
        from custom_components.fermax_blue.button import FermaxOpenDoorButton

        return FermaxOpenDoorButton(mock_coordinator, "GENERAL", "Portal")

    def test_unique_id_and_name(self, mock_coordinator):
        button = self._make_button(mock_coordinator)

        assert button.unique_id == "test_dev_GENERAL_open"
        assert button.name == "Open Portal"

    def test_falls_back_to_door_name_without_title(self, mock_coordinator):
        from custom_components.fermax_blue.button import FermaxOpenDoorButton

        assert FermaxOpenDoorButton(mock_coordinator, "GENERAL", "").name == "Open GENERAL"

    @pytest.mark.asyncio
    async def test_press_opens_the_door(self, mock_coordinator):
        mock_coordinator.open_door = AsyncMock(return_value=True)
        button = self._make_button(mock_coordinator)

        await button.async_press()

        mock_coordinator.open_door.assert_awaited_once_with("GENERAL")

    @pytest.mark.asyncio
    async def test_failed_press_does_not_raise(self, mock_coordinator):
        """A failed open is logged, not raised — HA would mark the entity broken."""
        mock_coordinator.open_door = AsyncMock(return_value=False)
        button = self._make_button(mock_coordinator)

        await button.async_press()

        mock_coordinator.open_door.assert_awaited_once()


class TestCameraPreviewButton:
    """Camera preview button."""

    def _make_button(self, mock_coordinator):
        from custom_components.fermax_blue.button import FermaxCameraPreviewButton

        return FermaxCameraPreviewButton(mock_coordinator)

    def test_unique_id(self, mock_coordinator):
        assert self._make_button(mock_coordinator).unique_id == "test_dev_camera_preview"

    @pytest.mark.asyncio
    async def test_press_starts_preview(self, mock_coordinator):
        result = MagicMock()
        result.description = "Auto on is starting"
        mock_coordinator.start_camera_preview = AsyncMock(return_value=result)
        button = self._make_button(mock_coordinator)

        await button.async_press()

        mock_coordinator.start_camera_preview.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_press_handles_refused_preview(self, mock_coordinator):
        mock_coordinator.start_camera_preview = AsyncMock(return_value=None)
        button = self._make_button(mock_coordinator)

        await button.async_press()

        mock_coordinator.start_camera_preview.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_setup_entry_creates_a_button_per_door_plus_the_fixed_three(
        self, mock_coordinator
    ):
        from custom_components.fermax_blue.button import async_setup_entry
        from custom_components.fermax_blue.const import DOMAIN

        entry = MagicMock()
        entry.entry_id = "entry_1"
        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_1": [mock_coordinator]}}
        added = []

        await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

        ids = sorted(button.unique_id for button in added)
        assert ids == [
            "test_dev_GENERAL_open",
            "test_dev_call_guard",
            "test_dev_camera_preview",
            "test_dev_f1",
        ]


class TestCameraImageSelection:
    """Which frame the camera serves, and when it reports itself available."""

    def _make_camera(self, mock_coordinator):
        from custom_components.fermax_blue.camera import FermaxCamera

        return FermaxCamera(mock_coordinator)

    def test_unique_id(self, mock_coordinator):
        mock_coordinator.last_photo = None
        mock_coordinator.stream_session = None
        assert self._make_camera(mock_coordinator).unique_id == "test_dev_camera"

    @pytest.mark.asyncio
    async def test_live_frame_wins_over_last_photo(self, mock_coordinator):
        mock_coordinator.last_photo = b"old-photo"
        mock_coordinator.stream_session = MagicMock(latest_frame=b"live-frame")

        assert await self._make_camera(mock_coordinator).async_camera_image() == b"live-frame"

    @pytest.mark.asyncio
    async def test_falls_back_to_last_photo_without_a_stream(self, mock_coordinator):
        mock_coordinator.last_photo = b"old-photo"
        mock_coordinator.stream_session = None

        assert await self._make_camera(mock_coordinator).async_camera_image() == b"old-photo"

    @pytest.mark.asyncio
    async def test_returns_none_with_nothing_to_serve(self, mock_coordinator):
        mock_coordinator.last_photo = None
        mock_coordinator.stream_session = None

        assert await self._make_camera(mock_coordinator).async_camera_image() is None

    def test_available_with_only_a_persisted_photo(self, mock_coordinator):
        """Survives a restart: the persisted frame alone keeps the entity usable."""
        mock_coordinator.last_photo = b"photo"
        mock_coordinator.stream_session = None

        assert self._make_camera(mock_coordinator).available is True

    def test_available_with_only_a_live_frame(self, mock_coordinator):
        mock_coordinator.last_photo = None
        mock_coordinator.stream_session = MagicMock(latest_frame=b"frame")

        assert self._make_camera(mock_coordinator).available is True

    def test_is_on_true_whenever_a_photo_exists(self, mock_coordinator):
        """HA answers 503 on camera_proxy when is_on is False."""
        mock_coordinator.last_photo = b"photo"
        mock_coordinator.stream_session = None
        camera = self._make_camera(mock_coordinator)

        assert camera.is_streaming is False
        assert camera.is_on is True

    def test_is_on_false_when_there_is_nothing(self, mock_coordinator):
        mock_coordinator.last_photo = None
        mock_coordinator.stream_session = None

        assert self._make_camera(mock_coordinator).is_on is False

    def test_is_streaming_tracks_the_session(self, mock_coordinator):
        mock_coordinator.last_photo = None
        mock_coordinator.stream_session = MagicMock(is_active=True)

        camera = self._make_camera(mock_coordinator)
        assert camera.is_streaming is True
        assert camera.is_on is True

    @pytest.mark.asyncio
    async def test_turn_off_stops_the_stream(self, mock_coordinator):
        mock_coordinator.last_photo = None
        mock_coordinator.stream_session = None
        mock_coordinator.stop_stream = AsyncMock()

        await self._make_camera(mock_coordinator).async_turn_off()

        mock_coordinator.stop_stream.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_setup_entry_creates_one_camera(self, mock_coordinator):
        from custom_components.fermax_blue.camera import async_setup_entry
        from custom_components.fermax_blue.const import DOMAIN

        entry = MagicMock()
        entry.entry_id = "entry_1"
        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_1": [mock_coordinator]}}
        added = []

        await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

        assert [camera.unique_id for camera in added] == ["test_dev_camera"]


class TestEventEntities:
    """Doorbell, door-opened and camera-on event entities."""

    def _make(self, cls, mock_coordinator):
        entity = cls(mock_coordinator)
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
        entity._trigger_event = MagicMock()
        return entity

    def test_unique_ids_and_types(self, mock_coordinator):
        from custom_components.fermax_blue.event import (
            FermaxCameraOnEvent,
            FermaxDoorOpenedEvent,
        )

        opened = FermaxDoorOpenedEvent(mock_coordinator)
        camera_on = FermaxCameraOnEvent(mock_coordinator)

        assert opened.unique_id == "test_dev_door_opened_event"
        assert opened.event_types == ["door_opened"]
        assert camera_on.unique_id == "test_dev_camera_on_event"
        assert camera_on.event_types == ["camera_on"]

    def test_each_entity_fires_its_own_event_type(self, mock_coordinator):
        from custom_components.fermax_blue.event import (
            FermaxCameraOnEvent,
            FermaxDoorbellEvent,
            FermaxDoorOpenedEvent,
        )

        for cls, event_type in (
            (FermaxDoorbellEvent, "ring"),
            (FermaxDoorOpenedEvent, "door_opened"),
            (FermaxCameraOnEvent, "camera_on"),
        ):
            entity = self._make(cls, mock_coordinator)
            entity._handle_event()
            entity._trigger_event.assert_called_once_with(event_type)

    @pytest.mark.asyncio
    async def test_setup_entry_creates_the_three_event_entities(self, mock_coordinator):
        from custom_components.fermax_blue.const import DOMAIN
        from custom_components.fermax_blue.event import async_setup_entry

        entry = MagicMock()
        entry.entry_id = "entry_1"
        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_1": [mock_coordinator]}}
        added = []

        await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

        assert sorted(entity.unique_id for entity in added) == [
            "test_dev_camera_on_event",
            "test_dev_door_opened_event",
            "test_dev_doorbell_event",
        ]
