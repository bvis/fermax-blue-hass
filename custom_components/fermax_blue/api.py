"""Fermax Blue API client."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from .const import (
    APP_HEADERS,
    FERMAX_AUTH_BASIC,
    FERMAX_AUTH_URL,
    FERMAX_BASE_URL,
)

_LOGGER = logging.getLogger(__name__)

OAUTH_TIMEOUT = 15.0
API_TIMEOUT = 10.0
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1.0


@dataclass
class AccessDoor:
    """Represents a door that can be opened."""

    name: str
    title: str
    access_id: dict
    visible: bool


@dataclass
class DeviceInfo:
    """Device information from Fermax."""

    device_id: str
    connection_state: str
    status: str
    family: str
    device_type: str
    subtype: str
    unit_number: int
    photocaller: bool
    streaming_mode: str
    is_monitor: bool
    wireless_signal: int


@dataclass
class Pairing:
    """Represents a paired device."""

    device_id: str
    tag: str
    installation_id: str
    access_doors: dict[str, AccessDoor] = field(default_factory=dict)


@dataclass
class CallLogEntry:
    """A call log entry with optional photo."""

    call_id: str
    device_id: str
    call_date: datetime
    photo_id: str | None = None
    answered: bool = False


@dataclass
class DivertResponse:
    """Response from autoOn/changeVideoSource calls."""

    reason: str
    divert_service: str
    code: float
    description: str
    directed_to: str
    local_address: str = ""
    remote_address: str = ""


@dataclass
class OpeningRecord:
    """A door opening history entry."""

    timestamp: str
    user: str
    door: str
    guest_email: str | None = None


def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception is transient and worth retrying."""
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


class FermaxAuthError(Exception):
    """Authentication error."""


class FermaxApiError(Exception):
    """Generic API error."""


