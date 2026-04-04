"""Binary sensor platform for Fermax Blue."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_CALL_ENDED, SIGNAL_DOORBELL_RING
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
        entities.append(FermaxDoorbellSensor(coordinator))

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


class FermaxDoorbellSensor(FermaxBlueEntity, BinarySensorEntity):
    """Sensor that activates when doorbell rings."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_translation_key = "doorbell"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_doorbell"
        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        """Register callbacks when added to hass."""
        await super().async_added_to_hass()

        # Listen for doorbell ring on any door
        for door_name in self.coordinator.pairing.access_doors:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_DOORBELL_RING.format(self._device_id, door_name),
                    self._ring_callback,
                )
            )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CALL_ENDED.format(self._device_id),
                self._call_ended_callback,
            )
        )

    @callback
    def _ring_callback(self) -> None:
        """Handle doorbell ring."""
        self._attr_is_on = True
        self.async_write_ha_state()

    @callback
    def _call_ended_callback(self) -> None:
        """Handle call end."""
        self._attr_is_on = False
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return True if doorbell is ringing."""
        return bool(self._attr_is_on)
