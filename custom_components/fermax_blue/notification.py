"""Firebase Cloud Messaging notification listener for Fermax Blue."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from firebase_messaging import FcmPushClient, FcmPushClientConfig
from firebase_messaging.fcmregister import FcmRegister, FcmRegisterConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_FCM_STORAGE_VERSION = 1
_FCM_STORAGE_KEY = f"{DOMAIN}_fcm_credentials"

_SENSITIVE_LOG_KEYS = frozenset(
    {"FermaxToken", "fermaxOauthToken", "appToken", "token", "fcm_token"}
)


def _redact_notification(data: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *data* with sensitive values replaced by '***'."""
    result: dict[str, Any] = {}
    for k, v in data.items():
        if k in _SENSITIVE_LOG_KEYS:
            result[k] = "***"
        elif isinstance(v, dict):
            result[k] = _redact_notification(v)
        else:
            result[k] = v
    return result


class FermaxNotificationListener:
    """Manages Firebase Cloud Messaging for doorbell push notifications."""

    def __init__(
        self,
        hass: HomeAssistant,
        notification_callback: Callable[[dict[str, Any], str], None],
        *,
        firebase_api_key: str,
        firebase_sender_id: int | str,
        firebase_app_id: str,
        firebase_project_id: str,
        firebase_package_name: str,
    ) -> None:
        self._hass = hass
        self._notification_callback = notification_callback
        self._credentials: dict | None = None
        self._push_client: FcmPushClient | None = None
        self._fcm_config = FcmRegisterConfig(
            project_id=firebase_project_id,
            app_id=firebase_app_id,
            api_key=firebase_api_key,
            messaging_sender_id=str(firebase_sender_id),
            bundle_id=firebase_package_name,
        )
        self._store: Store = Store(hass, _FCM_STORAGE_VERSION, _FCM_STORAGE_KEY)
        self._lifecycle_lock = asyncio.Lock()

    @property
    def fcm_token(self) -> str | None:
        """Return the FCM registration token for push notifications."""
        if self._credentials:
            # Prefer FCM v2 registration token over legacy GCM token
            fcm_reg = self._credentials.get("fcm", {}).get("registration", {})
            token: str | None = fcm_reg.get("token")
            if token:
                return token
            # Fallback to legacy GCM token
            gcm_token: str | None = self._credentials.get("gcm", {}).get("token")
            return gcm_token
        return None

    def _on_credentials_updated(self, new_creds: dict) -> None:
        """Handle FCM credentials update (sync callback from firebase_messaging).

        Schedules an async save via the HA event loop so we never perform
        blocking I/O inside a sync callback.
        """
        self._credentials = new_creds
        self._hass.loop.call_soon_threadsafe(
            self._hass.async_create_task,
            self._save_credentials(),
        )

    async def _save_credentials(self) -> None:
        """Persist FCM credentials via HA Store (non-blocking, within .storage/)."""
        if self._credentials:
            await self._store.async_save(self._credentials)

    async def _load_credentials(self) -> dict | None:
        """Load FCM credentials from HA Store."""
        return await self._store.async_load()

    def _on_notification(
        self,
        notification: dict[str, Any],
        persistent_id: str,
        obj: Any = None,  # noqa: V107
    ) -> None:
        """Handle incoming FCM notification."""
        _LOGGER.debug("Received FCM notification (persistent_id omitted)")
        _LOGGER.debug("Notification data: %s", _redact_notification(notification))
        self._notification_callback(notification, persistent_id)

    async def register(self) -> str | None:
        """Register with Firebase and return the FCM token."""
        self._credentials = await self._load_credentials()

        if not self._credentials:
            _LOGGER.info("Registering new FCM client with Firebase")
            registerer = FcmRegister(
                config=self._fcm_config,
                credentials_updated_callback=self._on_credentials_updated,
            )
            self._credentials = await registerer.register()
            await self._save_credentials()
            _LOGGER.info("FCM registration complete")

        return self.fcm_token

    async def start(self) -> None:
        """Start listening for push notifications."""
        async with self._lifecycle_lock:
            await self._start_locked()

    async def _start_locked(self) -> None:
        """Inner ``start`` that assumes the lifecycle lock is already held."""
        if not self._credentials:
            await self.register()

        if not self._credentials:
            _LOGGER.error("Cannot start listener: no FCM credentials")
            return

        self._push_client = FcmPushClient(
            callback=self._on_notification,
            fcm_config=self._fcm_config,
            credentials=self._credentials,
            credentials_updated_callback=self._on_credentials_updated,
            config=FcmPushClientConfig(abort_on_sequential_error_count=None),
        )

        await self._push_client.start()
        _LOGGER.info("FCM notification listener started")

    async def stop(self) -> None:
        """Stop listening for push notifications."""
        async with self._lifecycle_lock:
            if self._push_client:
                await self._push_client.stop()
                self._push_client = None
                _LOGGER.info("FCM notification listener stopped")

    @property
    def is_started(self) -> bool:
        """Return True if the listener is running."""
        return self._push_client is not None and self._push_client.is_started()

    async def ensure_running(self) -> bool:
        """Reanimate the FCM listener if it has stopped.

        The upstream client aborts the receiver after repeated transport errors
        and never reconnects on its own. This method is meant to be polled by a
        watchdog so the listener stays alive across network glitches.

        Serialised via ``_lifecycle_lock`` so overlapping watchdog ticks cannot
        spawn parallel ``FcmPushClient`` instances while a slow ``start()`` is
        still handshaking.

        Returns True when the listener is running after the call.
        """
        async with self._lifecycle_lock:
            if self.is_started:
                return True

            if not self._credentials:
                return False

            _LOGGER.warning("FCM listener is not running; restarting it")
            if self._push_client is not None:
                try:
                    await self._push_client.stop()
                except (ConnectionError, OSError, RuntimeError) as err:
                    _LOGGER.debug("Ignoring error during dead client teardown: %s", err)
                self._push_client = None

            # The persisted FCM token is reused, so the Fermax-side
            # ``register_app_token`` mapping stays valid; no re-registration needed.
            try:
                await self._start_locked()
            except (ConnectionError, OSError, RuntimeError):
                _LOGGER.exception("Failed to restart FCM listener")
                return False
            return self.is_started
