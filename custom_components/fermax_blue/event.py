"""Event platform for Fermax Blue."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_CAMERA_ON, SIGNAL_DOOR_OPENED, SIGNAL_DOORBELL_RING
from .coordinator import FermaxBlueCoordinator
from .entity import FermaxBlueEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fermax Blue event entities."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[EventEntity] = []

    for coordinator in coordinators:
        entities.append(FermaxDoorbellEvent(coordinator))
        entities.append(FermaxDoorOpenedEvent(coordinator))
        entities.append(FermaxCameraOnEvent(coordinator))

    async_add_entities(entities)


class FermaxDoorbellEvent(FermaxBlueEntity, EventEntity):
    """Event entity for doorbell rings."""

    _attr_translation_key = "doorbell"
    _attr_event_types = ["ring"]
    _attr_icon = "mdi:bell-ring"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_doorbell_event"

    async def async_added_to_hass(self) -> None:
        """Register callbacks when added to hass."""
        await super().async_added_to_hass()

        for door_name in self.coordinator.pairing.access_doors:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_DOORBELL_RING.format(self._device_id, door_name),
                    self._handle_event,
                )
            )

    @callback
    def _handle_event(self) -> None:
        self._trigger_event("ring")
        self.async_write_ha_state()


class FermaxDoorOpenedEvent(FermaxBlueEntity, EventEntity):
    """Event entity for door openings."""

    _attr_translation_key = "door_opened"
    _attr_event_types = ["door_opened"]
    _attr_icon = "mdi:door-open"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_door_opened_event"

    async def async_added_to_hass(self) -> None:
        """Register callbacks when added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_DOOR_OPENED.format(self._device_id),
                self._handle_event,
            )
        )

    @callback
    def _handle_event(self) -> None:
        self._trigger_event("door_opened")
        self.async_write_ha_state()


class FermaxCameraOnEvent(FermaxBlueEntity, EventEntity):
    """Event entity for camera preview activations."""

    _attr_translation_key = "camera_on"
    _attr_event_types = ["camera_on"]
    _attr_icon = "mdi:cctv"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_camera_on_event"

    async def async_added_to_hass(self) -> None:
        """Register callbacks when added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CAMERA_ON.format(self._device_id),
                self._handle_event,
            )
        )

    @callback
    def _handle_event(self) -> None:
        self._trigger_event("camera_on")
        self.async_write_ha_state()
