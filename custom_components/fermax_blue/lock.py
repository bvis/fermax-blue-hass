"""Lock platform for Fermax Blue (door opening)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .coordinator import FermaxBlueCoordinator
from .entity import FermaxBlueEntity

_LOGGER = logging.getLogger(__name__)

AUTO_LOCK_SECONDS = 5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fermax Blue locks."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[LockEntity] = []

    for coordinator in coordinators:
        for door_name, door in coordinator.pairing.access_doors.items():
            entities.append(FermaxDoorLock(coordinator, door_name, door.title))

    async_add_entities(entities)


class FermaxDoorLock(FermaxBlueEntity, LockEntity):
    """Represents a Fermax door that can be opened/locked."""

    _attr_translation_key = "door"

    def __init__(
        self,
        coordinator: FermaxBlueCoordinator,
        door_name: str,
        door_title: str,
    ) -> None:
        super().__init__(coordinator)
        self._door_name = door_name
        self._attr_unique_id = f"{self._device_id}_{door_name}_lock"
        self._attr_name = door_title or door_name
        self._is_locked = True
        self._auto_lock_unsub: CALLBACK_TYPE | None = None

    @property
    def is_locked(self) -> bool:
        """Return True if the door is locked."""
        return self._is_locked

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock (open) the door."""
        success = await self.coordinator.open_door(self._door_name)
        if success:
            self._is_locked = False
            self.async_write_ha_state()
            _LOGGER.info("Door %s opened", self._door_name)

            if self._auto_lock_unsub:
                self._auto_lock_unsub()

            @callback
            def _auto_lock(_now: Any) -> None:
                self._is_locked = True
                self.async_write_ha_state()
                self._auto_lock_unsub = None

            self._auto_lock_unsub = async_call_later(
                self.hass, AUTO_LOCK_SECONDS, _auto_lock
            )
        else:
            _LOGGER.error("Failed to open door %s", self._door_name)

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the door (no-op, doors auto-lock)."""
        self._is_locked = True
        self.async_write_ha_state()
