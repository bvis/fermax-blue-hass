# Changelog

## [0.17.0] - 2026-07-18

### Changed
- **Live video restored on Home Assistant 2026.7+** (#38) — `aiortc` 1.15.0 supports `av>=14,<18`, compatible with the `av==17.0.1` shipped by HA 2026.7+, so the live-video dependencies are hard requirements again: `pymediasoup>=1.5.0` and `aiortc>=1.15.0` are back in the manifest and installed automatically — no manual `pip install` needed on any supported HA version. The v0.16.8 availability guards remain as defense in depth: installs that upgraded while the deps were optional keep working (a restart lets HA install the requirements), and everything except live video continues to work if they are ever missing (#39).

## [0.16.8] - 2026-07-07

### Fixed
- **Integration loads again on Home Assistant 2026.7+** (#34) — HA 2026.7 ships `av>=17`, and no release of `aiortc` (a `pymediasoup` dependency) supports it yet, so the required `pymediasoup` package became uninstallable and the whole integration failed to set up. The live-video dependencies are now optional: they are no longer listed in the manifest requirements, and every stream entry point checks their availability first — `start_camera_preview()` skips the auto-on request entirely (the physical intercom is never woken up for a stream that cannot start), FCM-triggered stream starts are ignored with a WARNING, and turning the camera on logs a clear per-entity message. Everything except live video/audio keeps working: doorbell detection, door opening, visitor photo, F1, call guard, DND, photo caller, and call history. On HA ≤ 2026.6 live video can be restored by installing `pymediasoup` manually (see README); the hard requirement will return once `aiortc` supports `av>=17`. Contributed by @kikorr (#35).

## [0.16.7] - 2026-06-19

### Fixed
- **FCM listener no longer dies in a reconnect loop on an undecryptable push** (#25) — after the #21 padding fix, a malformed `crypto-key` (`dh=`) value that decodes to an invalid P-256 point made `http_ece` raise `ValueError: Invalid EC key`, which (like any decrypt error) propagated to the upstream `_listen` catch-all and shut the client down. Because the crash happened before the message was acknowledged, MCS redelivered the same poisoned message on every reconnect — an endless crash/restart loop with no notifications. The per-message decrypt is now isolated: any decode/decrypt failure is logged and that single message is skipped (empty payload) so the upstream handler acks it and the listener stays alive to process the next push.

## [0.16.6] - 2026-06-18

### Fixed
- **FCM client no longer shuts down on a malformed push** (#21) — some Fermax FCM data messages carry a `crypto-key`/`encryption` header whose base64 value is not a multiple of 4. Upstream `firebase-messaging` pads the stored key material before decoding but not these per-message headers, so `urlsafe_b64decode` raised `binascii.Error: Incorrect padding` inside the `_listen` loop; the library's catch-all then shut the whole `FcmPushClient` down, taking push notifications offline until the watchdog restarted it (and re-killing it if the message was redelivered). The integration now right-pads both headers before the upstream decrypt runs, so the affected messages decrypt normally instead of crashing the listener.

## [0.16.5] - 2026-06-15

### Fixed
- **Signaling URL domain validation** — the `SocketUrl` delivered in FCM push notifications was used directly to open the WebSocket signaling connection. A compromised or spoofed push could redirect the connection to an attacker-controlled signaling server, exposing OAuth and FCM tokens. Signaling URLs are now validated against `*.fermax.io` (covers all known Fermax environments: pro, devel, staging, SIS); untrusted URLs are logged and replaced with the default signaling URL. Contributed by @aitoraznar (#18).
- **FCM watchdog observability** (#16) — follow-up to the reconnect-storm hardening in 0.16.4, no behavior changes to the restart state machine:
  - The "FCM listener is not running; restart scheduled" message is now logged at INFO instead of WARNING. `is_started()` is also False during seconds-long transient states (socket resets, initial connection), so a watchdog tick landing inside one produced a recurring, alarming WARNING that the next healthy tick silently cleared. WARNING is now reserved for the restart actually firing.
  - `ensure_running()` catches all exceptions around the restart (not just connection errors) and logs them with traceback. Previously, errors raised by the `register()` path (e.g. HTTP or validation errors) escaped the catch and were silently discarded by the watchdog's `return_exceptions=True` gather — a failed restart attempt left no log line at all.
  - `ensure_running()` now returns the success of the start call instead of `is_started` (which is usually still False while the freshly restarted client is connecting), matching its documented contract.
  - The traceback rate-limit filter no longer consumes throttle budget on records carrying `exc_info=(None, None, None)` (produced by `exc_info=True` outside an `except` block) — such records have no traceback to strip and now pass through untouched.

### Added
- **Dependabot** — weekly automated dependency update PRs for the Python dev toolchain and GitHub Actions (#18).

## [0.16.4] - 2026-06-11

### Fixed
- **Config flow could not render on recent Home Assistant versions** (#8, #9, #10, #11) — the credentials form schema used a custom callable HTTPS validator that `voluptuous_serialize` cannot convert (`ValueError: Unable to convert schema: <function _https_url ...>`), blocking login entirely. URL fields are now plain strings in the form schema and HTTPS validation runs after submit, returning a translated `invalid_url` error (all 10 languages). Contributed by @pespinel (#7).
- **Phantom doorbell ring on Home Assistant restart** — event entities (doorbell ring, door opened, camera on) inherited their availability from the device `connection_state`. A transient intercom disconnect, or the brief window during an HA restart, flapped them to `unavailable` and back; on recovery the `EventEntity` restores its last event (e.g. `ring`) and HA fires state triggers, so automations watching the doorbell ran with no FCM message involved. Event availability is now decoupled from connectivity (events are momentary historical markers), eliminating the flap. Real events still fire normally.
- **Hardened the FCM push client against firebase-messaging reconnect storms** (#12) — a poisoned `StreamReader` in the upstream `_listen` loop re-raises the same exception object every iteration, growing its traceback while `logging.exception` formats it inside the HA event loop; on Python 3.14 this is quadratic and pegs the core until the Supervisor watchdog kills HA.
  - A `logging.Filter` on the `firebase_messaging.fcmpushclient` logger now strips `exc_info` after 3 tracebacks per 5-minute window (records are kept as one-liners), defusing the CPU bomb even while the upstream loop spins.
  - `FcmPushClientConfig.abort_on_sequential_error_count` is back to a bounded value (3) so the client gives up instead of spinning; the watchdog restarts it with a delayed, doubling backoff (5 → 15 min cap) that resets once the listener is healthy again. A persistent server-side failure now means "push down for a while" instead of a crash loop.
- **OAuth authentication diagnostics** — non-JSON responses, HTTP errors, OAuth error payloads, and missing `access_token` are now handled explicitly with safe, redacted log messages; `invalid_client` errors point to the OAuth Basic header instead of the user's email/password (#7).
- **APK credential extraction** — `scripts/extract_credentials.py` ignores unrelated `Basic` headers from tracing/telemetry code and generates the OAuth header from `OAuthUtils.java` + `Urls.clientId()`/`Urls.clientSecret()`, detecting the production environment; extracted secrets are redacted in console output (#7).

## [0.16.3] - 2026-05-04

### Fixed
- **FCM listener no longer reconnects after 3 sequential transport errors** — the upstream `firebase_messaging` client used to abort its receiver and never reconnect, leaving the doorbell camera black: `auto_on` answered `call_starting` but the push carrying the signaling payload never arrived (#3).
  - `FcmPushClient` now uses `FcmPushClientConfig(abort_on_sequential_error_count=None)` so the library keeps retrying with its own backoff.
  - A 60s watchdog (`async_track_time_interval`) revives the listener if `is_started` flips to False; `ensure_running()` is serialised by an `asyncio.Lock` so overlapping ticks cannot spawn parallel `FcmPushClient` instances.
  - The notification grace period is intentionally not re-armed on revival — the existing `_processed_notifications` dedup deque already filters re-deliveries, and re-arming the blackout could drop a real doorbell ring landing during the window.

## [0.16.2] - 2026-04-23

### Fixed
- **Signaling URL security** — default URL upgraded to HTTPS; insecure `http://`/`ws://` URLs from push notifications are auto-upgraded to `https://`/`wss://`
- **send_audio path validation** — audio file paths are now validated against HA media directories, preventing arbitrary file reads
- **Config flow URL validation** — API and auth URLs now enforce HTTPS scheme via `vol.Url()` + scheme check
- **TTS event loop blocking** — `gTTS.save()` and fallback glob operations moved to `asyncio.to_thread()` to avoid freezing the HA event loop
- **Notification dedup** — replaced unordered `set` with `deque(maxlen=100)` to preserve insertion order and reliably evict oldest entries
- **ConfigEntryNotReady** — transient auth/network failures during setup now raise `ConfigEntryNotReady` instead of crashing, enabling automatic retry
- **Blocking I/O in async context** — `storage_path.mkdir()`, `recordings_dir.mkdir()`, and `media_source.async_resolve_media()` file checks moved to executor
- **Deprecated asyncio API** — replaced `asyncio.get_event_loop()` with `asyncio.get_running_loop()` in socket.io handler
- **Hardcoded `/media/` paths** — coordinator and streaming now use `hass.config.media_dirs` consistently
- **TTS temp file leak** — generated TTS files are cleaned up after use
- **Recording cleanup** — no longer blocks HA startup, runs as background task
- **dispatcher_send → async_dispatcher_send** — all signal dispatches use the async variant

### Changed
- **pymediasoup patch deferred** — heavy `pymediasoup`/`aiortc` imports now happen on first stream start, not at module load time
- **Sensor icons via icons.json** — removed redundant `_attr_icon` from sensor descriptors, icons.json is now the single source of truth
- **Camera icon** — added `mdi:doorbell-video` for the camera entity in `icons.json`
- **subprocess.run** — explicit `shell=False` for ffmpeg recording calls

## [0.16.1] - 2026-04-16

### Added
- **MediaSource** — doorbell photos and video recordings browsable in HA media browser, sorted newest-first with path traversal protection

## [0.16.0] - 2026-04-15

### Added
- **Descriptor-based entities** — sensors and binary sensors use `SensorTypeInfo`/`BinarySensorTypeInfo` frozen dataclasses for cleaner, data-driven entity creation
- **Optimistic state** for DnD and PhotoCaller switches — UI updates instantly before API confirmation
- **`icons.json`** — state-aware icons for all entity types (switches change icon on/off, lock changes locked/unlocked, etc.)
- **Vulture dead code analysis** in CI and `make deadcode` target
- **Coverage threshold** (`--cov-fail-under=40`) enforced in CI
- **GitHub issue templates** — structured bug report (with FCM status, diagnostics fields) and feature request

### Changed
- **Ruff rules expanded** — added isort (I), naming (N), pyupgrade (UP), bugbear (B), builtins-shadow (A), simplification (SIM), type-checking (TCH)
- **API models frozen** — all 6 dataclasses in `api.py` use `@dataclass(frozen=True)` for immutability
- **Diagnostics rewritten** with `async_redact_data()`, operational data (listener status, stream state, FCM token redacted)
- **Coordinator** uses `dataclasses.replace()` instead of manual `DeviceInfo` reconstruction

## [0.15.4] - 2026-04-15

### Fixed
- **FCM credentials** now stored via HA Store API (`.storage/`) instead of plaintext files, with non-blocking async save from sync callbacks
- **Password field** masked in config flow UI using `TextSelectorType.PASSWORD`
- **Token access** — new public `get_access_token()` method with auto-reauthentication replaces direct `_access_token` access
- **Log redaction** — recursive `_redact_notification()` masks sensitive tokens (`FermaxToken`, `fermaxOauthToken`, etc.) at all nesting levels in logs
- **Recording cleanup** uses `hass.config.media_dirs` for portable media path and runs filesystem ops off the event loop via `asyncio.to_thread()`
- **Config migration v1→v2** — auto-promotes entries that already have all required fields; shows clear error for entries that genuinely need re-setup
- **Phantom doorbell ring on reload** — 10-second grace period after FCM listener startup ignores re-delivered notifications; start time set before listener to prevent race condition
- **Temp file security** — `tempfile.mkstemp()` with atomic write+close via fd, eliminating TOCTOU race in recording mux
- **CI workflow permissions** — added `permissions: contents: read` to GitHub Actions workflow

### Fixed (scripts)
- `extract_credentials.py` — regex end-anchor fix captures PRO environment arrays after `NoWhenBranch` throw; local Byte variable resolution replaces hardcoded heuristics; Android `strings.xml` parsing for JADX-decompiled directories; `base_url` derivation from `auth_url`

## [0.15.0] - 2026-04-09

### Added
- **Call photo persistence** — visitor photos from the Fermax API are now saved to `/media/fermax_recordings/` as JPEG files alongside video recordings. Photos follow the same retention policy and auto-cleanup as MP4 recordings

## [0.14.2] - 2026-04-08

### Fixed
- DND switch showing "unknown" — now fetches DND status from API on each poll
- Camera image appearing blank after page reload — force state update on entity registration so HA knows image is available immediately

## [0.14.1] - 2026-04-08

### Fixed
- Last camera frame not persisted after stream auto-stop — frame was lost because the session was cleared before saving

## [0.14.0] - 2026-04-07

### Added
- **Stream duration control** — new slider entity on the device (10s–120s, default 30s). Stream auto-stops after the configured duration
- Translations for the stream duration entity in all 10 languages

## [0.13.0] - 2026-04-07

### Added
- **Call mode selector** — new select entity on the device with three modes:
  - **Notify only** — doorbell ring triggers events and notifications, no video or recording
  - **Record** — automatically starts video stream and records with audio when someone rings
  - **Auto-respond** — records + sends pre-configured audio file through the intercom speaker
- Translations for the call mode entity in all 10 supported languages

### Changed
- Auto-response toggle removed from integration options (replaced by the device-level call mode selector)
- Dashboard card template updated with record button and cast-to-display button

## [0.12.1] - 2026-04-06

### Fixed
- Camera preview (Autoon) was blocked when auto-response was disabled — now always starts the stream for camera preview regardless of auto-response setting

## [0.12.0] - 2026-04-06

### Changed
- **User-provided credentials** — all API and Firebase credentials must now be provided during setup. No credentials are shipped in the integration code
- Config flow now has two steps: login (email/password) + credentials (API URLs, Firebase keys)
- Removed all obfuscated/hardcoded credential constants from `const.py`

### Added
- `scripts/extract_credentials.py` — extracts Firebase credentials from the APK and attempts AES decryption of OAuth credentials from decompiled source
- `credentials.example.json` template for reference
- `make extract-credentials APK=<path>` Makefile target
- Comprehensive documentation on how to obtain credentials, including community sources for the OAuth client
- Acknowledgments section crediting the open-source Fermax community
- Translations for the credentials step in all 10 supported languages

## [0.11.0] - 2026-04-06

### Changed
- **Auto-responder as gate** — when auto-response is disabled, doorbell rings only trigger notifications without starting video stream or interacting with the intercom
- **Hot-reload options** — toggling auto-response no longer causes a full integration reload, preventing false doorbell notifications
- Duplicate FCM notification filtering on reconnect

### Fixed
- False doorbell notifications triggered when changing integration options (entity re-creation during reload fired automation triggers)

## [0.10.0] - 2026-04-06

### Added
- **Mixed audio recording** — Recordings now include both received audio (intercom/street) and sent audio (your voice/TTS) mixed into a single track

### Changed
- `send_audio()` rewritten with direct PyAV decoding + resampling (replaces MediaPlayer), feeds 960-sample chunks to switchable track
- Sent audio PCM captured directly from numpy arrays for reliable recording mix
- Sent audio (48kHz) downsampled to match received audio sample rate before mixing

## [0.9.0] - 2026-04-06

### Added
- **Call recording** — Every video stream session is automatically recorded to MP4 (video + intercom audio) in `/config/media/fermax_recordings/`
- **Auto-cleanup** — Recordings older than the retention period are automatically deleted (default: 10 days, configurable in options)
- **Auto-response on doorbell** — Configurable in options: when enabled, plays a pre-recorded audio file through the intercom speaker when someone rings
- **`fermax_blue.send_audio` service** — Send audio file (WAV/MP3) or TTS text to the intercom during an active stream
- **Cast doorbell blueprint** — Show camera on Google Nest Hub + announce on speakers when doorbell rings (`blueprints/fermax_cast_doorbell.yaml`)
- **Options flow expanded** — Recording retention (days), auto-response toggle, audio file path
- Two-way audio via mediasoup sendTransport (PCMA codec, patched pymediasoup channels bug)
- Intercom audio consumer for recording the visitor's voice
- New dependency: `gTTS>=2.5.0` for text-to-speech

### Fixed
- pymediasoup `canProduce("audio")=False` bug: `channels=None` vs `channels=1` normalization patch
- Options flow: removed manual config_entry assignment (HA sets it automatically)
- Blueprint: robust null/empty checks for optional inputs
- Blueprint: use `tts.google_translate_say` with correct field names

### Changed
- Dashboard card updated: audio send button, events row, recordings link
- Separate event entities for doorbell, door opened, camera on

## [0.8.0] - 2026-04-06

### Added
- **Two-way audio** — Send audio to the intercom speaker during an active video stream
  - Service `fermax_blue.send_audio` accepts an audio file (WAV/MP3/OGG) or a text message
  - Text messages are converted to speech via Google TTS (`gTTS`)
  - Useful for automations: "when doorbell rings, say 'I'll be right down'"
- SendTransport and audio producer via mediasoup for real-time audio delivery
- New dependency: `gTTS>=2.5.0` for text-to-speech

## [0.7.0] - 2026-04-06

### Added
- **Separate event entities** — Doorbell, door opened, and camera preview are now 3 independent event entities with distinct icons and history, instead of a single entity with mixed event types
- Door opened event fires when door is successfully opened
- Camera preview event fires when live stream starts

### Changed
- Event history now clearly shows each event type separately

## [0.6.1] - 2026-04-06

### Fixed
- Remove dead code: unused OAUTH_TIMEOUT, _pairings cache, _last_divert_response, mock_pairing fixture
- Verified with vulture (dead code detector) — no remaining dead code at 80% confidence

## [0.6.0] - 2026-04-06

### Added
- **Open door during active stream** — Door can now be opened while viewing the camera. Uses the in-call API endpoint (`/device/incall/opendoor`) automatically when a stream session is active, falling back to the standard endpoint otherwise
- **LIVE indicator overlay** — Red `● LIVE HH:MM:SS` badge on stream frames so you can always distinguish live video from a static preview
- 84 unit tests (up from 82)

## [0.5.0] - 2026-04-05

### Added
- **Call history sensor** — `sensor.last_call` shows timestamp of last doorbell ring/call with attributes: call_id, answered, photo_id, recent_calls count
- **Dashboard card template** — Ready-to-use Lovelace card in `blueprints/fermax_dashboard_card.yaml`
- **Doorbell notification blueprint** — Automation blueprint with optional camera snapshot in `blueprints/fermax_doorbell_notification.yaml`
- Call log fetched on every coordinator poll (reuses existing API endpoint)
- **10 language translations** — English, Spanish, French, Italian, Portuguese, German, Polish, Turkish, Dutch, Arabic (covering all Fermax markets)

### Changed
- 82 unit tests (up from 79)
- README rewritten: dashboard card section, blueprint docs, updated entity table, fixed How It Works section

## [0.4.3] - 2026-04-05

### Fixed
- **Camera preview after restart** — Last stream frame is persisted to disk and loaded on HA startup, so the camera card always shows the most recent image
- **Camera 503 error** — HA requires `is_on=True` to serve images; now returns True whenever a saved frame exists
- **Live stream in card** — Dynamic `is_streaming` property auto-switches the card between static preview and live MJPEG when stream starts/stops
- **Video streaming not starting** — FCM notification data nested under `data` key, use `FermaxToken` from push for signaling auth
- **Door entities** — Create buttons/locks for all doors regardless of `visible` flag (unreliable on some installations)
- **Opening history** — Correct API response field (`openDoorRegistry` not `entries`), fetch during polling
- **H264 decode warnings** — Suppressed expected warnings before first keyframe
- **ICE transport teardown** — Clean shutdown avoids `RTCIceTransport is closed` errors

### Changed
- Camera entity created for all devices (no longer requires `photocaller=True`)
- Opening history fetched on every coordinator poll (1 API call)

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
