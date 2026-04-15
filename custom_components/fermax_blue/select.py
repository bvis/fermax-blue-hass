"""Select platform for Fermax Blue."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CALL_MODES, DOMAIN
from .coordinator import FermaxBlueCoordinator
from .entity import FermaxBlueEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fermax Blue select entities."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(FermaxCallModeSelect(c) for c in coordinators)


class FermaxCallModeSelect(FermaxBlueEntity, SelectEntity):
    """Select entity to control doorbell call behavior."""

    _attr_translation_key = "call_mode"
    _attr_options = CALL_MODES

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_call_mode"

    @property
    def current_option(self) -> str:
        """Return the current call mode."""
        return self.coordinator.call_mode

    async def async_select_option(self, option: str) -> None:
        """Set the call mode."""
        self.coordinator.call_mode = option
        self.async_write_ha_state()
        _LOGGER.info("Call mode set to: %s", option)

    @property
    def available(self) -> bool:
        """Always available — not dependent on device connection."""
        return True
