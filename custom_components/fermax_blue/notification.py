"""Firebase Cloud Messaging notification listener for Fermax Blue."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from base64 import urlsafe_b64decode
from collections import deque
from collections.abc import Callable
from typing import Any

import http_ece
from cryptography.hazmat.primitives.serialization import load_der_private_key
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
FCM_STOP_TIMEOUT = 5.0  # wait for the client tasks to finish on stop
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


def _b64_pad(value: str) -> str:
    """Right-pad a urlsafe base64 string with '=' to a length multiple of 4."""
    return value + "=" * (-len(value) % 4)


def _header_param(value: str, name: str) -> str:
    """Return parameter *name* from an upstream-sliced ``;``-separated header value.

    Upstream slices the leading ``dh=`` / ``salt=`` off by length and keeps the
    rest, so a VAPID-signed push (``dh=<key>; p256ecdsa=<key>``) reaches us as
    ``<key>; p256ecdsa=<key>``. The first segment is the wanted one unless the
    parameter shows up labelled further on.
    """
    segments = [segment.strip() for segment in value.split(";")]
    for segment in segments:
        if segment.startswith(f"{name}="):
            return segment[len(name) + 1 :]
    return segments[0]


def _decrypt_push(
    credentials: dict[str, dict[str, str]],
    crypto_key_str: str,
    salt_str: str,
    raw_data: bytes,
) -> bytes:
    """Decrypt one push for our own FcmPushClient (issues #21, #25, #117).

    Installed on our client instance, never on the shared class: another
    integration patching ``FcmPushClient`` does not see our pushes and we do
    not see theirs. Upstream's version breaks three ways, all fixed here:

    1. The ``crypto-key`` (dh=) and ``encryption`` (salt=) values arrive
       unpadded, legal per RFC 8188/8291 (``binascii.Error``, #21).
    2. Fermax signs its pushes (``dh=<key>; p256ecdsa=<key>``) and upstream
       decodes the whole value as the key (``Invalid EC key``, #117).
    3. Any exception escapes before the message is acked, so MCS redelivers
       it on every reconnect and the client shuts down each time (#25).

    A failure returns ``b""``: upstream then acks and skips that one message
    and the listener stays alive for the next push.
    """
    try:
        keys = credentials["keys"]
        private_key = load_der_private_key(
            urlsafe_b64decode(_b64_pad(keys["private"])), password=None
        )
        return http_ece.decrypt(  # type: ignore[no-any-return]
            raw_data,
            salt=urlsafe_b64decode(_b64_pad(_header_param(salt_str, "salt"))),
            private_key=private_key,
            dh=urlsafe_b64decode(_b64_pad(_header_param(crypto_key_str, "dh"))),
            version="aesgcm",
            auth_secret=urlsafe_b64decode(_b64_pad(keys["secret"])),
        )
    except Exception:
        _LOGGER.warning(
            "Skipping an undecryptable FCM push; FCM listener kept alive",
            exc_info=True,
        )
        return b""


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
        # Set by stop(): the watchdog must not revive a listener turned off on purpose
        self._stopped = False

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
            self._stopped = False
            if self.is_started:
                return
            await self._close_client()
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
        # Upstream calls self._decrypt_raw_data, so the instance attribute wins
        # and the class other integrations may patch is left alone.
        self._push_client._decrypt_raw_data = _decrypt_push  # type: ignore[method-assign]

        await self._push_client.start()
        _LOGGER.info("FCM notification listener started")

    async def stop(self) -> None:
        """Stop listening for push notifications."""
        async with self._lifecycle_lock:
            self._stopped = True
            if self._push_client:
                await self._close_client()
                _LOGGER.info("FCM notification listener stopped")

    async def _close_client(self) -> None:
        """Stop the current client, if any, and wait for its tasks to end."""
        client, self._push_client = self._push_client, None
        if client is None:
            return
        # The client cancels its tasks without waiting, and the reader's cleanup
        # then waits for a TLS close the server can take tens of seconds to
        # send: drop the connection first and wait for them.
        if client.writer:
            client.writer.transport.abort()
        tasks = list(client.tasks)
        await client.stop()
        if tasks:
            await asyncio.wait(tasks, timeout=FCM_STOP_TIMEOUT)

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
        if self._stopped:
            return False

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
            with contextlib.suppress(ConnectionError, OSError, RuntimeError):
                await self._close_client()

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
