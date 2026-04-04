"""Data coordinator for Fermax Blue integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import DeviceInfo, DivertResponse, FermaxBlueApi, Pairing
from .const import DOMAIN, SIGNAL_CALL_ENDED, SIGNAL_DOORBELL_RING
from .notification import FermaxNotificationListener

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(minutes=5)


class FermaxBlueCoordinator(DataUpdateCoordinator):
    """Coordinate data updates and notifications for a Fermax Blue device."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: FermaxBlueApi,
        pairing: Pairing,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{pairing.device_id}",
            update_interval=UPDATE_INTERVAL,
        )
        self.api = api
        self.pairing = pairing
        self.device_info: DeviceInfo | None = None
        self.notification_listener: FermaxNotificationListener | None = None
        self._last_photo: bytes | None = None
        self._last_photo_id: str | None = None
        self._doorbell_ringing: bool = False
        self._camera_active: bool = False
        self._last_divert_response: DivertResponse | None = None
        self._photo_fetch_pending: bool = False

    @property
    def last_photo(self) -> bytes | None:
        """Return the last captured photo."""
        return self._last_photo

    @property
    def doorbell_ringing(self) -> bool:
        """Return True if the doorbell is currently ringing."""
        return self._doorbell_ringing

    @property
    def camera_active(self) -> bool:
        """Return True if camera preview is active."""
        return self._camera_active

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the API.

        Only fetches device info on each poll (1 API call per 5 min).
        Call log and photos are only fetched after a doorbell ring event
        to minimize unnecessary API requests.
        """
        device_info = await self.api.get_device_info(self.pairing.device_id)
        self.device_info = device_info

        # Only fetch call log/photos after a doorbell ring (not every poll)
        if (
            self._photo_fetch_pending
            and self.notification_listener
            and self.notification_listener.fcm_token
        ):
            self._photo_fetch_pending = False
            try:
                call_log = await self.api.get_call_log(
                    self.notification_listener.fcm_token
                )
                if call_log:
                    latest = max(call_log, key=lambda c: c.call_date)
                    if latest.photo_id and latest.photo_id != self._last_photo_id:
                        photo = await self.api.get_call_photo(latest.photo_id)
                        if photo:
                            self._last_photo = photo
                            self._last_photo_id = latest.photo_id
            except Exception:
                _LOGGER.debug("Failed to fetch call log/photo", exc_info=True)

        return {
            "device_id": device_info.device_id,
            "connection_state": device_info.connection_state,
            "status": device_info.status,
            "family": device_info.family,
            "type": device_info.device_type,
            "subtype": device_info.subtype,
            "unit_number": device_info.unit_number,
            "photocaller": device_info.photocaller,
            "streaming_mode": device_info.streaming_mode,
            "is_monitor": device_info.is_monitor,
            "wireless_signal": device_info.wireless_signal,
        }

    async def setup_notifications(self, storage_path: Path) -> None:
        """Set up the FCM notification listener."""
        self.notification_listener = FermaxNotificationListener(
            storage_path=storage_path,
            notification_callback=self._handle_notification,
        )

        fcm_token = await self.notification_listener.register()
        if fcm_token:
            await self.api.register_app_token(fcm_token, active=True)
            await self.notification_listener.start()
            _LOGGER.info(
                "Notification listener started for device %s",
                self.pairing.device_id,
            )

    async def stop_notifications(self) -> None:
        """Stop the notification listener."""
        if self.notification_listener:
            if self.notification_listener.fcm_token:
                await self.api.register_app_token(
                    self.notification_listener.fcm_token, active=False
                )
            await self.notification_listener.stop()

    @callback
    def _handle_notification(self, notification: dict, persistent_id: str) -> None:
        """Handle an incoming FCM doorbell notification."""
        _LOGGER.info(
            "Doorbell notification for %s: %s",
            self.pairing.device_id,
            notification,
        )

        self._doorbell_ringing = True
        self._photo_fetch_pending = True

        # Extract door key from notification if available
        door_key = notification.get("accessDoorKey", "GENERAL")
        dispatcher_send(
            self.hass,
            SIGNAL_DOORBELL_RING.format(self.pairing.device_id, door_key),
        )

        # Auto-reset ringing state after 30 seconds
        async def reset_ringing():
            await asyncio.sleep(30)
            self._doorbell_ringing = False
            dispatcher_send(
                self.hass,
                SIGNAL_CALL_ENDED.format(self.pairing.device_id),
            )
            self.async_set_updated_data(self.data)

        self.hass.async_create_task(reset_ringing())

        # Trigger a data refresh to get any new photos
        self.hass.async_create_task(self.async_request_refresh())

    async def open_door(self, door_name: str = "GENERAL") -> bool:
        """Open a specific door."""
        door = self.pairing.access_doors.get(door_name)
        if not door:
            # Try first visible door
            for d in self.pairing.access_doors.values():
                if d.visible:
                    door = d
                    break

        if not door:
            _LOGGER.error("No accessible door found for %s", door_name)
            return False

        return await self.api.open_door(self.pairing.device_id, door.access_id)

    async def start_camera_preview(self) -> DivertResponse | None:
        """Start camera preview (auto-on) to view the intercom camera.

        This initiates a video stream from the intercom without requiring
        a doorbell ring. The stream parameters will arrive via push notification.
        """
        if not self.notification_listener or not self.notification_listener.fcm_token:
            _LOGGER.error("Cannot start camera: no FCM token available")
            return None

        result = await self.api.auto_on(
            self.pairing.device_id,
            self.notification_listener.fcm_token,
        )

        if result:
            self._camera_active = True
            self._last_divert_response = result
            _LOGGER.info(
                "Camera preview started: %s (%s)",
                result.reason,
                result.description,
            )

            # Auto-deactivate after 90 seconds (matching app behavior)
            async def deactivate_camera():
                await asyncio.sleep(90)
                self._camera_active = False
                self.async_set_updated_data(self.data)

            self.hass.async_create_task(deactivate_camera())
            self.async_set_updated_data(self.data)

        return result

    async def change_video_source(self) -> DivertResponse | None:
        """Request a video source change on the intercom."""
        if not self.notification_listener or not self.notification_listener.fcm_token:
            return None

        return await self.api.change_video_source(
            self.pairing.device_id,
            self.notification_listener.fcm_token,
        )
