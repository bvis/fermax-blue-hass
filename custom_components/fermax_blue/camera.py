"""Camera platform for Fermax Blue."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiohttp import web
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
        entities.append(FermaxCamera(coordinator))

    async_add_entities(entities)


class FermaxCamera(FermaxBlueEntity, Camera):
    """Camera entity with live video streaming and visitor photo capture.

    Supports two modes:
    - Still image: shows the last captured visitor photo (from doorbell ring)
    - Live stream: connects to the intercom camera via mediasoup and serves
      MJPEG frames in real-time (triggered by turn_on / camera preview button)
    """

    _attr_translation_key = "visitor"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        FermaxBlueEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._attr_unique_id = f"{self._device_id}_camera"

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

    @property
    def available(self) -> bool:
        """Camera is available if we have any image to serve."""
        if self.coordinator.last_photo:
            return True
        stream = self.coordinator.stream_session
        if stream and stream.latest_frame:
            return True
        return super().available

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the latest frame: live stream if active, else last captured frame."""
        stream = self.coordinator.stream_session
        if stream and stream.latest_frame:
            return stream.latest_frame
        return self.coordinator.last_photo

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        """Serve live MJPEG stream from the intercom camera."""
        stream = self.coordinator.stream_session

        # If no active stream, serve last photo as single MJPEG frame
        if not stream or not stream.is_active:
            image = await self.async_camera_image()
            if not image:
                return None

            response = web.StreamResponse(
                status=200,
                reason="OK",
                headers={
                    "Content-Type": "multipart/x-mixed-replace;boundary=frameboundary",
                },
            )
            await response.prepare(request)
            with contextlib.suppress(ConnectionResetError, ConnectionError):
                await response.write(
                    b"--frameboundary\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: "
                    + str(len(image)).encode()
                    + b"\r\n\r\n"
                    + image
                    + b"\r\n"
                )
            return response

        # Active stream: serve live frames
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "multipart/x-mixed-replace;boundary=frameboundary",
            },
        )
        await response.prepare(request)

        try:
            while stream.is_active:
                frame = stream.latest_frame
                if frame:
                    await response.write(
                        b"--frameboundary\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: "
                        + str(len(frame)).encode()
                        + b"\r\n\r\n"
                        + frame
                        + b"\r\n"
                    )
                await asyncio.sleep(0.1)
        except (ConnectionResetError, ConnectionError):
            pass

        return response

    async def async_turn_on(self) -> None:
        """Start live camera stream via auto-on + mediasoup."""
        result = await self.coordinator.start_camera_preview()
        if result:
            _LOGGER.info("Camera auto-on started: %s", result.description)
        else:
            _LOGGER.error("Failed to start camera auto-on")

    async def async_turn_off(self) -> None:
        """Stop live camera stream."""
        await self.coordinator.stop_stream()

    @property
    def is_streaming(self) -> bool:
        """Return True if live video stream is active."""
        stream = self.coordinator.stream_session
        return bool(stream and stream.is_active)

    @property
    def is_on(self) -> bool:
        """Return True if the camera can serve an image."""
        if self.is_streaming:
            return True
        return self.coordinator.last_photo is not None
