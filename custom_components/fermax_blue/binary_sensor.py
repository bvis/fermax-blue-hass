"""Binary sensor platform for Fermax Blue."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
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
    """Set up Fermax Blue binary sensors."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []

    for coordinator in coordinators:
        entities.append(FermaxConnectionSensor(coordinator))

    async_add_entities(entities)


class FermaxConnectionSensor(FermaxBlueEntity, BinarySensorEntity):
    """Sensor for device connection status."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "connection"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_connection"

    @property
    def is_on(self) -> bool | None:
        """Return True if connected."""
        if self.coordinator.data:
            return self.coordinator.data.get("connection_state") == "Connected"
        return None
