# Changelog

## [0.4.2] - 2026-04-05

### Fixed
- **Video streaming not starting** — FCM notification data is nested under `data` key; coordinator now extracts `RoomId`, `SocketUrl`, `FermaxToken` correctly
- **FermaxToken for signaling** — Use device JWT from push notification instead of user OAuth token for Socket.IO authentication
- **Door buttons/locks missing** — Create entities for all doors regardless of `visible` flag from API (flag is unreliable on some installations)
- **AccessDoorKey field name** — Handle both `AccessDoorKey` and `accessDoorKey` in notification data

## [0.4.1] - 2026-04-05

### Fixed
- **SSL blocking call warning** — Use HA's `create_async_httpx_client` / `get_async_client` helpers instead of creating `httpx.AsyncClient` directly, which triggered `Detected blocking call to load_verify_locations` on the event loop
- **`av==13.1.0` dependency conflict** — Removed pinned `av` version from requirements; it's a transitive dependency of pymediasoup/aiortc and conflicts with HA's bundled version on Python 3.14

### Added
- **Hassfest validation** in CI — Official HA integration validator now runs on every push/PR
- API client now accepts an injected `httpx.AsyncClient` for proper HA integration; falls back to creating its own for standalone usage (CLI, tests)

## [0.4.0] - 2026-04-05

### Added
- **Live video streaming** — Real-time MJPEG video from the intercom camera via mediasoup/WebRTC
  - Camera entity now serves live frames when preview is active (~720x480, ~10fps)
  - Full pipeline: auto-on → FCM push → Socket.IO signaling → mediasoup consume → JPEG frames
  - Camera `turn_on` starts live stream, `turn_off` stops it
  - Automatic stream teardown on call end or timeout
- **E2E streaming test script** — `scripts/test_streaming.py` for local validation

### Fixed
- FCM push notifications not received: missing `bundle_id` (`com.fermax.blue.app`) in Firebase config
- FCM token type: use FCM v2 registration token instead of legacy GCM token
- Signaling protocol: correct field names matching APK (`fermaxOauthToken`, `appToken`, `protocolVersion`)
- Token registration: use v1 endpoint matching APK behavior, handle 409 conflict
- DND status API: handle bare boolean response (not always dict)
- mediasoup consume: parse RTP capabilities from JSON string to dict for Socket.IO
- Stale push notification filtering based on RoomId timestamp

### Changed
- `notification.py`: prefer FCM v2 registration token over legacy GCM token
- `streaming.py`: full rewrite integrating pymediasoup + aiortc for actual video frame capture
- `camera.py`: `async_camera_image` returns live frames when streaming, MJPEG stream support
- `coordinator.py`: automatic stream session management on push notification with RoomId
- New dependencies: `pymediasoup>=1.1.0`, `Pillow>=10.0.0`, `av==13.1.0`

## [0.3.1] - 2026-04-05

### Fixed
- Resolve mypy `no-any-return` errors in API client (`_api_request`, `get_dnd_status`)
- Fix `RuntimeError: Frame helper not set up` in coordinator tests on Python 3.13

### Added
- Pre-push git hook replicating full CI pipeline locally (`make pre-push`)
- Interactive CLI tester for local API testing (`make cli`)

## [0.3.0] - 2026-04-05

### Added
- **Do Not Disturb switch** — Toggle DND mode per device via `/notification/api/v1/mutedevice/me`
- **Photo caller switch** — Enable/disable automatic photo capture on doorbell ring via `/deviceaction/api/v1/{id}/photocaller`
- **F1 auxiliary button** — Trigger the F1 function on the intercom panel
- **Call guard button** — Call the building's guard/janitor from HA
- **Doorbell event entity** — Semantic `event.doorbell` replaces the binary_sensor doorbell for richer automations
- **Last door opening sensor** — Shows timestamp of last opening with extra attributes (user, door, guest)
- **Notification acknowledgement** — Automatically ACK call and info push notifications for improved reliability
- **Opening history API** — Fetch door opening registry from `/rexistro/api/v1/opendoorregistry`
- **Diagnostics support** — Config entry diagnostics with redacted credentials for troubleshooting
- **Options flow** — Configure polling interval (1–30 minutes) from the integration settings
- **Entity availability** — All entities become unavailable when the intercom is disconnected
- **API retry logic** — Exponential backoff (1s/2s/4s) on transient errors (5xx, connection, timeout)
- **Interactive CLI tester** — `make cli` to test all API features locally in Docker without HA
- 79 unit tests across API, coordinator, entity, and diagnostics layers

### Changed
- Doorbell detection migrated from `binary_sensor` to `event` platform (more semantic, better automations)
- API client now uses unified `_api_request` with retry for GET/POST/PUT
- Coordinator accepts configurable `scan_interval` from options flow
- Makefile fixed: Docker commands now work correctly with proper quoting

### Removed
- `binary_sensor.doorbell` — replaced by `event.doorbell` (see migration note below)

### Migration from v0.2.0
- The `binary_sensor.<name>_doorbell` entity is removed. Use the new `event.<name>_doorbell` entity instead.
- Automations using `binary_sensor.doorbell` state changes should be updated to use event triggers:
  ```yaml
  trigger:
    - platform: state
      entity_id: event.fermax_your_home_doorbell
      attribute: event_type
  ```

## [0.2.0] - 2026-04-05

### Added
- Camera preview (auto-on): view intercom camera on demand without a doorbell ring
- Camera preview button entity (`button.camera_preview`)
- Camera entity supports `turn_on` to trigger auto-on
- Video source change API support
- Docker-based development tooling (`make check/lint/format/typecheck/test`)
- Comprehensive ruff linting (16 rule categories) and mypy type checking in CI
- 36 unit tests across 8 test classes

### Fixed
- Critical: `@callback` on async shutdown handler prevented cleanup on HA stop
- API client leak on setup failure and config flow validation
- Use `async_call_later` for all timed tasks (doorbell reset, auto-lock, camera timeout) instead of untracked background coroutines
- Proper error handling with `UpdateFailed` in coordinator
- Log exceptions instead of silently swallowing them
- WiFi signal sensor: removed invalid device_class for "bars" unit

### Changed
- Optimize API calls: call log/photos only fetched after doorbell ring (was every 5 min)
- Camera auto-deactivates after 90 seconds (matching app behavior)
- Door auto-locks after 5 seconds using cancellable timer
- Removed duplicate CONF constants from const.py

## [0.1.1] - 2026-04-04

### Fixed
- Resolve blocking I/O warnings in HA event loop (SSL cert loading, file reads)
- Use persistent HTTP client instead of creating one per request
- Use `asyncio.to_thread` for credential file I/O operations
- Properly close HTTP client on integration unload

### Changed
- Switch to Fermax notification API v2 for push token registration
- Add dedicated user setup guide for doorbell notifications

## [0.1.0] - 2026-04-03

### Added
- Initial release
- Fermax Blue API client with OAuth authentication
- Firebase Cloud Messaging for real-time doorbell notifications
- Door opening via lock entity and button
- Visitor camera (last photo from photocaller)
- Connection status binary sensor
- Doorbell ring binary sensor (auto-resets after 30s)
- WiFi signal strength sensor
- Device status sensor
- Notification enable/disable switch
- Config flow for UI-based setup
- English translations
- HACS compatibility
