"""Binary sensor platform for Fermax Blue."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FermaxBlueConfigEntry, FermaxBlueCoordinator
from .entity import FermaxBlueEntity

# Coordinator-driven or local: no per-entity update calls
PARALLEL_UPDATES = 0


@dataclass(frozen=True)
class BinarySensorTypeInfo:
    """Descriptor for a binary sensor type."""

    translation_key: str
    device_class: BinarySensorDeviceClass | None = None
    entity_category: EntityCategory | None = None


BINARY_SENSOR_TYPES: dict[str, BinarySensorTypeInfo] = {
    "connection": BinarySensorTypeInfo(
        translation_key="connection",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FermaxBlueConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fermax Blue binary sensors."""
    coordinators = entry.runtime_data
    entities: list[BinarySensorEntity] = []

    for coordinator in coordinators:
        for key in BINARY_SENSOR_TYPES:
            entities.append(FermaxBinarySensor(coordinator, key))

    async_add_entities(entities)


class FermaxBinarySensor(FermaxBlueEntity, BinarySensorEntity):
    """Generic Fermax binary sensor driven by a BinarySensorTypeInfo descriptor."""

    def __init__(self, coordinator: FermaxBlueCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        descriptor = BINARY_SENSOR_TYPES[key]
        self._attr_unique_id = f"{self._device_id}_{key}"
        self._attr_translation_key = descriptor.translation_key
        self._attr_device_class = descriptor.device_class
        self._attr_entity_category = descriptor.entity_category

    @property
    def available(self) -> bool:
        """Available while the cloud answers: a disconnected intercom reads as off."""
        return self.coordinator.last_update_success and bool(self.coordinator.data)

    @property
    def is_on(self) -> bool | None:
        """Return sensor state based on key."""
        if self._key == "connection":
            if self.coordinator.data:
                return self.coordinator.data.get("connection_state") == "Connected"
            return None
        return None


# ---------------------------------------------------------------------------
# Backward-compatible alias
# ---------------------------------------------------------------------------


class FermaxConnectionSensor(FermaxBinarySensor):
    """Backward-compatible alias for the connection binary sensor."""

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator, "connection")
