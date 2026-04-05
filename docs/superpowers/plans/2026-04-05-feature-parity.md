# Feature Parity & Platinum Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the Fermax Blue HA integration to feature parity with the Android APK v4.3.0 and platinum-tier quality.

**Architecture:** TDD approach — tests first, then implementation. New API methods with retry logic, new entities (DND switch, photo caller switch, F1 button, call guard button, event entity, opening history sensor), plus diagnostics, options flow, and availability tracking. All changes are additive; existing functionality preserved.

**Tech Stack:** Python 3.12+, pytest, httpx, homeassistant, firebase-messaging

---

## File Map

### New files
| File | Purpose |
|------|---------|
| `custom_components/fermax_blue/event.py` | Doorbell event entity (replaces binary_sensor doorbell) |
| `custom_components/fermax_blue/diagnostics.py` | Config entry + device diagnostics |
| `tests/test_api_new.py` | Tests for new API methods + retry logic |
| `tests/test_coordinator.py` | Coordinator logic tests |
| `tests/test_entities.py` | Entity platform tests (all platforms) |
| `tests/test_diagnostics.py` | Diagnostics tests |

### Modified files
| File | Changes |
|------|---------|
| `custom_components/fermax_blue/api.py` | Add 7 new methods, retry decorator, OpeningRecord dataclass |
| `custom_components/fermax_blue/const.py` | Add event platform, new constants |
| `custom_components/fermax_blue/coordinator.py` | DND/photocaller/openings state, ACK, availability, options |
| `custom_components/fermax_blue/entity.py` | Add availability logic |
| `custom_components/fermax_blue/switch.py` | Add DND + photo caller switches |
| `custom_components/fermax_blue/button.py` | Add F1 + call guard buttons |
| `custom_components/fermax_blue/sensor.py` | Add last opening sensor |
| `custom_components/fermax_blue/binary_sensor.py` | Remove doorbell sensor (moved to event) |
| `custom_components/fermax_blue/__init__.py` | Register event platform, options flow listener |
| `custom_components/fermax_blue/config_flow.py` | Add options flow |
| `custom_components/fermax_blue/strings.json` | New translation keys |
| `tests/conftest.py` | Expanded fixtures for new API methods |
| `tests/test_api.py` | Add tests for new methods |

---

## Task 1: API Retry Logic

**Files:**
- Modify: `custom_components/fermax_blue/api.py`
- Test: `tests/test_api_new.py`

- [ ] **Step 1: Write failing tests for retry decorator**

Create `tests/test_api_new.py`:
```python
"""Tests for new API methods and retry logic."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from custom_components.fermax_blue.api import FermaxBlueApi


@pytest.fixture
def api():
    """Return a FermaxBlueApi instance."""
    return FermaxBlueApi("test@example.com", "testpass123")


@pytest.fixture
def authenticated_api(api):
    """Return an authenticated API instance."""
    api._access_token = "valid_token"
    api._token_expires_at = 9999999999
    return api


def _mock_response(status_code, **kwargs):
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code,
        request=httpx.Request("GET", "https://test.com"),
        **kwargs,
    )


class TestRetryLogic:
    """Test transient error retry behavior."""

    @pytest.mark.asyncio
    async def test_retry_on_500(self, authenticated_api):
        """Test that 500 errors are retried."""
        fail_resp = _mock_response(500, text="error")
        ok_resp = _mock_response(
            200,
            json={
                "deviceId": "dev1",
                "connectionState": "Connected",
                "status": "ACTIVATED",
                "family": "MONITOR",
                "type": "VEO-XL",
                "subtype": "WIFI",
                "unitNumber": 42,
                "photocaller": True,
                "streamingMode": "video_call",
                "isMonitor": True,
                "wirelessSignal": 4,
            },
        )

        with patch(
            "httpx.AsyncClient.get", side_effect=[fail_resp, ok_resp]
        ):
            info = await authenticated_api.get_device_info("dev1")

        assert info.device_id == "dev1"

    @pytest.mark.asyncio
    async def test_no_retry_on_401(self, authenticated_api):
        """Test that 401 errors are NOT retried (auth errors)."""
        resp = _mock_response(401, json={"error": "unauthorized"})

        with (
            patch("httpx.AsyncClient.get", return_value=resp),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await authenticated_api.get_device_info("dev1")

    @pytest.mark.asyncio
    async def test_retry_exhausted(self, authenticated_api):
        """Test that retries are exhausted after max attempts."""
        fail_resp = _mock_response(500, text="error")

        with (
            patch("httpx.AsyncClient.get", return_value=fail_resp),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await authenticated_api.get_device_info("dev1")

    @pytest.mark.asyncio
    async def test_retry_on_connection_error(self, authenticated_api):
        """Test retry on connection errors."""
        ok_resp = _mock_response(
            200,
            json={
                "deviceId": "dev1",
                "connectionState": "Connected",
                "status": "ACTIVATED",
                "family": "MONITOR",
                "type": "VEO-XL",
                "subtype": "WIFI",
                "unitNumber": 42,
                "photocaller": True,
                "streamingMode": "video_call",
                "isMonitor": True,
                "wirelessSignal": 4,
            },
        )

        with patch(
            "httpx.AsyncClient.get",
            side_effect=[httpx.ConnectError("fail"), ok_resp],
        ):
            info = await authenticated_api.get_device_info("dev1")

        assert info.device_id == "dev1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api_new.py -v`
Expected: FAIL — no retry logic exists yet.

- [ ] **Step 3: Implement retry logic in api.py**

Add to `custom_components/fermax_blue/api.py` after the imports:

```python
import asyncio
from functools import wraps

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1.0  # seconds

def _is_retryable(exc: Exception) -> bool:
    """Return True if the error is transient and worth retrying."""
    if isinstance(exc, httpx.ConnectError | httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500:
        return True
    return False
```

