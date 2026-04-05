"""Button platform for Fermax Blue."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
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
    """Set up Fermax Blue buttons."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []

    for coordinator in coordinators:
        for door_name, door in coordinator.pairing.access_doors.items():
            if door.visible:
                entities.append(
                    FermaxOpenDoorButton(coordinator, door_name, door.title)
                )
        entities.append(FermaxCameraPreviewButton(coordinator))
        entities.append(FermaxF1Button(coordinator))
        entities.append(FermaxCallGuardButton(coordinator))

    async_add_entities(entities)


class FermaxOpenDoorButton(FermaxBlueEntity, ButtonEntity):
    """Button to open a door."""

    _attr_translation_key = "open_door"

    def __init__(
        self,
        coordinator: FermaxBlueCoordinator,
        door_name: str,
        door_title: str,
    ) -> None:
        super().__init__(coordinator)
        self._door_name = door_name
        self._attr_unique_id = f"{self._device_id}_{door_name}_open"
        self._attr_name = f"Open {door_title or door_name}"

    async def async_press(self) -> None:
        """Open the door."""
        success = await self.coordinator.open_door(self._door_name)
        if success:
            _LOGGER.info("Door %s opened via button", self._door_name)
        else:
            _LOGGER.error("Failed to open door %s via button", self._door_name)


class FermaxCameraPreviewButton(FermaxBlueEntity, ButtonEntity):
    """Button to start camera preview (auto-on)."""

    _attr_translation_key = "camera_preview"
    _attr_icon = "mdi:cctv"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_camera_preview"

    async def async_press(self) -> None:
        """Start camera preview."""
        result = await self.coordinator.start_camera_preview()
        if result:
            _LOGGER.info("Camera preview started: %s", result.description)
        else:
            _LOGGER.error("Failed to start camera preview")


class FermaxF1Button(FermaxBlueEntity, ButtonEntity):
    """Button for F1 auxiliary function."""

    _attr_translation_key = "f1"
    _attr_icon = "mdi:numeric-1-box"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_f1"

    async def async_press(self) -> None:
        """Press F1."""
        await self.coordinator.press_f1()


class FermaxCallGuardButton(FermaxBlueEntity, ButtonEntity):
    """Button to call the building guard/janitor."""

    _attr_translation_key = "call_guard"
    _attr_icon = "mdi:account-tie"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_call_guard"

    async def async_press(self) -> None:
        """Call the guard."""
        await self.coordinator.call_guard()
