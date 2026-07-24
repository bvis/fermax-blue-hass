"""Switch platform for Fermax Blue."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

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
        entities.append(FermaxRingPreviewSwitch(coordinator))

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


class FermaxRingPreviewSwitch(FermaxBlueEntity, SwitchEntity, RestoreEntity):
    """Switch for the receive-only live preview on doorbell ring.

    Per-device, purely local (no Fermax API involved), and restored across
    restarts. When on, a ring starts a receive-only stream so the camera
    shows the current visitor; the call is never answered.
    """

    _attr_translation_key = "ring_preview"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_ring_preview"

    async def async_added_to_hass(self) -> None:
        """Restore the previous setting when added to hass."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self.coordinator.ring_preview = last_state.state == STATE_ON

    @property
    def is_on(self) -> bool:
        """Return True if ring preview is enabled."""
        return self.coordinator.ring_preview

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable ring preview."""
        self.coordinator.ring_preview = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable ring preview."""
        self.coordinator.ring_preview = False
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Always available — a local setting, not device state."""
        return True


class FermaxDndSwitch(FermaxBlueEntity, SwitchEntity):
    """Switch for Do Not Disturb mode."""

    _attr_translation_key = "dnd"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_dnd"
        self._optimistic_state: bool | None = None

    @property
    def is_on(self) -> bool | None:
        """Return True if DND is enabled."""
        if self._optimistic_state is not None:
            return self._optimistic_state
        return self.coordinator.dnd_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable Do Not Disturb."""
        self._optimistic_state = True
        self.async_write_ha_state()
        await self.coordinator.set_dnd(True)
        self._optimistic_state = None

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable Do Not Disturb."""
        self._optimistic_state = False
        self.async_write_ha_state()
        await self.coordinator.set_dnd(False)
        self._optimistic_state = None


class FermaxPhotoCallerSwitch(FermaxBlueEntity, SwitchEntity):
    """Switch to enable/disable photo caller."""

    _attr_translation_key = "photo_caller"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_photo_caller"
        self._optimistic_state: bool | None = None

    @property
    def is_on(self) -> bool | None:
        """Return True if photo caller is enabled."""
        if self._optimistic_state is not None:
            return self._optimistic_state
        if self.coordinator.device_info:
            return self.coordinator.device_info.photocaller
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable photo caller."""
        self._optimistic_state = True
        self.async_write_ha_state()
        await self.coordinator.set_photo_caller(True)
        self._optimistic_state = None

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable photo caller."""
        self._optimistic_state = False
        self.async_write_ha_state()
        await self.coordinator.set_photo_caller(False)
        self._optimistic_state = None