Then modify `_api_get` and `_api_post` to add retry:

```python
async def _api_get(self, path: str, **kwargs) -> httpx.Response:
    """Make an authenticated GET request with retry."""
    await self._ensure_authenticated()
    client = await self._get_client()
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = await client.get(
                f"{FERMAX_BASE_URL}{path}",
                headers=self._get_auth_headers(),
                **kwargs,
            )
            response.raise_for_status()
            return response
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt == MAX_RETRIES - 1:
                raise
            await asyncio.sleep(RETRY_BACKOFF_BASE * (2 ** attempt))
    raise last_exc  # unreachable, but satisfies type checker

async def _api_post(self, path: str, **kwargs) -> httpx.Response:
    """Make an authenticated POST request with retry."""
    await self._ensure_authenticated()
    client = await self._get_client()
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = await client.post(
                f"{FERMAX_BASE_URL}{path}",
                headers=self._get_auth_headers(),
                **kwargs,
            )
            response.raise_for_status()
            return response
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt == MAX_RETRIES - 1:
                raise
            await asyncio.sleep(RETRY_BACKOFF_BASE * (2 ** attempt))
    raise last_exc
```

Also add a `_api_put` method (needed for photo caller):
```python
async def _api_put(self, path: str, **kwargs) -> httpx.Response:
    """Make an authenticated PUT request with retry."""
    await self._ensure_authenticated()
    client = await self._get_client()
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = await client.put(
                f"{FERMAX_BASE_URL}{path}",
                headers=self._get_auth_headers(),
                **kwargs,
            )
            response.raise_for_status()
            return response
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt == MAX_RETRIES - 1:
                raise
            await asyncio.sleep(RETRY_BACKOFF_BASE * (2 ** attempt))
    raise last_exc
```

**Important:** Existing methods that check `response.is_success` (like `open_door`, `get_call_log`, etc.) need updating since `raise_for_status()` is now called in `_api_get`/`_api_post`. Wrap those calls with try/except where a non-200 is expected behavior (not an error). Update `open_door`:

```python
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
```

Similarly update `get_call_log`, `get_call_photo`, `auto_on`, `change_video_source`, `register_app_token` to use try/except patterns.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api_new.py tests/test_api.py -v`
Expected: ALL PASS (both new and existing tests).

---

## Task 2: New API Methods

**Files:**
- Modify: `custom_components/fermax_blue/api.py`
- Test: `tests/test_api_new.py`

- [ ] **Step 1: Write failing tests for new API methods**

Add to `tests/test_api_new.py`:

```python
from custom_components.fermax_blue.api import OpeningRecord


class TestDndApi:
    """Test Do Not Disturb API methods."""

    @pytest.mark.asyncio
    async def test_get_dnd_enabled(self, authenticated_api):
        """Test getting DND status when enabled."""
        resp = _mock_response(200, json=True)

        with patch("httpx.AsyncClient.get", return_value=resp):
            result = await authenticated_api.get_dnd_status("dev1", "fcm_tok")

        assert result is True

    @pytest.mark.asyncio
    async def test_get_dnd_disabled(self, authenticated_api):
        """Test getting DND status when disabled."""
        resp = _mock_response(200, json=False)

        with patch("httpx.AsyncClient.get", return_value=resp):
            result = await authenticated_api.get_dnd_status("dev1", "fcm_tok")

        assert result is False

    @pytest.mark.asyncio
    async def test_set_dnd(self, authenticated_api):
        """Test setting DND status."""
        resp = _mock_response(200, text="ok")

        with patch("httpx.AsyncClient.post", return_value=resp):
            await authenticated_api.set_dnd("dev1", "fcm_tok", enabled=True)
        # No exception means success


