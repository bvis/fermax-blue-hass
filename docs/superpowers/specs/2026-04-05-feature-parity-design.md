# Fermax Blue HA Integration - Feature Parity & Platinum Quality

## Goal

Bring the HA integration to feature parity with the Fermax Blue Android APK v4.3.0 for all user-facing features that make sense in a smart home context, while reaching platinum-tier quality standards.

## Scope

### New Features (from APK)

1. **Do Not Disturb (DND) switch** — `GET/POST /notification/api/v1/mutedevice/me`
   - Switch entity per device
   - Reads current DND state, allows toggle
   - Useful for night-mode automations

2. **F1 auxiliary button** — `POST /deviceaction/api/v1/device/{id}/f1`
   - Button entity per device
   - Fires the F1 function on the intercom panel

3. **Call guard/janitor button** — `POST /deviceaction/api/v1/device/{id}/callguard`
   - Button entity per device
   - Calls the building's guard/janitor

4. **Notification ACK** — `POST /notification/api/v1/message/ack` + `POST /callmanager/api/v1/message/ack`
   - Automatically acknowledge call and info notifications when received
   - Improves push reliability (server knows message was delivered)
   - Integrated into coordinator notification handler, not a separate entity

5. **Photo caller toggle** — `PUT /deviceaction/api/v1/{deviceId}/photocaller?value={bool}`
   - Switch entity per device
   - Enables/disables automatic photo capture on doorbell ring

6. **Door opening history** — `GET /rexistro/api/v1/opendoorregistry`
   - Sensor showing last opening timestamp + who opened
   - Extra state attributes: recent openings list

### Quality Improvements

7. **Diagnostics** — `diagnostics.py`
   - Config entry diagnostics (redacted credentials, API status)
   - Device diagnostics (connection state, signal, firmware, panels)

8. **Event entity** — replace doorbell binary_sensor with `event.py`
   - `event.fermax_{tag}_doorbell` with event_type `doorbell_ring`
   - More semantic than binary_sensor on/off cycling
   - Keep connection binary_sensor as-is

9. **Retry logic with exponential backoff**
   - Wrap API calls with retry decorator (max 3 retries, 1s/2s/4s delays)
   - Only retry on transient errors (5xx, timeouts, connection errors)
   - Never retry auth errors (401/403)

10. **Entity unavailable when offline**
    - When device `connectionState == "Disconnected"`, mark all entities as unavailable
    - Coordinator sets `available` flag based on device info

11. **Options flow**
    - Post-setup configuration: polling interval (1-30 min, default 5)
    - Enable/disable specific features (photo caller, notifications)

12. **Expanded test coverage (TDD)**
    - Every new feature gets tests FIRST
    - Standalone API test layer (no HA dependency) for verifying API calls
    - Entity-level tests for all platforms
    - Coordinator tests
    - Target: 90%+ coverage

## Architecture

### API Layer (`api.py`)
New methods:
- `get_dnd_status(device_id, fcm_token) -> bool`
- `set_dnd(device_id, fcm_token, enabled) -> None`
- `press_f1(device_id) -> None`
- `call_guard(device_id) -> None`
- `ack_notification(message_id, is_call) -> None`
- `set_photo_caller(device_id, enabled) -> None`
- `get_opening_history(device_id, user_id) -> list[OpeningRecord]`

New dataclass:
- `OpeningRecord(timestamp, user, door, guest_email)`

Retry wrapper:
- Decorator `@retry_transient` on all API methods
- Uses `asyncio.sleep` with exponential backoff

### Entity Architecture

All entities follow existing pattern: extend `FermaxBlueEntity`, use coordinator data.

| Platform | New Entities | Translation Key |
|----------|-------------|-----------------|
| switch | DND switch, Photo caller switch | `dnd`, `photo_caller` |
| button | F1 button, Call guard button | `f1`, `call_guard` |
| event | Doorbell event | `doorbell` |
| sensor | Last opening sensor | `last_opening` |

### Coordinator Changes
- Store DND state, photo caller state, opening history in coordinator data
- ACK notifications automatically in `_handle_notification`
- Expose `available` property based on connection state
- Configurable polling interval from options flow

### Testing Strategy

**Layer 1: API tests** (`tests/test_api.py`)
- Pure httpx mocking, no HA dependency
- Tests every API method: success, failure, retry behavior
- Tests retry decorator independently

**Layer 2: Coordinator tests** (`tests/test_coordinator.py`)
- Mock API, test coordinator logic
- Notification handling, state transitions, polling

**Layer 3: Entity tests** (`tests/test_*.py` per platform)
- Mock coordinator, test entity behavior
- State reporting, action handling, availability

**Layer 4: Config/options flow tests** (`tests/test_config_flow.py`)
- Existing + options flow tests

**Layer 5: Integration smoke tests** (`tests/test_integration.py`)
- Full setup → entity creation → state check → teardown
- Uses all mocks, validates wiring

## Out of Scope

- Full video streaming (mediasoup WebRTC) — requires browser-side client
- In-call actions (open door during call, change video mid-call) — requires active streaming session
- Panel management / firmware updates — admin functions
- Guest management — admin function
- Subscription management — irrelevant for HA
- User account management — handled by config flow

## File Changes Summary

**New files:**
- `custom_components/fermax_blue/diagnostics.py`
- `custom_components/fermax_blue/event.py`
- `tests/test_coordinator.py`
- `tests/test_event.py`
- `tests/test_switch.py`
- `tests/test_button.py`
- `tests/test_binary_sensor.py`
- `tests/test_camera.py`
- `tests/test_lock.py`
- `tests/test_sensor.py`
- `tests/test_diagnostics.py`
- `tests/test_integration.py`

**Modified files:**
- `custom_components/fermax_blue/api.py` — new methods, retry logic, OpeningRecord
- `custom_components/fermax_blue/coordinator.py` — new state, ACK, availability, options
- `custom_components/fermax_blue/switch.py` — add DND + photo caller switches
- `custom_components/fermax_blue/button.py` — add F1 + call guard buttons
- `custom_components/fermax_blue/sensor.py` — add last opening sensor
- `custom_components/fermax_blue/binary_sensor.py` — remove doorbell (moved to event)
- `custom_components/fermax_blue/entity.py` — add availability logic
- `custom_components/fermax_blue/config_flow.py` — add options flow
- `custom_components/fermax_blue/const.py` — new constants
- `custom_components/fermax_blue/__init__.py` — register event platform, options flow
- `custom_components/fermax_blue/strings.json` — new translation keys
- `tests/conftest.py` — expanded fixtures
- `tests/test_api.py` — new API method tests
- `tests/test_config_flow.py` — options flow tests
