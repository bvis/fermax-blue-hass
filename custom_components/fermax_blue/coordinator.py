"""Data coordinator for Fermax Blue integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    DeviceInfo,
    DivertResponse,
    FermaxApiError,
    FermaxAuthError,
    FermaxBlueApi,
    OpeningRecord,
    Pairing,
)
from .const import DOMAIN, SIGNAL_CALL_ENDED, SIGNAL_DOORBELL_RING
from .notification import FermaxNotificationListener

_LOGGER = logging.getLogger(__name__)

DOORBELL_RESET_SECONDS = 30
CAMERA_TIMEOUT_SECONDS = 90


class FermaxBlueCoordinator(DataUpdateCoordinator):
    """Coordinate data updates and notifications for a Fermax Blue device."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: FermaxBlueApi,
        pairing: Pairing,
        scan_interval: int = 5,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{pairing.device_id}",
            update_interval=timedelta(minutes=scan_interval),
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
        self._doorbell_reset_unsub: CALLBACK_TYPE | None = None
        self._camera_timeout_unsub: CALLBACK_TYPE | None = None
        self._dnd_enabled: bool | None = None
        self._last_opening: OpeningRecord | None = None

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

    @property
    def dnd_enabled(self) -> bool | None:
        """Return DND state."""
        return self._dnd_enabled

    @property
    def last_opening(self) -> OpeningRecord | None:
        """Return the last door opening record."""
        return self._last_opening

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the API.

        Only fetches device info on each poll (1 API call per 5 min).
        Call log and photos are only fetched after a doorbell ring event
        to minimize unnecessary API requests.
        """
        try:
            device_info = await self.api.get_device_info(self.pairing.device_id)
        except (FermaxAuthError, FermaxApiError) as err:
            raise UpdateFailed(f"Error fetching device info: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

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

        # ACK the notification for reliability
        fcm_message_id = notification.get("fcmMessageId") or persistent_id
        notification_type = notification.get("FermaxNotificationType", "")
        is_call = notification_type in ("Call", "CallAttend", "CallEnd")
        self.hass.async_create_task(
            self.api.ack_notification(fcm_message_id, is_call=is_call)
        )

        self._doorbell_ringing = True
        self._photo_fetch_pending = True

        # Extract door key from notification if available
        door_key = notification.get("accessDoorKey", "GENERAL")
        dispatcher_send(
            self.hass,
            SIGNAL_DOORBELL_RING.format(self.pairing.device_id, door_key),
        )

        # Cancel previous reset timer if still pending
        if self._doorbell_reset_unsub:
            self._doorbell_reset_unsub()

        @callback
        def _reset_ringing(_now: Any) -> None:
            """Reset doorbell ringing state."""
            self._doorbell_ringing = False
            dispatcher_send(
                self.hass,
                SIGNAL_CALL_ENDED.format(self.pairing.device_id),
            )
            self.async_set_updated_data(self.data)
            self._doorbell_reset_unsub = None

        self._doorbell_reset_unsub = async_call_later(
            self.hass, DOORBELL_RESET_SECONDS, _reset_ringing
        )

        # Trigger a data refresh to get any new photos
        self.hass.async_create_task(self.async_request_refresh())

    async def open_door(self, door_name: str = "GENERAL") -> bool:
        """Open a specific door."""
        door = self.pairing.access_doors.get(door_name)
        if not door:
            for d in self.pairing.access_doors.values():
                if d.visible:
                    door = d
                    break

        if not door:
            _LOGGER.error("No accessible door found for %s", door_name)
            return False

        return await self.api.open_door(self.pairing.device_id, door.access_id)

    async def start_camera_preview(self) -> DivertResponse | None:
        """Start camera preview (auto-on) to view the intercom camera."""
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

            # Cancel previous camera timeout if still pending
            if self._camera_timeout_unsub:
                self._camera_timeout_unsub()

            @callback
            def _deactivate_camera(_now: Any) -> None:
                """Deactivate camera after timeout."""
                self._camera_active = False
                self.async_set_updated_data(self.data)
                self._camera_timeout_unsub = None

            self._camera_timeout_unsub = async_call_later(
                self.hass, CAMERA_TIMEOUT_SECONDS, _deactivate_camera
            )
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

    async def set_dnd(self, enabled: bool) -> None:
        """Set Do Not Disturb."""
        if not self.notification_listener or not self.notification_listener.fcm_token:
            return
        await self.api.set_dnd(
            self.pairing.device_id,
            self.notification_listener.fcm_token,
            enabled=enabled,
        )
        self._dnd_enabled = enabled

    async def press_f1(self) -> None:
        """Press F1 auxiliary button."""
        await self.api.press_f1(self.pairing.device_id)

    async def call_guard(self) -> None:
        """Call the building guard."""
        await self.api.call_guard(self.pairing.device_id)

    async def set_photo_caller(self, enabled: bool) -> None:
        """Enable or disable photo caller."""
        await self.api.set_photo_caller(self.pairing.device_id, enabled=enabled)
        # Update local state to reflect the change immediately
        if self.device_info:
            self.device_info = DeviceInfo(
                device_id=self.device_info.device_id,
                connection_state=self.device_info.connection_state,
                status=self.device_info.status,
                family=self.device_info.family,
                device_type=self.device_info.device_type,
                subtype=self.device_info.subtype,
                unit_number=self.device_info.unit_number,
                photocaller=enabled,
                streaming_mode=self.device_info.streaming_mode,
                is_monitor=self.device_info.is_monitor,
                wireless_signal=self.device_info.wireless_signal,
            )
