"""Number platform for Fermax Blue."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    MAX_STREAM_DURATION,
    MIN_STREAM_DURATION,
)
from .coordinator import FermaxBlueCoordinator
from .entity import FermaxBlueEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fermax Blue number entities."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(FermaxStreamDurationNumber(c) for c in coordinators)


class FermaxStreamDurationNumber(FermaxBlueEntity, NumberEntity):
    """Number entity to control stream duration."""

    _attr_translation_key = "stream_duration"
    _attr_native_min_value = MIN_STREAM_DURATION
    _attr_native_max_value = MAX_STREAM_DURATION
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "s"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_stream_duration"

    @property
    def native_value(self) -> float:
        """Return the current stream duration."""
        return self.coordinator.stream_duration

    async def async_set_native_value(self, value: float) -> None:
        """Set the stream duration."""
        self.coordinator.stream_duration = int(value)
        self.async_write_ha_state()
        _LOGGER.info("Stream duration set to %ds", int(value))

    @property
    def available(self) -> bool:
        """Always available."""
        return True
