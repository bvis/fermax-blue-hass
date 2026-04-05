"""Switch platform for Fermax Blue."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FermaxBlueCoordinator
from .entity import FermaxBlueEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fermax Blue switches."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []

    for coordinator in coordinators:
        if coordinator.notification_listener:
            entities.append(FermaxNotificationSwitch(coordinator))
        entities.append(FermaxDndSwitch(coordinator))
        entities.append(FermaxPhotoCallerSwitch(coordinator))

    async_add_entities(entities)


class FermaxNotificationSwitch(FermaxBlueEntity, SwitchEntity):
    """Switch to enable/disable doorbell notifications."""

    _attr_translation_key = "notifications"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_notifications"
        self._is_on = True

    @property
    def is_on(self) -> bool:
        """Return True if notifications are enabled."""
        if self.coordinator.notification_listener:
            return self.coordinator.notification_listener.is_started
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable notifications."""
        if self.coordinator.notification_listener:
            await self.coordinator.notification_listener.start()
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable notifications."""
        if self.coordinator.notification_listener:
            await self.coordinator.notification_listener.stop()
            self._is_on = False
            self.async_write_ha_state()


class FermaxDndSwitch(FermaxBlueEntity, SwitchEntity):
    """Switch for Do Not Disturb mode."""

    _attr_translation_key = "dnd"
    _attr_icon = "mdi:bell-off"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_dnd"

    @property
    def is_on(self) -> bool | None:
        """Return True if DND is enabled."""
        return self.coordinator.dnd_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable Do Not Disturb."""
        await self.coordinator.set_dnd(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable Do Not Disturb."""
        await self.coordinator.set_dnd(False)
        self.async_write_ha_state()


class FermaxPhotoCallerSwitch(FermaxBlueEntity, SwitchEntity):
    """Switch to enable/disable photo caller."""

    _attr_translation_key = "photo_caller"
    _attr_icon = "mdi:camera-account"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_photo_caller"

    @property
    def is_on(self) -> bool | None:
        """Return True if photo caller is enabled."""
        if self.coordinator.device_info:
            return self.coordinator.device_info.photocaller
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable photo caller."""
        await self.coordinator.set_photo_caller(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable photo caller."""
        await self.coordinator.set_photo_caller(False)
        self.async_write_ha_state()
