"""Firebase Cloud Messaging notification listener for Fermax Blue."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from firebase_messaging import FcmPushClient
from firebase_messaging.fcmregister import FcmRegister, FcmRegisterConfig

from .const import (
    FIREBASE_API_KEY,
    FIREBASE_APP_ID,
    FIREBASE_PROJECT_ID,
    FIREBASE_SENDER_ID,
)

_LOGGER = logging.getLogger(__name__)


class FermaxNotificationListener:
    """Manages Firebase Cloud Messaging for doorbell push notifications."""

    def __init__(
        self,
        storage_path: Path,
        notification_callback: Callable[[dict[str, Any], str], None],
    ) -> None:
        self._storage_path = storage_path
        self._notification_callback = notification_callback
        self._credentials: dict | None = None
        self._push_client: FcmPushClient | None = None
        self._fcm_config = FcmRegisterConfig(
            project_id=FIREBASE_PROJECT_ID,
            app_id=FIREBASE_APP_ID,
            api_key=FIREBASE_API_KEY,
            messaging_sender_id=str(FIREBASE_SENDER_ID),
        )
        self._credentials_file = storage_path / "fermax_fcm_credentials.json"

    @property
    def fcm_token(self) -> str | None:
        """Return the GCM/FCM token for this client."""
        if self._credentials:
            return self._credentials.get("gcm", {}).get("token")
        return None

    def _on_credentials_updated(self, new_creds: dict) -> None:
        """Handle FCM credentials update."""
        self._credentials = new_creds
        self._save_credentials()

    def _save_credentials(self) -> None:
        """Save FCM credentials to disk."""
        if self._credentials:
            self._credentials_file.write_text(
                json.dumps(self._credentials, indent=2)
            )

    def _load_credentials(self) -> dict | None:
        """Load FCM credentials from disk."""
        if self._credentials_file.exists():
            try:
                return json.loads(self._credentials_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return None

    def _on_notification(
        self,
        notification: dict[str, Any],
        persistent_id: str,
        obj: Any = None,
    ) -> None:
        """Handle incoming FCM notification."""
        _LOGGER.info("Received FCM notification: %s", persistent_id)
        _LOGGER.debug("Notification data: %s", notification)
        self._notification_callback(notification, persistent_id)

    async def register(self) -> str | None:
        """Register with Firebase and return the FCM token."""
        self._credentials = self._load_credentials()

        if not self._credentials:
            _LOGGER.info("Registering new FCM client with Firebase")
            registerer = FcmRegister(
                config=self._fcm_config,
                credentials_updated_callback=self._on_credentials_updated,
            )
            self._credentials = await registerer.register()
            self._save_credentials()
            _LOGGER.info("FCM registration complete")

        return self.fcm_token

    async def start(self) -> None:
        """Start listening for push notifications."""
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
        )

        await self._push_client.start()
        _LOGGER.info("FCM notification listener started")

    async def stop(self) -> None:
        """Stop listening for push notifications."""
        if self._push_client:
            await self._push_client.stop()
            self._push_client = None
            _LOGGER.info("FCM notification listener stopped")

    @property
    def is_started(self) -> bool:
        """Return True if the listener is running."""
        return self._push_client is not None and self._push_client.is_started()
