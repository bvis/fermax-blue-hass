"""Camera platform for Fermax Blue."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_DOORBELL_RING
from .coordinator import FermaxBlueCoordinator
from .entity import FermaxBlueEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fermax Blue cameras."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[Camera] = []

    for coordinator in coordinators:
        if coordinator.device_info and coordinator.device_info.photocaller:
            entities.append(FermaxCamera(coordinator))

    async_add_entities(entities)


class FermaxCamera(FermaxBlueEntity, Camera):
    """Camera entity showing last visitor photo.

    Supports on-demand camera preview via the auto-on feature.
    When turned on, it triggers the intercom to start streaming
    and captures the visitor photo.

    Note: Full video streaming requires mediasoup/Socket.IO client
    which is not yet implemented. Currently supports still image capture.
    """

    _attr_translation_key = "visitor"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        FermaxBlueEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._attr_unique_id = f"{self._device_id}_camera"
        self._attr_is_streaming = False

    async def async_added_to_hass(self) -> None:
        """Register for doorbell ring events."""
        await super().async_added_to_hass()

        for door_name in self.coordinator.pairing.access_doors:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_DOORBELL_RING.format(self._device_id, door_name),
                    self._on_doorbell_ring,
                )
            )

    @callback
    def _on_doorbell_ring(self) -> None:
        """Handle doorbell ring - trigger image refresh."""
        self.async_write_ha_state()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the last captured visitor photo."""
        return self.coordinator.last_photo

    async def async_turn_on(self) -> None:
        """Start camera preview via auto-on.

        This triggers the intercom to activate its camera and start
        streaming. The video stream uses mediasoup over Socket.IO
        (signaling server: signaling-pro-duoxme.fermax.io).

        Currently captures a still image. Full video streaming support
        is planned for a future release.
        """
        result = await self.coordinator.start_camera_preview()
        if result:
            _LOGGER.info("Camera auto-on started: %s", result.description)
            # Trigger a refresh to pick up new photos
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to start camera auto-on")

    async def async_turn_off(self) -> None:
        """Stop camera preview (no-op, stream auto-expires)."""

    @property
    def is_on(self) -> bool:
        """Return True if the camera preview is active."""
        return self.coordinator.camera_active
