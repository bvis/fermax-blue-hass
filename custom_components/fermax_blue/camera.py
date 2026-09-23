"""Camera platform for Fermax Blue."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_DOORBELL_RING
from .coordinator import FermaxBlueCoordinator
from .entity import FermaxBlueEntity
from .streaming import streaming_deps_available
from .webrtc_bridge import webrtc_stream_source

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

    Supports three modes:
    - Still image: shows the last captured visitor photo (from doorbell ring)
    - Live stream: connects to the intercom camera via mediasoup and serves
      MJPEG frames in real-time (triggered by turn_on / camera preview button)
    - WebRTC: the same session published through go2rtc, with the panel audio
      and a return channel for the viewer microphone (see webrtc_bridge)
    """

    _attr_translation_key = "visitor"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        FermaxBlueEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._attr_unique_id = f"{self._device_id}_camera"

    async def async_added_to_hass(self) -> None:
        """Register for doorbell ring events."""
        await super().async_added_to_hass()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_DOORBELL_RING.format(self._device_id), self._on_doorbell_ring
            )
        )

        # Force state update so HA knows we have an image immediately
        if self.coordinator.last_photo:
            self.async_write_ha_state()

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

    async def stream_source(self) -> str | None:
        """Return the go2rtc source for this intercom's WebRTC bridge."""
        if not streaming_deps_available():
            return None
        return webrtc_stream_source(self.hass, self.coordinator.webrtc_token)

    async def async_camera_image(
        self,
        width: int | None = None,  # noqa: V107
        height: int | None = None,  # noqa: V107
    ) -> bytes | None:
        """Return the latest frame: live stream if active, else last captured frame."""
        stream = self.coordinator.stream_session
        if stream and stream.latest_frame:
            return stream.latest_frame
        return self.coordinator.last_photo

    async def handle_async_mjpeg_stream(self, request: web.Request) -> web.StreamResponse | None:
        """Serve MJPEG stream: live frames when streaming, last photo otherwise.

        The stream serves continuously — when a live stream starts or stops,
        the MJPEG output switches seamlessly between live frames and the
        static preview without dropping the connection.
        """
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "multipart/x-mixed-replace;boundary=frameboundary",
            },
        )
        await response.prepare(request)

        # While someone watches the MJPEG, the session decodes every frame
        self.coordinator.mjpeg_clients += 1
        try:
            while True:
                stream = self.coordinator.stream_session
                frame = None

                # Prefer live stream frame
                if stream and stream.latest_frame:
                    frame = stream.latest_frame
                elif self.coordinator.last_photo:
                    frame = self.coordinator.last_photo

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

                # Fast poll during stream, slow poll for static preview
                if stream and stream.is_active:
                    await asyncio.sleep(0.04)  # ~25fps
                else:
                    await asyncio.sleep(2)  # Refresh preview every 2s
        except (ConnectionResetError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self.coordinator.mjpeg_clients -= 1

        return response

    async def async_turn_on(self) -> None:
        """Start live camera stream via auto-on + mediasoup."""
        if not streaming_deps_available():
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="streaming_unavailable"
            )

        result = await self.coordinator.start_camera_preview()
        if not result:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="camera_preview_failed"
            )
        _LOGGER.info("Camera auto-on started: %s", result.description)

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