class TestF1Api:
    """Test F1 auxiliary button API."""

    @pytest.mark.asyncio
    async def test_press_f1(self, authenticated_api):
        """Test pressing F1 button."""
        resp = _mock_response(200, text="ok")

        with patch("httpx.AsyncClient.post", return_value=resp):
            await authenticated_api.press_f1("dev1")

    @pytest.mark.asyncio
    async def test_press_f1_failure(self, authenticated_api):
        """Test F1 failure raises."""
        resp = _mock_response(500, text="error")

        with (
            patch("httpx.AsyncClient.post", return_value=resp),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await authenticated_api.press_f1("dev1")


class TestCallGuardApi:
    """Test call guard/janitor API."""

    @pytest.mark.asyncio
    async def test_call_guard(self, authenticated_api):
        """Test calling the guard."""
        resp = _mock_response(200, text="ok")

        with patch("httpx.AsyncClient.post", return_value=resp):
            await authenticated_api.call_guard("dev1")


class TestAckApi:
    """Test notification acknowledgement API."""

    @pytest.mark.asyncio
    async def test_ack_call_notification(self, authenticated_api):
        """Test acknowledging a call notification."""
        resp = _mock_response(200, text="ok")

        with patch("httpx.AsyncClient.post", return_value=resp):
            await authenticated_api.ack_notification("msg_123", is_call=True)

    @pytest.mark.asyncio
    async def test_ack_info_notification(self, authenticated_api):
        """Test acknowledging an info notification."""
        resp = _mock_response(200, text="ok")

        with patch("httpx.AsyncClient.post", return_value=resp):
            await authenticated_api.ack_notification("msg_456", is_call=False)


class TestPhotoCallerApi:
    """Test photo caller toggle API."""

    @pytest.mark.asyncio
    async def test_enable_photo_caller(self, authenticated_api):
        """Test enabling photo caller."""
        resp = _mock_response(200, text="ok")

        with patch("httpx.AsyncClient.put", return_value=resp):
            await authenticated_api.set_photo_caller("dev1", enabled=True)

    @pytest.mark.asyncio
    async def test_disable_photo_caller(self, authenticated_api):
        """Test disabling photo caller."""
        resp = _mock_response(200, text="ok")

        with patch("httpx.AsyncClient.put", return_value=resp):
            await authenticated_api.set_photo_caller("dev1", enabled=False)


class TestOpeningsApi:
    """Test door opening history API."""

    @pytest.mark.asyncio
    async def test_get_openings(self, authenticated_api):
        """Test getting opening history."""
        resp = _mock_response(
            200,
            json={
                "entries": [
                    {
                        "timestamp": "2026-04-05T10:30:00Z",
                        "user": "John",
                        "door": "Portal",
                        "guestEmail": None,
                    },
                    {
                        "timestamp": "2026-04-05T09:15:00Z",
                        "user": "Jane",
                        "door": "Portal",
                        "guestEmail": "guest@example.com",
                    },
                ]
            },
        )

        with patch("httpx.AsyncClient.get", return_value=resp):
            records = await authenticated_api.get_opening_history(
                "dev1", "user_123"
            )

        assert len(records) == 2
        assert records[0].user == "John"
        assert records[1].guest_email == "guest@example.com"

    @pytest.mark.asyncio
    async def test_get_openings_empty(self, authenticated_api):
        """Test empty opening history."""
        resp = _mock_response(200, json={"entries": []})

        with patch("httpx.AsyncClient.get", return_value=resp):
            records = await authenticated_api.get_opening_history(
                "dev1", "user_123"
            )

        assert len(records) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api_new.py::TestDndApi tests/test_api_new.py::TestF1Api tests/test_api_new.py::TestCallGuardApi tests/test_api_new.py::TestAckApi tests/test_api_new.py::TestPhotoCallerApi tests/test_api_new.py::TestOpeningsApi -v`
Expected: FAIL — methods don't exist yet.

- [ ] **Step 3: Implement new API methods**

Add `OpeningRecord` dataclass and new methods to `custom_components/fermax_blue/api.py`:

```python
@dataclass
class OpeningRecord:
    """A door opening history entry."""

    timestamp: str
    user: str
    door: str
    guest_email: str | None = None
```

Add methods to `FermaxBlueApi`:

```python
async def get_dnd_status(self, device_id: str, fcm_token: str) -> bool:
    """Get Do Not Disturb status for a device."""
    response = await self._api_get(
        "/notification/api/v1/mutedevice/me",
        params={"deviceId": device_id, "token": fcm_token},
    )
    return response.json()

async def set_dnd(
    self, device_id: str, fcm_token: str, *, enabled: bool
) -> None:
    """Set Do Not Disturb status for a device."""
    await self._api_post(
        "/notification/api/v1/mutedevice/me",
        json={"deviceId": device_id, "token": fcm_token, "muted": enabled},
    )

async def press_f1(self, device_id: str) -> None:
    """Press the F1 auxiliary button on the intercom."""
    await self._api_post(f"/deviceaction/api/v1/device/{device_id}/f1")

async def call_guard(self, device_id: str) -> None:
    """Call the building guard/janitor."""
    await self._api_post(
        f"/deviceaction/api/v1/device/{device_id}/callguard"
    )

async def ack_notification(
    self, message_id: str, *, is_call: bool
) -> None:
    """Acknowledge a notification."""
    path = (
        "/callmanager/api/v1/message/ack"
        if is_call
        else "/notification/api/v1/message/ack"
    )
    try:
        await self._api_post(
            path,
            json={"attended": True, "fcmMessageId": message_id},
        )
    except httpx.HTTPStatusError:
        _LOGGER.debug("Failed to ACK notification %s", message_id)

async def set_photo_caller(
    self, device_id: str, *, enabled: bool
) -> None:
    """Enable or disable photo caller on the device."""
    await self._api_put(
        f"/deviceaction/api/v1/{device_id}/photocaller",
        params={"value": str(enabled).lower()},
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
    except httpx.HTTPStatusError:
        return []

    data = response.json()
    return [
        OpeningRecord(
            timestamp=entry.get("timestamp", ""),
            user=entry.get("user", ""),
            door=entry.get("door", ""),
            guest_email=entry.get("guestEmail"),
        )
        for entry in data.get("entries", [])
    ]
```

- [ ] **Step 4: Run all API tests**

Run: `python -m pytest tests/test_api_new.py tests/test_api.py -v`
Expected: ALL PASS.

---

## Task 3: Update Constants and Strings

**Files:**
- Modify: `custom_components/fermax_blue/const.py`
- Modify: `custom_components/fermax_blue/strings.json`

- [ ] **Step 1: Update const.py**

Add `"event"` to PLATFORMS list and add new constants:

```python
PLATFORMS = [
    "binary_sensor",
    "button",
    "camera",
    "event",
    "lock",
    "sensor",
    "switch",
]

# Options flow defaults
DEFAULT_SCAN_INTERVAL = 5  # minutes
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 30
CONF_SCAN_INTERVAL = "scan_interval"
```

- [ ] **Step 2: Update strings.json**

Replace the full content:

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Fermax Blue Login",
        "description": "Enter your Fermax Blue app credentials.",
        "data": {
          "username": "Email",
          "password": "Password"
        }
      }
    },
    "error": {
      "invalid_auth": "Invalid credentials. Please check your email and password.",
      "cannot_connect": "Unable to connect to Fermax Blue servers.",
      "no_devices": "No paired devices found on this account."
    },
    "abort": {
      "already_configured": "This account is already configured."
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "Fermax Blue Options",
        "data": {
          "scan_interval": "Polling interval (minutes)"
        }
      }
    }
  },
  "entity": {
    "binary_sensor": {
      "connection": {
        "name": "Connection"
      }
    },
    "event": {
      "doorbell": {
        "name": "Doorbell",
        "state_attributes": {
          "event_type": {
            "state": {
              "ring": "Ring"
            }
          }
        }
      }
    },
    "lock": {
      "door": {
        "name": "Door"
      }
    },
    "camera": {
      "visitor": {
        "name": "Visitor"
      }
    },
    "sensor": {
      "wifi_signal": {
        "name": "WiFi signal"
      },
      "status": {
        "name": "Status"
      },
      "last_opening": {
        "name": "Last door opening"
      }
    },
    "button": {
      "open_door": {
        "name": "Open door"
      },
      "camera_preview": {
        "name": "Camera preview"
      },
      "f1": {
        "name": "F1 auxiliary"
      },
      "call_guard": {
        "name": "Call guard"
      }
    },
    "switch": {
      "notifications": {
        "name": "Doorbell notifications"
      },
      "dnd": {
        "name": "Do not disturb"
      },
      "photo_caller": {
        "name": "Photo caller"
      }
    }
  }
}
```

- [ ] **Step 3: Run lint to verify**

Run: `python -m ruff check custom_components/fermax_blue/const.py`
Expected: No errors.

---

## Task 4: Entity Availability and Base Entity Update

**Files:**
- Modify: `custom_components/fermax_blue/entity.py`
- Test: `tests/test_entities.py`

- [ ] **Step 1: Write failing test for availability**

Create `tests/test_entities.py`:

```python
"""Tests for entity platforms."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fermax_blue.api import (
    AccessDoor,
    DeviceInfo,
    Pairing,
)
from custom_components.fermax_blue.coordinator import FermaxBlueCoordinator


@pytest.fixture
def mock_coordinator():
    """Return a mock coordinator."""
    coordinator = MagicMock(spec=FermaxBlueCoordinator)
    coordinator.pairing = Pairing(
        device_id="test_dev",
        tag="Test Home",
        installation_id="inst_1",
        access_doors={
            "GENERAL": AccessDoor(
                name="GENERAL",
                title="Portal",
                access_id={"block": 100, "subblock": -1, "number": 0},
                visible=True,
            ),
        },
    )
    coordinator.device_info = DeviceInfo(
        device_id="test_dev",
        connection_state="Connected",
        status="ACTIVATED",
        family="MONITOR",
        device_type="VEO-XL",
        subtype="WIFI",
        unit_number=42,
        photocaller=True,
        streaming_mode="video_call",
        is_monitor=True,
        wireless_signal=4,
    )
    coordinator.data = {
        "connection_state": "Connected",
        "status": "ACTIVATED",
        "wireless_signal": 4,
    }
    coordinator.notification_listener = MagicMock()
    coordinator.notification_listener.fcm_token = "test_fcm"
    coordinator.notification_listener.is_started = True
    return coordinator


class TestEntityAvailability:
    """Test entity availability based on device connection."""

    def test_available_when_connected(self, mock_coordinator):
        """Test entity is available when device is connected."""
        from custom_components.fermax_blue.entity import FermaxBlueEntity

        entity = FermaxBlueEntity(mock_coordinator)
        assert entity.available is True

    def test_unavailable_when_disconnected(self, mock_coordinator):
        """Test entity is unavailable when device is disconnected."""
        from custom_components.fermax_blue.entity import FermaxBlueEntity

        mock_coordinator.data = {"connection_state": "Disconnected"}
        entity = FermaxBlueEntity(mock_coordinator)
        assert entity.available is False

    def test_unavailable_when_no_data(self, mock_coordinator):
        """Test entity is unavailable when no data."""
        from custom_components.fermax_blue.entity import FermaxBlueEntity

        mock_coordinator.data = None
        entity = FermaxBlueEntity(mock_coordinator)
        assert entity.available is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_entities.py::TestEntityAvailability -v`
Expected: FAIL — `available` property not overridden.

- [ ] **Step 3: Add availability to entity.py**

Replace `custom_components/fermax_blue/entity.py`:

```python
"""Base entity for Fermax Blue integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import FermaxBlueCoordinator


class FermaxBlueEntity(CoordinatorEntity[FermaxBlueCoordinator]):
    """Base entity for Fermax Blue devices."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._device_id = coordinator.pairing.device_id

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.data:
            return False
        return self.coordinator.data.get("connection_state") == "Connected"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        info = self.coordinator.device_info
        model = f"{info.device_type} {info.subtype}" if info else "Unknown"

        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=f"Fermax {self.coordinator.pairing.tag}",
            manufacturer=MANUFACTURER,
            model=model,
            sw_version=None,
        )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_entities.py::TestEntityAvailability -v`
Expected: ALL PASS.

---

## Task 5: New Switch Entities (DND + Photo Caller)

**Files:**
- Modify: `custom_components/fermax_blue/switch.py`
- Test: `tests/test_entities.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_entities.py`:

```python
class TestDndSwitch:
    """Test Do Not Disturb switch."""

    def test_dnd_switch_unique_id(self, mock_coordinator):
        """Test DND switch unique ID."""
        from custom_components.fermax_blue.switch import FermaxDndSwitch

        switch = FermaxDndSwitch(mock_coordinator)
        assert switch.unique_id == "test_dev_dnd"

    @pytest.mark.asyncio
    async def test_dnd_turn_on(self, mock_coordinator):
        """Test turning on DND."""
        from custom_components.fermax_blue.switch import FermaxDndSwitch

        mock_coordinator.set_dnd = AsyncMock()
        switch = FermaxDndSwitch(mock_coordinator)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()
        mock_coordinator.set_dnd.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_dnd_turn_off(self, mock_coordinator):
        """Test turning off DND."""
        from custom_components.fermax_blue.switch import FermaxDndSwitch

        mock_coordinator.set_dnd = AsyncMock()
        switch = FermaxDndSwitch(mock_coordinator)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()
        mock_coordinator.set_dnd.assert_called_once_with(False)


class TestPhotoCallerSwitch:
    """Test Photo Caller switch."""

    def test_photo_caller_unique_id(self, mock_coordinator):
        """Test photo caller switch unique ID."""
        from custom_components.fermax_blue.switch import FermaxPhotoCallerSwitch

        switch = FermaxPhotoCallerSwitch(mock_coordinator)
        assert switch.unique_id == "test_dev_photo_caller"

    @pytest.mark.asyncio
    async def test_photo_caller_turn_on(self, mock_coordinator):
        """Test enabling photo caller."""
        from custom_components.fermax_blue.switch import FermaxPhotoCallerSwitch

        mock_coordinator.set_photo_caller = AsyncMock()
        switch = FermaxPhotoCallerSwitch(mock_coordinator)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()
        mock_coordinator.set_photo_caller.assert_called_once_with(True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_entities.py::TestDndSwitch tests/test_entities.py::TestPhotoCallerSwitch -v`
Expected: FAIL.

- [ ] **Step 3: Implement new switches in switch.py**

Replace `custom_components/fermax_blue/switch.py`:

```python
"""Switch platform for Fermax Blue."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FermaxBlueCoordinator
from .entity import FermaxBlueEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fermax Blue switches."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []

    for coordinator in coordinators:
        if coordinator.notification_listener:
            entities.append(FermaxNotificationSwitch(coordinator))
        entities.append(FermaxDndSwitch(coordinator))
        entities.append(FermaxPhotoCallerSwitch(coordinator))

    async_add_entities(entities)


class FermaxNotificationSwitch(FermaxBlueEntity, SwitchEntity):
    """Switch to enable/disable doorbell notifications."""

    _attr_translation_key = "notifications"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_notifications"
        self._is_on = True

    @property
    def is_on(self) -> bool:
        """Return True if notifications are enabled."""
        if self.coordinator.notification_listener:
            return self.coordinator.notification_listener.is_started
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable notifications."""
        if self.coordinator.notification_listener:
            await self.coordinator.notification_listener.start()
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable notifications."""
        if self.coordinator.notification_listener:
            await self.coordinator.notification_listener.stop()
            self._is_on = False
            self.async_write_ha_state()


class FermaxDndSwitch(FermaxBlueEntity, SwitchEntity):
    """Switch for Do Not Disturb mode."""

    _attr_translation_key = "dnd"
    _attr_icon = "mdi:bell-off"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_dnd"

    @property
    def is_on(self) -> bool | None:
        """Return True if DND is enabled."""
        return self.coordinator.dnd_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable Do Not Disturb."""
        await self.coordinator.set_dnd(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable Do Not Disturb."""
        await self.coordinator.set_dnd(False)
        self.async_write_ha_state()


class FermaxPhotoCallerSwitch(FermaxBlueEntity, SwitchEntity):
    """Switch to enable/disable photo caller."""

    _attr_translation_key = "photo_caller"
    _attr_icon = "mdi:camera-account"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_photo_caller"

    @property
    def is_on(self) -> bool | None:
        """Return True if photo caller is enabled."""
        if self.coordinator.device_info:
            return self.coordinator.device_info.photocaller
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable photo caller."""
        await self.coordinator.set_photo_caller(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable photo caller."""
        await self.coordinator.set_photo_caller(False)
        self.async_write_ha_state()
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_entities.py -v`
Expected: ALL PASS.

---

## Task 6: New Button Entities (F1 + Call Guard)

**Files:**
- Modify: `custom_components/fermax_blue/button.py`
- Test: `tests/test_entities.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_entities.py`:

```python
class TestF1Button:
    """Test F1 auxiliary button."""

    def test_f1_unique_id(self, mock_coordinator):
        """Test F1 button unique ID."""
        from custom_components.fermax_blue.button import FermaxF1Button

        button = FermaxF1Button(mock_coordinator)
        assert button.unique_id == "test_dev_f1"

    @pytest.mark.asyncio
    async def test_f1_press(self, mock_coordinator):
        """Test pressing F1."""
        from custom_components.fermax_blue.button import FermaxF1Button

        mock_coordinator.press_f1 = AsyncMock()
        button = FermaxF1Button(mock_coordinator)

        await button.async_press()
        mock_coordinator.press_f1.assert_called_once()


class TestCallGuardButton:
    """Test Call Guard button."""

    def test_call_guard_unique_id(self, mock_coordinator):
        """Test call guard button unique ID."""
        from custom_components.fermax_blue.button import FermaxCallGuardButton

        button = FermaxCallGuardButton(mock_coordinator)
        assert button.unique_id == "test_dev_call_guard"

    @pytest.mark.asyncio
    async def test_call_guard_press(self, mock_coordinator):
        """Test pressing call guard."""
        from custom_components.fermax_blue.button import FermaxCallGuardButton

        mock_coordinator.call_guard = AsyncMock()
        button = FermaxCallGuardButton(mock_coordinator)

        await button.async_press()
        mock_coordinator.call_guard.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_entities.py::TestF1Button tests/test_entities.py::TestCallGuardButton -v`

- [ ] **Step 3: Add F1 and Call Guard buttons to button.py**

Add after `FermaxCameraPreviewButton` class, and update `async_setup_entry`:

```python
class FermaxF1Button(FermaxBlueEntity, ButtonEntity):
    """Button for F1 auxiliary function."""

    _attr_translation_key = "f1"
    _attr_icon = "mdi:numeric-1-box"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_f1"

    async def async_press(self) -> None:
        """Press F1."""
        await self.coordinator.press_f1()


class FermaxCallGuardButton(FermaxBlueEntity, ButtonEntity):
    """Button to call the building guard/janitor."""

    _attr_translation_key = "call_guard"
    _attr_icon = "mdi:account-tie"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_call_guard"

    async def async_press(self) -> None:
        """Call the guard."""
        await self.coordinator.call_guard()
```

Update `async_setup_entry` to add new buttons:

```python
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fermax Blue buttons."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []

    for coordinator in coordinators:
        for door_name, door in coordinator.pairing.access_doors.items():
            if door.visible:
                entities.append(
                    FermaxOpenDoorButton(coordinator, door_name, door.title)
                )
        entities.append(FermaxCameraPreviewButton(coordinator))
        entities.append(FermaxF1Button(coordinator))
        entities.append(FermaxCallGuardButton(coordinator))

    async_add_entities(entities)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_entities.py -v`
Expected: ALL PASS.

---

## Task 7: Event Entity (Doorbell)

**Files:**
- Create: `custom_components/fermax_blue/event.py`
- Modify: `custom_components/fermax_blue/binary_sensor.py`
- Test: `tests/test_entities.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_entities.py`:

```python
class TestDoorbellEvent:
    """Test doorbell event entity."""

    def test_doorbell_event_types(self, mock_coordinator):
        """Test doorbell event entity has correct event types."""
        from custom_components.fermax_blue.event import FermaxDoorbellEvent

        event = FermaxDoorbellEvent(mock_coordinator)
        assert "ring" in event.event_types
        assert event.unique_id == "test_dev_doorbell_event"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_entities.py::TestDoorbellEvent -v`

- [ ] **Step 3: Create event.py**

Create `custom_components/fermax_blue/event.py`:

```python
"""Event platform for Fermax Blue."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_DOORBELL_RING
from .coordinator import FermaxBlueCoordinator
from .entity import FermaxBlueEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fermax Blue event entities."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[EventEntity] = []

    for coordinator in coordinators:
        entities.append(FermaxDoorbellEvent(coordinator))

    async_add_entities(entities)


class FermaxDoorbellEvent(FermaxBlueEntity, EventEntity):
    """Event entity for doorbell rings."""

    _attr_translation_key = "doorbell"
    _attr_event_types = ["ring"]

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_doorbell_event"

    async def async_added_to_hass(self) -> None:
        """Register callbacks when added to hass."""
        await super().async_added_to_hass()

        for door_name in self.coordinator.pairing.access_doors:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    SIGNAL_DOORBELL_RING.format(self._device_id, door_name),
                    self._handle_ring,
                )
            )

    @callback
    def _handle_ring(self) -> None:
        """Handle a doorbell ring event."""
        self._trigger_event("ring")
        self.async_write_ha_state()
```

- [ ] **Step 4: Remove doorbell from binary_sensor.py**

Update `custom_components/fermax_blue/binary_sensor.py` — remove `FermaxDoorbellSensor` class and its import from `async_setup_entry`. Remove the `SIGNAL_CALL_ENDED` import. Keep only `FermaxConnectionSensor`:

```python
"""Binary sensor platform for Fermax Blue."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FermaxBlueCoordinator
from .entity import FermaxBlueEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fermax Blue binary sensors."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []

    for coordinator in coordinators:
        entities.append(FermaxConnectionSensor(coordinator))

    async_add_entities(entities)


class FermaxConnectionSensor(FermaxBlueEntity, BinarySensorEntity):
    """Sensor for device connection status."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "connection"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_connection"

    @property
    def is_on(self) -> bool | None:
        """Return True if connected."""
        if self.coordinator.data:
            return self.coordinator.data.get("connection_state") == "Connected"
        return None
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_entities.py -v`
Expected: ALL PASS.

---

## Task 8: Last Opening Sensor

**Files:**
- Modify: `custom_components/fermax_blue/sensor.py`
- Test: `tests/test_entities.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_entities.py`:

```python
class TestLastOpeningSensor:
    """Test last door opening sensor."""

    def test_last_opening_unique_id(self, mock_coordinator):
        """Test last opening sensor unique ID."""
        from custom_components.fermax_blue.sensor import FermaxLastOpeningSensor

        mock_coordinator.last_opening = None
        sensor = FermaxLastOpeningSensor(mock_coordinator)
        assert sensor.unique_id == "test_dev_last_opening"

    def test_last_opening_value(self, mock_coordinator):
        """Test last opening returns timestamp."""
        from custom_components.fermax_blue.api import OpeningRecord
        from custom_components.fermax_blue.sensor import FermaxLastOpeningSensor

        mock_coordinator.last_opening = OpeningRecord(
            timestamp="2026-04-05T10:30:00Z",
            user="John",
            door="Portal",
        )
        sensor = FermaxLastOpeningSensor(mock_coordinator)
        assert sensor.native_value == "2026-04-05T10:30:00Z"

    def test_last_opening_extra_attrs(self, mock_coordinator):
        """Test last opening extra state attributes."""
        from custom_components.fermax_blue.api import OpeningRecord
        from custom_components.fermax_blue.sensor import FermaxLastOpeningSensor

        mock_coordinator.last_opening = OpeningRecord(
            timestamp="2026-04-05T10:30:00Z",
            user="John",
            door="Portal",
            guest_email="guest@test.com",
        )
        sensor = FermaxLastOpeningSensor(mock_coordinator)
        attrs = sensor.extra_state_attributes
        assert attrs["user"] == "John"
        assert attrs["door"] == "Portal"
        assert attrs["guest_email"] == "guest@test.com"
```

- [ ] **Step 2: Implement in sensor.py**

Add `FermaxLastOpeningSensor` class and update `async_setup_entry`:

```python
class FermaxLastOpeningSensor(FermaxBlueEntity, SensorEntity):
    """Sensor showing the last door opening."""

    _attr_translation_key = "last_opening"
    _attr_icon = "mdi:door-open"

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_last_opening"

    @property
    def native_value(self) -> str | None:
        """Return the timestamp of the last opening."""
        if self.coordinator.last_opening:
            return self.coordinator.last_opening.timestamp
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str | None] | None:
        """Return extra attributes about the last opening."""
        if not self.coordinator.last_opening:
            return None
        record = self.coordinator.last_opening
        return {
            "user": record.user,
            "door": record.door,
            "guest_email": record.guest_email,
        }
```

Update `async_setup_entry` to add the new sensor:
```python
for coordinator in coordinators:
    entities.append(FermaxWifiSignalSensor(coordinator))
    entities.append(FermaxDeviceStatusSensor(coordinator))
    entities.append(FermaxLastOpeningSensor(coordinator))
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_entities.py::TestLastOpeningSensor -v`
Expected: ALL PASS.

---

## Task 9: Coordinator Updates

**Files:**
- Modify: `custom_components/fermax_blue/coordinator.py`
- Test: `tests/test_coordinator.py`

- [ ] **Step 1: Write coordinator tests**

Create `tests/test_coordinator.py`:

```python
"""Tests for the Fermax Blue coordinator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fermax_blue.api import (
    AccessDoor,
    DeviceInfo,
    FermaxBlueApi,
    OpeningRecord,
    Pairing,
)
from custom_components.fermax_blue.coordinator import FermaxBlueCoordinator


@pytest.fixture
def mock_hass():
    """Return a mock HomeAssistant instance."""
    hass = MagicMock()
    hass.async_create_task = MagicMock()
    return hass


@pytest.fixture
def mock_api():
    """Return a mock API."""
    api = AsyncMock(spec=FermaxBlueApi)
    api.get_device_info = AsyncMock(
        return_value=DeviceInfo(
            device_id="dev1",
            connection_state="Connected",
            status="ACTIVATED",
            family="MONITOR",
            device_type="VEO-XL",
            subtype="WIFI",
            unit_number=42,
            photocaller=True,
            streaming_mode="video_call",
            is_monitor=True,
            wireless_signal=4,
        )
    )
    api.get_dnd_status = AsyncMock(return_value=False)
    api.set_dnd = AsyncMock()
    api.press_f1 = AsyncMock()
    api.call_guard = AsyncMock()
    api.set_photo_caller = AsyncMock()
    api.get_opening_history = AsyncMock(return_value=[])
    api.ack_notification = AsyncMock()
    return api


@pytest.fixture
def pairing():
    """Return a test pairing."""
    return Pairing(
        device_id="dev1",
        tag="Home",
        installation_id="inst_1",
        access_doors={
            "GENERAL": AccessDoor(
                name="GENERAL",
                title="Portal",
                access_id={"block": 100, "subblock": -1, "number": 0},
                visible=True,
            ),
        },
    )


class TestCoordinatorDnd:
    """Test DND coordination."""

    @pytest.mark.asyncio
    async def test_set_dnd_calls_api(self, mock_hass, mock_api, pairing):
        """Test that set_dnd calls the API."""
        coord = FermaxBlueCoordinator(mock_hass, mock_api, pairing)
        coord.notification_listener = MagicMock()
        coord.notification_listener.fcm_token = "tok"

        await coord.set_dnd(True)
        mock_api.set_dnd.assert_called_once_with("dev1", "tok", enabled=True)


class TestCoordinatorF1:
    """Test F1 coordination."""

    @pytest.mark.asyncio
    async def test_press_f1_calls_api(self, mock_hass, mock_api, pairing):
        """Test that press_f1 calls the API."""
        coord = FermaxBlueCoordinator(mock_hass, mock_api, pairing)

        await coord.press_f1()
        mock_api.press_f1.assert_called_once_with("dev1")


class TestCoordinatorCallGuard:
    """Test call guard coordination."""

    @pytest.mark.asyncio
    async def test_call_guard_calls_api(self, mock_hass, mock_api, pairing):
        """Test that call_guard calls the API."""
        coord = FermaxBlueCoordinator(mock_hass, mock_api, pairing)

        await coord.call_guard()
        mock_api.call_guard.assert_called_once_with("dev1")


class TestCoordinatorPhotoCaller:
    """Test photo caller coordination."""

    @pytest.mark.asyncio
    async def test_set_photo_caller_calls_api(
        self, mock_hass, mock_api, pairing
    ):
        """Test that set_photo_caller calls the API."""
        coord = FermaxBlueCoordinator(mock_hass, mock_api, pairing)

        await coord.set_photo_caller(True)
        mock_api.set_photo_caller.assert_called_once_with(
            "dev1", enabled=True
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_coordinator.py -v`

- [ ] **Step 3: Update coordinator.py with new methods and state**

Add to `FermaxBlueCoordinator.__init__`:
```python
self._dnd_enabled: bool | None = None
self._last_opening: OpeningRecord | None = None
```

Add new properties:
```python
@property
def dnd_enabled(self) -> bool | None:
    """Return DND state."""
    return self._dnd_enabled

@property
def last_opening(self) -> OpeningRecord | None:
    """Return the last door opening record."""
    return self._last_opening
```

Add new methods:
```python
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
    await self.api.set_photo_caller(
        self.pairing.device_id, enabled=enabled
    )
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
```

Update `_handle_notification` to ACK notifications:
```python
@callback
def _handle_notification(self, notification: dict, persistent_id: str) -> None:
    """Handle an incoming FCM doorbell notification."""
    _LOGGER.info(
        "Doorbell notification for %s: %s",
        self.pairing.device_id,
        notification,
    )

    # ACK the notification
    fcm_message_id = notification.get("fcmMessageId") or persistent_id
    notification_type = notification.get("FermaxNotificationType", "")
    is_call = notification_type in ("Call", "CallAttend", "CallEnd")
    self.hass.async_create_task(
        self.api.ack_notification(fcm_message_id, is_call=is_call)
    )

    self._doorbell_ringing = True
    self._photo_fetch_pending = True
    # ... rest of existing code unchanged
```

Update imports at top of coordinator.py to include `OpeningRecord`:
```python
from .api import (
    DeviceInfo,
    DivertResponse,
    FermaxApiError,
    FermaxAuthError,
    FermaxBlueApi,
    OpeningRecord,
    Pairing,
)
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/test_coordinator.py tests/test_entities.py tests/test_api.py tests/test_api_new.py -v`
Expected: ALL PASS.

---

## Task 10: Options Flow

**Files:**
- Modify: `custom_components/fermax_blue/config_flow.py`
- Modify: `custom_components/fermax_blue/__init__.py`
- Test: `tests/test_config_flow.py`

- [ ] **Step 1: Write failing test for options flow**

Add to `tests/test_config_flow.py`:

```python
class TestOptionsFlow:
    """Test the options flow."""

    @pytest.mark.asyncio
    async def test_options_flow_default(self):
        """Test options flow returns default scan interval."""
        from custom_components.fermax_blue.config_flow import (
            FermaxBlueOptionsFlow,
        )

        flow = FermaxBlueOptionsFlow.__new__(FermaxBlueOptionsFlow)
        # Verify class exists and has the right step
        assert hasattr(flow, "async_step_init")
```

- [ ] **Step 2: Implement options flow in config_flow.py**

Add to `custom_components/fermax_blue/config_flow.py`:

```python
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, MAX_SCAN_INTERVAL, MIN_SCAN_INTERVAL


class FermaxBlueOptionsFlow(OptionsFlow):
    """Handle options for Fermax Blue."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                }
            ),
        )
```

Add `@staticmethod` to `FermaxBlueConfigFlow`:
```python
@staticmethod
def async_get_options_flow(config_entry: ConfigEntry) -> FermaxBlueOptionsFlow:
    """Return the options flow."""
    return FermaxBlueOptionsFlow(config_entry)
```

- [ ] **Step 3: Update __init__.py to listen for options updates**

Add options update listener in `async_setup_entry`:
```python
entry.async_on_unload(entry.add_update_listener(_async_options_updated))
```

Add the listener function:
```python
async def _async_options_updated(
    hass: HomeAssistant, entry: FermaxBlueConfigEntry
) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
```

Update coordinator creation to use configured interval:
```python
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL

# In async_setup_entry, when creating coordinators:
scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
coordinator = FermaxBlueCoordinator(hass, api, pairing, scan_interval=scan_interval)
```

Update `FermaxBlueCoordinator.__init__` to accept `scan_interval`:
```python
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
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS.

---

## Task 11: Diagnostics

**Files:**
- Create: `custom_components/fermax_blue/diagnostics.py`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_diagnostics.py`:

```python
"""Tests for Fermax Blue diagnostics."""

import pytest

from custom_components.fermax_blue.diagnostics import REDACT_KEYS


class TestDiagnostics:
    """Test diagnostics output."""

    def test_redact_keys_includes_password(self):
        """Test that sensitive keys are in redact list."""
        assert "password" in REDACT_KEYS
        assert "access_token" in REDACT_KEYS
        assert "fcm_token" in REDACT_KEYS

    def test_redact_keys_includes_username(self):
        """Test that username is redacted."""
        assert "username" in REDACT_KEYS
```

- [ ] **Step 2: Implement diagnostics.py**

Create `custom_components/fermax_blue/diagnostics.py`:

```python
"""Diagnostics support for Fermax Blue."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import FermaxBlueCoordinator

REDACT_KEYS = {
    "password",
    "username",
    "access_token",
    "fcm_token",
    "token",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinators: list[FermaxBlueCoordinator] = hass.data[DOMAIN][entry.entry_id]

    devices = []
    for coordinator in coordinators:
        device_data: dict[str, Any] = {
            "device_id": coordinator.pairing.device_id,
            "tag": coordinator.pairing.tag,
            "coordinator_data": coordinator.data,
        }
        if coordinator.device_info:
            device_data["device_info"] = {
                "connection_state": coordinator.device_info.connection_state,
                "status": coordinator.device_info.status,
                "family": coordinator.device_info.family,
                "device_type": coordinator.device_info.device_type,
                "subtype": coordinator.device_info.subtype,
                "wireless_signal": coordinator.device_info.wireless_signal,
                "photocaller": coordinator.device_info.photocaller,
            }
        devices.append(device_data)

    return async_redact_data(
        {
            "config_entry": async_redact_data(dict(entry.data), REDACT_KEYS),
            "devices": devices,
        },
        REDACT_KEYS,
    )
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_diagnostics.py -v`
Expected: ALL PASS.

---

## Task 12: Update Fixtures and Run Full Suite

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Update conftest.py with new API method mocks**

Add to the `mock_api` fixture:

```python
api.get_dnd_status = AsyncMock(return_value=False)
api.set_dnd = AsyncMock()
api.press_f1 = AsyncMock()
api.call_guard = AsyncMock()
api.ack_notification = AsyncMock()
api.set_photo_caller = AsyncMock()
api.get_opening_history = AsyncMock(return_value=[])
```

- [ ] **Step 2: Run the complete test suite**

Run: `python -m pytest tests/ -v --cov=custom_components/fermax_blue --cov-report=term-missing --tb=short`
Expected: ALL PASS, coverage > 80%.

- [ ] **Step 3: Run lint and type check**

Run: `python -m ruff check custom_components/ tests/ && python -m ruff format --check custom_components/ tests/`
Expected: No errors.

Run: `python -m mypy custom_components/fermax_blue/ --ignore-missing-imports`
Expected: No errors.

---

## Task 13: Final Verification

- [ ] **Step 1: Run the full CI check locally**

Run: `make check` or if Makefile has issues, run each command manually:
```bash
python -m ruff check custom_components/ tests/
python -m ruff format --check custom_components/ tests/
python -m mypy custom_components/fermax_blue/ --ignore-missing-imports
python -m pytest tests/ -v --cov=custom_components/fermax_blue --cov-report=term-missing --tb=short
```

Expected: ALL checks pass.

- [ ] **Step 2: Verify no regressions**

Manually verify:
1. All existing tests still pass
2. All new tests pass
3. No lint errors
4. No type errors
5. Coverage >= 80%