class FermaxBlueApi:
    """Client for the Fermax Blue API."""

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._access_token: str | None = None
        self._token_expires_at: float = 0
        self._pairings: list[Pairing] = []
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create a persistent HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=API_TIMEOUT)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    def is_authenticated(self) -> bool:
        """Return True if we have a valid token."""
        return self._access_token is not None and time.time() < self._token_expires_at

    def _get_auth_headers(self) -> dict:
        """Get headers for authenticated API requests."""
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            **APP_HEADERS,
        }

    async def authenticate(self) -> str:
        """Authenticate with Fermax Blue and return access token."""
        username = quote(self._username)
        password = quote(self._password)
        payload = f"grant_type=password&password={password}&username={username}"

        headers = {
            "Authorization": FERMAX_AUTH_BASIC,
            "Content-Type": "application/x-www-form-urlencoded",
            **APP_HEADERS,
        }

        client = await self._get_client()
        response = await client.post(FERMAX_AUTH_URL, headers=headers, content=payload)

        data = response.json()
        if "error" in data:
            raise FermaxAuthError(data.get("error_description", data["error"]))

        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 3600) - 60
        _LOGGER.debug("Authenticated with Fermax Blue")
        return self._access_token

    async def _ensure_authenticated(self) -> None:
        """Ensure we have a valid token."""
        if not self.is_authenticated:
            await self.authenticate()

    async def _api_request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated request with retry on transient errors."""
        await self._ensure_authenticated()
        client = await self._get_client()
        url = f"{FERMAX_BASE_URL}{path}"
        headers = self._get_auth_headers()
        last_exc: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response: httpx.Response = await getattr(client, method)(
                    url, headers=headers, **kwargs
                )
                response.raise_for_status()
                return response
            except (
                httpx.HTTPStatusError,
                httpx.ConnectError,
                httpx.TimeoutException,
            ) as exc:
                last_exc = exc
                if not _is_retryable(exc) or attempt >= MAX_RETRIES:
                    raise
                delay = RETRY_BACKOFF_BASE * (2**attempt)
                _LOGGER.debug(
                    "Retryable error on %s %s (attempt %d/%d), retrying in %.1fs: %s",
                    method.upper(),
                    path,
                    attempt + 1,
                    MAX_RETRIES,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        # Should not reach here, but satisfy type checker
        raise last_exc  # type: ignore[misc]

    async def _api_get(self, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated GET request with retry."""
        return await self._api_request("get", path, **kwargs)

    async def _api_post(self, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated POST request with retry."""
        return await self._api_request("post", path, **kwargs)

    async def _api_put(self, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated PUT request with retry."""
        return await self._api_request("put", path, **kwargs)

    async def get_pairings(self) -> list[Pairing]:
        """Get all paired devices."""
        response = await self._api_get("/pairing/api/v4/pairings/me")
        pairings = []

        for item in response.json():
            access_doors = {}
            for door_name, door_data in item.get("accessDoorMap", {}).items():
                access_doors[door_name] = AccessDoor(
                    name=door_name,
                    title=door_data.get("title", door_name),
                    access_id=door_data["accessId"],
                    visible=door_data.get("visible", False),
                )

            pairings.append(
                Pairing(
                    device_id=item["deviceId"],
                    tag=item.get("tag", ""),
                    installation_id=item.get("installationId", ""),
                    access_doors=access_doors,
                )
            )

        self._pairings = pairings
        return pairings

    async def get_device_info(self, device_id: str) -> DeviceInfo:
        """Get device information."""
        response = await self._api_get(f"/deviceaction/api/v1/device/{device_id}")
        data = response.json()

        return DeviceInfo(
            device_id=data["deviceId"],
            connection_state=data.get("connectionState", "Unknown"),
            status=data.get("status", "Unknown"),
            family=data.get("family", "Unknown"),
            device_type=data.get("type", "Unknown"),
            subtype=data.get("subtype", ""),
            unit_number=data.get("unitNumber", 0),
            photocaller=data.get("photocaller", False),
            streaming_mode=data.get("streamingMode", ""),
            is_monitor=data.get("isMonitor", False),
            wireless_signal=data.get("wirelessSignal", 0),
        )

    async def open_door(self, device_id: str, access_id: dict) -> bool:
        """Open a door."""
        try:
            await self._api_post(
                f"/deviceaction/api/v1/device/{device_id}/directed-opendoor",
                content=json.dumps(access_id),
            )
            return True
        except httpx.HTTPStatusError:
            return False

    async def get_call_log(self, fcm_token: str) -> list[CallLogEntry]:
        """Get call log entries."""
        try:
            response = await self._api_get(
                "/callmanager/api/v1/callregistry/participant",
                params={"appToken": fcm_token, "callRegistryType": "all"},
            )
        except httpx.HTTPStatusError:
            return []

        entries = []
        for item in response.json():
            entries.append(
                CallLogEntry(
                    call_id=item.get("id", ""),
                    device_id=item.get("deviceId", ""),
                    call_date=datetime.fromisoformat(
                        item.get("callDate", datetime.now(UTC).isoformat())
                    ),
                    photo_id=item.get("photoId"),
                    answered=item.get("answered", False),
                )
            )
        return entries

    async def get_call_photo(self, photo_id: str) -> bytes | None:
        """Get a photo from a call."""
        try:
            response = await self._api_get(
                "/callmanager/api/v1/photocall",
                params={"photoId": photo_id},
            )
        except httpx.HTTPStatusError:
            return None

        try:
            data = response.json()
            image_data = data.get("image", {}).get("data")
            if image_data:
                return base64.b64decode(image_data)
        except Exception:
            _LOGGER.debug("Failed to decode call photo", exc_info=True)
        return None

    async def auto_on(self, device_id: str, fcm_token: str) -> DivertResponse | None:
        """Start camera preview (auto-on) without a doorbell ring.

        This triggers the intercom to start streaming video to the app/client.
        The signaling server URL and room ID will arrive via push notification.
        """
        payload = {
            "directedToBluestream": fcm_token,
            "directedToSippo": None,
            "callAs": None,
        }

        try:
            response = await self._api_post(
                f"/deviceaction/api/v2/device/{device_id}/autoon",
                json=payload,
            )
        except httpx.HTTPStatusError as exc:
            _LOGGER.error("autoOn failed: %s", exc)
            return None

        data = response.json()
        additional = data.get("additional_info", {})
        local_info = additional.get("local", {})
        remote_info = additional.get("remote", {})

        return DivertResponse(
            reason=data.get("reason", ""),
            divert_service=data.get("divertService", ""),
            code=data.get("code", 0),
            description=data.get("description", ""),
            directed_to=data.get("directedTo", ""),
            local_address=local_info.get("address", ""),
            remote_address=remote_info.get("address", ""),
        )

    async def change_video_source(
        self, device_id: str, fcm_token: str
    ) -> DivertResponse | None:
        """Request a video source change on the intercom."""
        payload = {
            "directedToBluestream": fcm_token,
            "directedToSippo": None,
            "callAs": None,
        }

        try:
            response = await self._api_post(
                f"/deviceaction/api/v2/device/{device_id}/changevideosource",
                json=payload,
            )
        except httpx.HTTPStatusError:
            return None

        data = response.json()
        return DivertResponse(
            reason=data.get("reason", ""),
            divert_service=data.get("divertService", ""),
            code=data.get("code", 0),
            description=data.get("description", ""),
            directed_to=data.get("directedTo", ""),
        )

    async def register_app_token(self, fcm_token: str, active: bool = True) -> bool:
        """Register FCM token with Fermax for push notifications."""
        payload = {
            "appTokenId": fcm_token,
            "token": fcm_token,
            "os": "ANDROID",
            "appVersion": "4.3.0",
            "appBuild": 1,
            "phoneModel": "HA-Integration",
            "phoneOS": "14.0",
            "locale": "en_US",
            "active": active,
        }

        try:
            await self._api_post(
                "/notification/api/v2/apptoken",
                json=payload,
            )
            return True
        except httpx.HTTPStatusError:
            return False

    async def get_dnd_status(self, device_id: str, fcm_token: str) -> bool:
        """Get Do Not Disturb status for a device."""
        response = await self._api_get(
            "/notification/api/v1/mutedevice/me",
            params={"deviceId": device_id, "token": fcm_token},
        )
        data: dict = response.json()
        return bool(data.get("muted", False))

    async def set_dnd(self, device_id: str, fcm_token: str, *, enabled: bool) -> None:
        """Set Do Not Disturb status for a device."""
        await self._api_post(
            "/notification/api/v1/mutedevice/me",
            json={"deviceId": device_id, "token": fcm_token, "muted": enabled},
        )

    async def press_f1(self, device_id: str) -> None:
        """Press the F1 button on the intercom."""
        await self._api_post(
            f"/deviceaction/api/v1/device/{device_id}/f1",
        )

    async def call_guard(self, device_id: str) -> None:
        """Call the guard/concierge."""
        await self._api_post(
            f"/deviceaction/api/v1/device/{device_id}/callguard",
        )

    async def ack_notification(self, message_id: str, *, is_call: bool) -> None:
        """Acknowledge a notification (call or info)."""
        path = (
            "/callmanager/api/v1/message/ack"
            if is_call
            else "/notification/api/v1/message/ack"
        )
        body = {"attended": True, "fcmMessageId": message_id}
        try:
            await self._api_post(path, json=body)
        except httpx.HTTPStatusError:
            _LOGGER.debug("Failed to ack notification %s", message_id)

    async def set_photo_caller(self, device_id: str, *, enabled: bool) -> None:
        """Enable or disable photo caller on a device."""
        await self._api_put(
            f"/deviceaction/api/v1/{device_id}/photocaller",
            params={"value": "true" if enabled else "false"},
        )

    async def get_opening_history(
        self, device_id: str, user_id: str
    ) -> list[OpeningRecord]:
        """Get door opening history."""
        try:
            response = await self._api_get(
                "/rexistro/api/v1/opendoorregistry",
                params={"deviceId": device_id, "userId": user_id},
            )
        except Exception:
            _LOGGER.debug("Failed to get opening history", exc_info=True)
            return []

        entries = response.json().get("entries", [])
        return [
            OpeningRecord(
                timestamp=entry["timestamp"],
                user=entry["user"],
                door=entry["door"],
                guest_email=entry.get("guestEmail"),
            )
            for entry in entries
        ]
