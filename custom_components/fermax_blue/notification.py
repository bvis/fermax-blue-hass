"""Firebase Cloud Messaging notification listener for Fermax Blue."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
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

# Hardening against firebase_messaging reconnect storms (issue #12): a poisoned
# StreamReader re-raises the same exception object every iteration of _listen,
# growing its traceback while logging.exception formats it inside the HA event
# loop — on Python 3.14 that is quadratic and pegs the core until the watchdog
# kills HA. Bound the failure (abort + delayed restart) and defuse the log bomb
# (rate-limit filter strips exc_info before formatting).
FCM_UPSTREAM_LOGGER = "firebase_messaging.fcmpushclient"
FCM_ABORT_SEQUENTIAL_ERROR_COUNT = 3
FCM_RESTART_BACKOFF_INITIAL = 300.0  # seconds until the first restart attempt
FCM_RESTART_BACKOFF_MAX = 900.0  # ceiling for the doubled delay
FCM_EXC_LOG_LIMIT = 3  # full tracebacks allowed per window
FCM_EXC_LOG_WINDOW = 300.0  # seconds


class _FcmExcInfoRateLimitFilter(logging.Filter):
    """Strip tracebacks from upstream FCM records after a burst.

    Filters run before formatting, so stripping ``exc_info`` here prevents
    `logging.exception` calls in firebase_messaging's listen loop from
    formatting an ever-growing traceback chain on every iteration. The record
    itself is always kept as a one-line message.
    """

    def __init__(
        self,
        limit: int = FCM_EXC_LOG_LIMIT,
        window: float = FCM_EXC_LOG_WINDOW,
    ) -> None:
        super().__init__()
        self._limit = limit
        self._window = window
        self._timestamps: deque[float] = deque()

    def filter(self, record: logging.LogRecord) -> bool:
        # exc_info=True outside an except block yields the truthy (None, None,
        # None) tuple — no traceback to strip, so it must not consume budget.
        if not record.exc_info or record.exc_info[0] is None:
            return True

        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] > self._window:
            self._timestamps.popleft()

        if len(self._timestamps) < self._limit:
            self._timestamps.append(now)
            return True

        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        record.msg = f"{record.msg} (traceback suppressed: rate limit exceeded)"
        return True


def _install_fcm_log_rate_limit() -> None:
    """Attach the traceback rate-limit filter to the upstream FCM logger (idempotent)."""
    upstream = logging.getLogger(FCM_UPSTREAM_LOGGER)
    if not any(isinstance(f, _FcmExcInfoRateLimitFilter) for f in upstream.filters):
        upstream.addFilter(_FcmExcInfoRateLimitFilter())


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
        self._restart_backoff = FCM_RESTART_BACKOFF_INITIAL
        self._restart_at: float | None = None

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

        _install_fcm_log_rate_limit()

        # Bounded abort: let the upstream client give up after a few sequential
        # errors instead of spinning forever on a poisoned reader; the watchdog
        # restarts it with delayed backoff via ensure_running().
        self._push_client = FcmPushClient(
            callback=self._on_notification,
            fcm_config=self._fcm_config,
            credentials=self._credentials,
            credentials_updated_callback=self._on_credentials_updated,
            config=FcmPushClientConfig(
                abort_on_sequential_error_count=FCM_ABORT_SEQUENTIAL_ERROR_COUNT
            ),
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
        """Reanimate the FCM listener if it has stopped, with delayed backoff.

        The upstream client aborts the receiver after repeated transport errors
        and never reconnects on its own; this is meant to be polled by a
        watchdog. Restarts are deferred by a doubling delay (5 → 15 min cap) so
        a persistent server-side failure becomes "push down for a while"
        instead of a reconnect storm (issue #12). Serialised via
        ``_lifecycle_lock`` so overlapping ticks cannot spawn parallel
        ``FcmPushClient`` instances.

        Returns True when the listener is running, or when a restart attempt
        was successfully initiated (the client may still be connecting).
        """
        if self.is_started:
            self._restart_backoff = FCM_RESTART_BACKOFF_INITIAL
            self._restart_at = None
            return True

        async with self._lifecycle_lock:
            if self.is_started:
                return True

            if not self._credentials:
                return False

            now = time.monotonic()
            if self._restart_at is None:
                self._restart_at = now + self._restart_backoff
                # INFO, not WARNING: is_started() is also False during
                # seconds-long transient states (RESETTING, STARTING_*), and a
                # healthy next tick clears this schedule silently. WARNING is
                # reserved for the restart actually firing below.
                _LOGGER.info(
                    "FCM listener is not running; restart scheduled in %.0f seconds "
                    "(cleared automatically if the listener recovers on its own)",
                    self._restart_backoff,
                )
                return False

            if now < self._restart_at:
                return False

            self._restart_at = None
            self._restart_backoff = min(self._restart_backoff * 2, FCM_RESTART_BACKOFF_MAX)

            _LOGGER.warning("FCM listener restart backoff elapsed; restarting it")
            if self._push_client is not None:
                with contextlib.suppress(ConnectionError, OSError, RuntimeError):
                    await self._push_client.stop()
                self._push_client = None

            try:
                await self._start_locked()
            except Exception:
                # Catch everything: the register() path can raise types beyond
                # connection errors, and the watchdog gathers with
                # return_exceptions=True and discards results — anything
                # escaping here would be swallowed with no log line at all.
                _LOGGER.exception("Failed to restart FCM listener")
                return False
            # The client is usually still connecting (STARTING_*) here, so
            # report the success of the start call rather than is_started.
            return True
