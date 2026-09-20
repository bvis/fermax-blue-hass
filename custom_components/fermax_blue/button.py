"""Button platform for Fermax Blue."""

from __future__ import annotations

import logging

import httpx
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
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
            entities.append(FermaxOpenDoorButton(coordinator, door_name, door.title))
        entities.append(FermaxCameraPreviewButton(coordinator))
        entities.append(FermaxF1Button(coordinator))
        entities.append(FermaxVideoSourceButton(coordinator))
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
        self._door_title = door_title or door_name
        self._attr_unique_id = f"{self._device_id}_{door_name}_open"
        self._attr_name = f"Open {self._door_title}"

    async def async_press(self) -> None:
        """Open the door."""
        if not await self.coordinator.open_door(self._door_name):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="open_door_failed",
                translation_placeholders={"door": self._door_title},
            )
        _LOGGER.info("Door %s opened via button", self._door_name)


class FermaxCameraPreviewButton(FermaxBlueEntity, ButtonEntity):
    """Button to start camera preview (auto-on)."""

    _attr_translation_key = "camera_preview"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_camera_preview"

    async def async_press(self) -> None:
        """Start camera preview."""
        result = await self.coordinator.start_camera_preview()
        if not result:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="camera_preview_failed"
            )
        _LOGGER.info("Camera preview started: %s", result.description)


class FermaxF1Button(FermaxBlueEntity, ButtonEntity):
    """Button for F1 auxiliary function."""

    _attr_translation_key = "f1"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_f1"

    async def async_press(self) -> None:
        """Press F1."""
        try:
            await self.coordinator.press_f1()
        except httpx.HTTPError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="f1_failed"
            ) from err


class FermaxVideoSourceButton(FermaxBlueEntity, ButtonEntity):
    """Button to switch the intercom to the next video source."""

    _attr_translation_key = "video_source"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_video_source"

    async def async_press(self) -> None:
        """Switch to the next video source.

        The intercom only accepts a source change inside a live session, so
        pressing without one is reported as what it is: something the user can
        fix, not a device failure.
        """
        if not self.coordinator.has_active_stream:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_active_stream"
            )

        result = await self.coordinator.change_video_source()
        if not result:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="video_source_failed"
            )
        _LOGGER.info("Video source changed: %s", result.description)


class FermaxCallGuardButton(FermaxBlueEntity, ButtonEntity):
    """Button to call the building guard/janitor."""

    _attr_translation_key = "call_guard"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_call_guard"

    async def async_press(self) -> None:
        """Call the guard."""
        try:
            await self.coordinator.call_guard()
        except httpx.HTTPError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="call_guard_failed"
            ) from err
