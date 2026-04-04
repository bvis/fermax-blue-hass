"""Sensor platform for Fermax Blue."""

from __future__ import annotations

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
