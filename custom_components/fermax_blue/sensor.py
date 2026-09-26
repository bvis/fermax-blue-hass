"""Sensor platform for Fermax Blue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import redact_email
from .const import DOMAIN
from .coordinator import FermaxBlueCoordinator
from .entity import FermaxBlueEntity


@dataclass(frozen=True)
class SensorTypeInfo:
    """Descriptor for a sensor type."""

    translation_key: str
    icon: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    unit: str | None = None
    entity_registry_enabled_default: bool = True


SENSOR_TYPES: dict[str, SensorTypeInfo] = {
    "wifi_signal": SensorTypeInfo(
        translation_key="wifi_signal",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "device_status": SensorTypeInfo(
        translation_key="status",
    ),
    "last_opening": SensorTypeInfo(
        translation_key="last_opening",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    "last_call": SensorTypeInfo(
        translation_key="last_call",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fermax Blue sensors."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    registry = er.async_get(hass)

    for coordinator in coordinators:
        # Installs from before 2026-04-15 carry a `<device>_status` sensor that
        # `_device_status` superseded; it would otherwise linger forever as a
        # restored, permanently unavailable entity.
        legacy = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{coordinator.pairing.device_id}_status"
        )
        if legacy:
            registry.async_remove(legacy)
        for key in SENSOR_TYPES:
            entities.append(FermaxSensor(coordinator, key))

    async_add_entities(entities)


def _aware(value: datetime | None) -> datetime | None:
    """A timestamp sensor needs a zone; the cloud's instants are UTC (#106)."""
    if value is None or value.tzinfo:
        return value
    return value.replace(tzinfo=UTC)


class FermaxSensor(FermaxBlueEntity, SensorEntity):
    """Generic Fermax sensor driven by a SensorTypeInfo descriptor."""

    def __init__(self, coordinator: FermaxBlueCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        descriptor = SENSOR_TYPES[key]
        self._attr_unique_id = f"{self._device_id}_{key}"
        self._attr_translation_key = descriptor.translation_key
        self._attr_icon = descriptor.icon
        self._attr_device_class = descriptor.device_class
        self._attr_state_class = descriptor.state_class
        self._attr_native_unit_of_measurement = descriptor.unit
        self._attr_entity_registry_enabled_default = descriptor.entity_registry_enabled_default

    @property
    def native_value(self) -> Any:
        """Return the sensor value based on key."""
        if self._key == "wifi_signal":
            if self.coordinator.data:
                return self.coordinator.data.get("wireless_signal")
            return None
        if self._key == "device_status":
            if self.coordinator.data:
                return self.coordinator.data.get("status")
            return None
        if self._key == "last_opening":
            if self.coordinator.last_opening:
                return _aware(dt_util.parse_datetime(self.coordinator.last_opening.timestamp))
            return None
        if self._key == "last_call":
            if self.coordinator.last_call:
                return _aware(self.coordinator.last_call.call_date)
            return None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes for sensors that expose them."""
        if self._key == "last_opening":
            if not self.coordinator.last_opening:
                return None
            record = self.coordinator.last_opening
            return {
                "user": redact_email(record.user),
                "door": record.door,
                "guest_email": redact_email(record.guest_email),
            }
        if self._key == "last_call":
            if not self.coordinator.last_call:
                return None
            last = self.coordinator.last_call
            attrs: dict[str, Any] = {
                "call_id": last.call_id,
                "device_id": last.device_id,
                "answered": last.answered,
                "photo_id": last.photo_id,
            }
            attrs["recent_calls"] = len(self.coordinator.call_log)
            return attrs
        return None


# ---------------------------------------------------------------------------
# Backward-compatible aliases so existing tests and imports still work
# ---------------------------------------------------------------------------


class FermaxWifiSignalSensor(FermaxSensor):
    """Backward-compatible alias for the wifi_signal sensor."""

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator, "wifi_signal")


class FermaxDeviceStatusSensor(FermaxSensor):
    """Backward-compatible alias for the device_status sensor."""

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator, "device_status")


class FermaxLastOpeningSensor(FermaxSensor):
    """Backward-compatible alias for the last_opening sensor."""

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator, "last_opening")


class FermaxLastCallSensor(FermaxSensor):
    """Backward-compatible alias for the last_call sensor."""

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator, "last_call")
