# Changelog

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
