"""Sensor platform for Fermax Blue."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FermaxBlueCoordinator
from .entity import FermaxBlueEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fermax Blue sensors."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for coordinator in coordinators:
        entities.append(FermaxWifiSignalSensor(coordinator))
        entities.append(FermaxDeviceStatusSensor(coordinator))
        entities.append(FermaxLastOpeningSensor(coordinator))
        entities.append(FermaxLastCallSensor(coordinator))

    async_add_entities(entities)


class FermaxWifiSignalSensor(FermaxBlueEntity, SensorEntity):
    """WiFi signal strength sensor."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi"
    _attr_translation_key = "wifi_signal"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_wifi_signal"

    @property
    def native_value(self) -> int | None:
        """Return signal strength in bars (0-4)."""
        if self.coordinator.data:
            return self.coordinator.data.get("wireless_signal")
        return None


class FermaxDeviceStatusSensor(FermaxBlueEntity, SensorEntity):
    """Device status sensor."""

    _attr_translation_key = "status"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_status"

    @property
    def native_value(self) -> str | None:
        """Return the device status."""
        if self.coordinator.data:
            return self.coordinator.data.get("status")
        return None


class FermaxLastOpeningSensor(FermaxBlueEntity, SensorEntity):
    """Sensor showing the last door opening."""

    _attr_translation_key = "last_opening"
    _attr_icon = "mdi:door-open"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_last_opening"

    @property
    def native_value(self) -> str | None:
        """Return the timestamp of the last opening."""
        if self.coordinator.last_opening:
            return self.coordinator.last_opening.timestamp
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str | None] | None:
        """Return extra attributes about the last opening."""
        if not self.coordinator.last_opening:
            return None
        record = self.coordinator.last_opening
        return {
            "user": record.user,
            "door": record.door,
            "guest_email": record.guest_email,
        }


class FermaxLastCallSensor(FermaxBlueEntity, SensorEntity):
    """Sensor showing the last call/doorbell ring."""

    _attr_translation_key = "last_call"
    _attr_icon = "mdi:phone-incoming"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_last_call"

    @property
    def native_value(self) -> str | None:
        """Return the timestamp of the last call."""
        if self.coordinator.last_call:
            return self.coordinator.last_call.call_date.isoformat()
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes about call history."""
        if not self.coordinator.last_call:
            return None
        last = self.coordinator.last_call
        attrs: dict[str, Any] = {
            "call_id": last.call_id,
            "device_id": last.device_id,
            "answered": last.answered,
            "photo_id": last.photo_id,
        }
        # Include recent call count
        attrs["recent_calls"] = len(self.coordinator.call_log)
        return attrs
