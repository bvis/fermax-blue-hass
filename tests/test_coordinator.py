"""Tests for the Fermax Blue coordinator."""

import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.fermax_blue.api import (
    AccessDoor,
    CallLogEntry,
    DeviceInfo,
    DivertResponse,
    FermaxApiError,
    FermaxBlueApi,
    OpeningRecord,
    Pairing,
)
from custom_components.fermax_blue.const import (
    CALL_MODE_AUTO_RESPOND,
    CALL_MODE_NOTIFY,
    CALL_MODE_RECORD,
    DEFAULT_STREAM_DURATION,
    RECORDINGS_DIR,
    SIGNAL_CALL_ENDED,
    SIGNAL_CAMERA_ON,
    SIGNAL_DOOR_OPENED,
    SIGNAL_DOORBELL_RING,
)
from custom_components.fermax_blue.coordinator import (
    CAMERA_TIMEOUT_SECONDS,
    DEFAULT_PREVIEW_TIMEOUT,
    FermaxBlueCoordinator,
    _is_trusted_signaling_url,
)
from custom_components.fermax_blue.streaming import DEFAULT_SIGNALING_URL


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


@pytest.fixture
def coordinator(mock_hass, mock_api, pairing):
    """Create a coordinator with patched HA internals."""
    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coord = FermaxBlueCoordinator.__new__(FermaxBlueCoordinator)
        coord.api = mock_api
        coord.pairing = pairing
        coord.hass = mock_hass
        coord.device_info = None
        coord.notification_listener = None
        coord._last_photo = None
        coord._last_photo_id = None
        coord._doorbell_ringing = False
        coord._camera_active = False
        coord._last_divert_response = None
        coord._photo_fetch_pending = False
        coord._call_mode = CALL_MODE_NOTIFY
        coord._auto_response_file = ""
        coord._ring_preview = False
        coord._doorbell_reset_unsub = None
        coord._camera_timeout_unsub = None
        coord._dnd_enabled = None
        coord._last_opening = None
        coord._notification_start_time = None
        coord._processed_notifications = []
        coord.update_interval = None
    return coord


class TestStreamingDepsGuard:
    """Optional live-video deps: never wake the intercom when they are missing."""

    @pytest.mark.asyncio
    async def test_preview_skipped_without_deps(self, coordinator, mock_api):
        coordinator.notification_listener = MagicMock()
        coordinator.notification_listener.fcm_token = "tok"

        with patch(
            "custom_components.fermax_blue.coordinator.streaming_deps_available",
            return_value=False,
        ):
            result = await coordinator.start_camera_preview()

        assert result is None
        mock_api.auto_on.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_preview_requests_auto_on_with_deps(self, coordinator, mock_api):
        coordinator.notification_listener = MagicMock()
        coordinator.notification_listener.fcm_token = "tok"
        mock_api.auto_on = AsyncMock(return_value=None)

        with patch(
            "custom_components.fermax_blue.coordinator.streaming_deps_available",
            return_value=True,
        ):
            await coordinator.start_camera_preview()

        mock_api.auto_on.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_stream_skipped_without_deps(self, coordinator):
        coordinator._stream_session = None
        coordinator.stop_stream = AsyncMock()

        with patch(
            "custom_components.fermax_blue.coordinator.streaming_deps_available",
            return_value=False,
        ):
            await coordinator._start_stream("room1", "https://signaling-pro-duoxme.fermax.io")

        coordinator.stop_stream.assert_not_awaited()
        assert coordinator.stream_session is None


class TestCoordinatorDnd:
    """Test DND coordination."""

    @pytest.mark.asyncio
    async def test_set_dnd_calls_api(self, coordinator, mock_api):
        coordinator.notification_listener = MagicMock()
        coordinator.notification_listener.fcm_token = "tok"

        await coordinator.set_dnd(True)
        mock_api.set_dnd.assert_called_once_with("dev1", "tok", enabled=True)
        assert coordinator.dnd_enabled is True

    @pytest.mark.asyncio
    async def test_set_dnd_no_listener(self, coordinator, mock_api):
        coordinator.notification_listener = None

        await coordinator.set_dnd(True)
        mock_api.set_dnd.assert_not_called()


class TestCoordinatorF1:
    """Test F1 coordination."""

    @pytest.mark.asyncio
    async def test_press_f1_calls_api(self, coordinator, mock_api):
        await coordinator.press_f1()
        mock_api.press_f1.assert_called_once_with("dev1")


class TestCoordinatorCallGuard:
    """Test call guard coordination."""

    @pytest.mark.asyncio
    async def test_call_guard_calls_api(self, coordinator, mock_api):
        await coordinator.call_guard()
        mock_api.call_guard.assert_called_once_with("dev1")


class TestCoordinatorFcmWatchdog:
    """Test the FCM listener watchdog hook."""

    @pytest.mark.asyncio
    async def test_no_listener_is_noop(self, coordinator):
        coordinator.notification_listener = None
        await coordinator.ensure_notifications_running()

    @pytest.mark.asyncio
    async def test_delegates_to_listener(self, coordinator):
        listener = MagicMock()
        listener.ensure_running = AsyncMock(return_value=True)
        coordinator.notification_listener = listener
        coordinator._notification_start_time = 12345.0

        await coordinator.ensure_notifications_running()

        listener.ensure_running.assert_awaited_once()
        assert coordinator._notification_start_time == 12345.0


class TestCoordinatorPhotoCaller:
    """Test photo caller coordination."""

    @pytest.mark.asyncio
    async def test_set_photo_caller_calls_api(self, coordinator, mock_api):
        coordinator.device_info = DeviceInfo(
            device_id="dev1",
            connection_state="Connected",
            status="ACTIVATED",
            family="MONITOR",
            device_type="VEO-XL",
            subtype="WIFI",
            unit_number=42,
            photocaller=False,
            streaming_mode="video_call",
            is_monitor=True,
            wireless_signal=4,
        )

        await coordinator.set_photo_caller(True)
        mock_api.set_photo_caller.assert_called_once_with("dev1", enabled=True)
        assert coordinator.device_info.photocaller is True


class TestRingPreview:
    """The ring preview option starts a receive-only stream without answering."""

    def _ring(self, coordinator, persistent_id="n1"):
        notification = {
            "data": {
                "FermaxNotificationType": "Call",
                "RoomId": "room1",
                "SocketUrl": "https://signaling-pro-duoxme.fermax.io",
                "FermaxToken": "ftok",
                "PreviewTimeout": "29",
            }
        }
        with (
            patch("custom_components.fermax_blue.coordinator.async_dispatcher_send"),
            patch(
                "custom_components.fermax_blue.coordinator.async_call_later",
                return_value=MagicMock(),
            ),
        ):
            coordinator._handle_notification(notification, persistent_id)

    def test_ring_starts_receive_only_stream(self, coordinator):
        coordinator.hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())
        coordinator._ring_preview = True
        coordinator._start_stream = MagicMock()

        self._ring(coordinator)

        coordinator._start_stream.assert_called_once_with(
            "room1",
            "https://signaling-pro-duoxme.fermax.io",
            "ftok",
            receive_only=True,
            preview_timeout="29",
        )

    def test_no_stream_in_notify_mode_by_default(self, coordinator):
        coordinator.hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())
        coordinator._start_stream = MagicMock()

        self._ring(coordinator)

        coordinator._start_stream.assert_not_called()

    def test_attending_call_mode_still_picks_up(self, coordinator):
        coordinator.hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())
        coordinator._ring_preview = True
        coordinator._call_mode = CALL_MODE_RECORD
        coordinator._start_stream = MagicMock()

        self._ring(coordinator)

        assert coordinator._start_stream.call_args.kwargs["receive_only"] is False


class TestPreviewTimeoutClamp:
    """Receive-only sessions honour the server-side PreviewTimeout ceiling."""

    async def _start(self, coordinator, *, receive_only, preview_timeout, stream_duration):
        coordinator._stream_session = None
        coordinator._stream_stop_unsub = None
        coordinator._stream_duration = stream_duration
        listener = MagicMock()
        listener.fcm_token = "tok"
        coordinator.notification_listener = listener
        session = MagicMock()
        session.start = AsyncMock(return_value=True)
        with (
            patch(
                "custom_components.fermax_blue.coordinator.streaming_deps_available",
                return_value=True,
            ),
            patch(
                "custom_components.fermax_blue.coordinator.FermaxStreamSession",
                return_value=session,
            ),
            patch("custom_components.fermax_blue.coordinator.async_dispatcher_send"),
            patch(
                "custom_components.fermax_blue.coordinator.async_call_later",
                return_value=MagicMock(),
            ) as call_later,
        ):
            await coordinator._start_stream(
                "room1",
                "https://signaling-pro-duoxme.fermax.io",
                "ftok",
                receive_only=receive_only,
                preview_timeout=preview_timeout,
            )
        return call_later.call_args.args[1]

    @pytest.mark.asyncio
    async def test_clamped_to_payload_timeout(self, coordinator):
        delay = await self._start(
            coordinator, receive_only=True, preview_timeout="29", stream_duration=120
        )
        assert delay == 29

    @pytest.mark.asyncio
    async def test_defaults_to_29_when_absent(self, coordinator):
        delay = await self._start(
            coordinator, receive_only=True, preview_timeout=None, stream_duration=60
        )
        assert delay == 29

    @pytest.mark.asyncio
    async def test_shorter_stream_duration_wins(self, coordinator):
        delay = await self._start(
            coordinator, receive_only=True, preview_timeout="60", stream_duration=15
        )
        assert delay == 15

    @pytest.mark.asyncio
    async def test_invalid_timeout_falls_back(self, coordinator):
        delay = await self._start(
            coordinator, receive_only=True, preview_timeout="garbage", stream_duration=120
        )
        assert delay == 29

    @pytest.mark.asyncio
    async def test_attended_stream_not_clamped(self, coordinator):
        delay = await self._start(
            coordinator, receive_only=False, preview_timeout="29", stream_duration=120
        )
        assert delay == 120


class TestOpenDoorFallback:
    """In-call door opening falls back to the standard endpoint on failure."""

    def _active_session(self, coordinator):
        session = MagicMock()
        session.is_active = True
        session._room_id = "room1"
        coordinator._stream_session = session

    @pytest.mark.asyncio
    async def test_incall_endpoint_used_during_stream(self, coordinator, mock_api):
        self._active_session(coordinator)
        mock_api.open_door_incall = AsyncMock(return_value=True)
        with patch("custom_components.fermax_blue.coordinator.async_dispatcher_send"):
            assert await coordinator.open_door() is True
        mock_api.open_door.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_when_incall_fails(self, coordinator, mock_api):
        self._active_session(coordinator)
        mock_api.open_door_incall = AsyncMock(return_value=False)
        mock_api.open_door = AsyncMock(return_value=True)
        with patch("custom_components.fermax_blue.coordinator.async_dispatcher_send"):
            assert await coordinator.open_door() is True
        mock_api.open_door_incall.assert_awaited_once()
        mock_api.open_door.assert_awaited_once_with(
            "dev1", {"block": 100, "subblock": -1, "number": 0}
        )

    @pytest.mark.asyncio
    async def test_standard_endpoint_without_stream(self, coordinator, mock_api):
        coordinator._stream_session = None
        mock_api.open_door = AsyncMock(return_value=True)
        with patch("custom_components.fermax_blue.coordinator.async_dispatcher_send"):
            assert await coordinator.open_door() is True
        mock_api.open_door_incall.assert_not_called()


class TestCoordinatorScanInterval:
    """Test configurable scan interval."""

    def test_default_interval(self, mock_hass, mock_api, pairing):
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__"
        ) as mock_init:
            FermaxBlueCoordinator(mock_hass, mock_api, pairing)
            call_kwargs = mock_init.call_args
            assert call_kwargs.kwargs["update_interval"].total_seconds() == 300

    def test_custom_interval(self, mock_hass, mock_api, pairing):
        with patch(
            "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__"
        ) as mock_init:
            FermaxBlueCoordinator(mock_hass, mock_api, pairing, scan_interval=10)
            call_kwargs = mock_init.call_args
            assert call_kwargs.kwargs["update_interval"].total_seconds() == 600


class TestSignalingUrlValidation:
    """Test signaling URL domain validation."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://signaling-pro-duoxme.fermax.io",
            "https://signaling.fermax.io/path",
            "wss://signaling-pro-duoxme.fermax.io",
            "https://fermax.io",
        ],
    )
    def test_trusted_urls_accepted(self, url):
        assert _is_trusted_signaling_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.com",
            "https://notfermax.io",
            "https://fermax.io.evil.com",
            "https://evil-fermax.io",
            "",
            "not-a-url",
        ],
    )
    def test_untrusted_urls_rejected(self, url):
        assert _is_trusted_signaling_url(url) is False


class TestSnapshotOverlay:
    """The blue SNAPSHOT badge burned into still previews."""

    @staticmethod
    def _jpeg() -> bytes:
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (368, 288), (0, 0, 0)).save(buf, format="JPEG")
        return buf.getvalue()

    def test_badge_is_burned_into_the_photo(self):
        import io

        from PIL import Image

        out = FermaxBlueCoordinator._overlay_snapshot_indicator(self._jpeg())

        img = Image.open(io.BytesIO(out))
        # Blue badge background, white dot (JPEG is lossy, so approximate)
        r, g, b = img.getpixel((8, 8))
        assert b > 100
        assert b > r
        r, g, b = img.getpixel((17, 17))
        assert min(r, g, b) > 180

    def test_none_passthrough(self):
        assert FermaxBlueCoordinator._overlay_snapshot_indicator(None) is None

    def test_invalid_jpeg_returned_unchanged(self):
        data = b"not a jpeg"
        assert FermaxBlueCoordinator._overlay_snapshot_indicator(data) == data


class TestPersistedPreviewIsUnbadged:
    """The frame persisted to .storage must be the raw, unstamped one.

    Persisting the badged preview would resurrect a stale SNAPSHOT
    timestamp after a restart via _load_last_photo().
    """

    @pytest.mark.asyncio
    async def test_stop_stream_persists_raw_frame(self, coordinator):
        raw = TestSnapshotOverlay._jpeg()
        session = MagicMock()
        session.latest_frame = b"badged-display-frame"
        session.latest_frame_raw = raw
        session.stop = AsyncMock()
        coordinator._stream_session = session
        coordinator._save_last_photo = MagicMock(return_value=None)
        coordinator.hass.async_create_task = MagicMock()

        await coordinator.stop_stream()

        coordinator._save_last_photo.assert_called_once_with(raw)
        # the in-memory preview still gets the badge
        assert coordinator._last_photo != raw

    @pytest.mark.asyncio
    async def test_save_last_photo_prefers_explicit_photo(self, coordinator, tmp_path):
        coordinator._storage_path = tmp_path
        coordinator._last_photo = b"stamped"
        await coordinator._save_last_photo(b"raw-bytes")
        assert (tmp_path / "last_frame_dev1.jpg").read_bytes() == b"raw-bytes"

    @pytest.mark.asyncio
    async def test_save_last_photo_falls_back_to_last_photo(self, coordinator, tmp_path):
        coordinator._storage_path = tmp_path
        coordinator._last_photo = b"fallback"
        await coordinator._save_last_photo()
        assert (tmp_path / "last_frame_dev1.jpg").read_bytes() == b"fallback"


@pytest.fixture
def full_coordinator(mock_hass, mock_api, pairing):
    """Create a coordinator through the real __init__ (base class patched out).

    Unlike the hand-built ``coordinator`` fixture above, this exercises the
    real attribute initialization, so every internal field gets its true
    default (deque for processed notifications, stream duration, etc.).
    """
    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coord = FermaxBlueCoordinator(mock_hass, mock_api, pairing)
    coord.hass = mock_hass  # normally set by the patched-out base __init__
    return coord


def _close_coro_tasks(coordinator):
    """Make hass.async_create_task close the coroutine instead of running it."""
    coordinator.hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())


class TestMalformedSignalingUrl:
    """urlparse failures must be treated as untrusted, not crash the handler."""

    def test_malformed_url_rejected(self):
        # An unclosed IPv6 literal makes urlparse raise ValueError
        assert _is_trusted_signaling_url("http://[") is False


class TestCoordinatorDefaultsAndSetters:
    """Initial state exposed to entities, and the writable knobs."""

    def test_initial_state(self, full_coordinator):
        coord = full_coordinator
        assert coord.call_mode == CALL_MODE_NOTIFY
        assert coord.ring_preview is False
        assert coord.stream_duration == DEFAULT_STREAM_DURATION
        assert coord.last_photo is None
        assert coord.doorbell_ringing is False
        assert coord.camera_active is False
        assert coord.dnd_enabled is None
        assert coord.last_opening is None
        assert coord.last_call is None
        assert coord.call_log == []
        assert coord.stream_session is None

    def test_setters_round_trip(self, full_coordinator):
        coord = full_coordinator
        coord.call_mode = CALL_MODE_RECORD
        coord.ring_preview = True
        coord.stream_duration = 60
        assert coord.call_mode == CALL_MODE_RECORD
        assert coord.ring_preview is True
        assert coord.stream_duration == 60


class TestPhotoPersistence:
    """Disk persistence of the camera preview and doorbell call photos."""

    async def test_no_storage_path_is_noop(self, full_coordinator):
        assert full_coordinator._last_frame_path() is None
        await full_coordinator._save_last_photo(b"data")  # must not raise
        await full_coordinator._load_last_photo()
        assert full_coordinator.last_photo is None

    async def test_load_last_photo_restores_persisted_frame(self, full_coordinator, tmp_path):
        full_coordinator._storage_path = tmp_path
        (tmp_path / "last_frame_dev1.jpg").write_bytes(b"persisted-frame")
        await full_coordinator._load_last_photo()
        assert full_coordinator.last_photo == b"persisted-frame"

    async def test_load_last_photo_missing_file(self, full_coordinator, tmp_path):
        full_coordinator._storage_path = tmp_path
        await full_coordinator._load_last_photo()
        assert full_coordinator.last_photo is None

    async def test_save_call_photo_writes_to_recordings_dir(self, full_coordinator, tmp_path):
        full_coordinator.hass.config.media_dirs = {"local": str(tmp_path)}
        await full_coordinator._save_call_photo(b"call-photo")
        files = list((tmp_path / RECORDINGS_DIR).glob("*_photo.jpg"))
        assert len(files) == 1
        assert files[0].read_bytes() == b"call-photo"


class TestAsyncUpdateData:
    """Polling: device info, call log, ring-driven photo fetch, DND, history."""

    @staticmethod
    def _attach_listener(coordinator, token="tok"):
        listener = MagicMock()
        listener.fcm_token = token
        coordinator.notification_listener = listener

    async def test_device_info_poll_without_listener(self, full_coordinator, mock_api):
        data = await full_coordinator._async_update_data()

        assert data == {
            "device_id": "dev1",
            "connection_state": "Connected",
            "status": "ACTIVATED",
            "family": "MONITOR",
            "type": "VEO-XL",
            "subtype": "WIFI",
            "unit_number": 42,
            "photocaller": True,
            "streaming_mode": "video_call",
            "is_monitor": True,
            "wireless_signal": 4,
        }
        assert full_coordinator.device_info is mock_api.get_device_info.return_value
        # Without an FCM token neither the call log nor DND may be queried
        mock_api.get_call_log.assert_not_called()
        mock_api.get_dnd_status.assert_not_called()

    async def test_api_error_raises_update_failed(self, full_coordinator, mock_api):
        mock_api.get_device_info.side_effect = FermaxApiError("boom")
        with pytest.raises(UpdateFailed, match="Error fetching device info"):
            await full_coordinator._async_update_data()

    async def test_unexpected_error_raises_update_failed(self, full_coordinator, mock_api):
        mock_api.get_device_info.side_effect = ValueError("weird")
        with pytest.raises(UpdateFailed, match="Unexpected error"):
            await full_coordinator._async_update_data()

    async def test_ring_pending_fetches_newest_call_photo(self, full_coordinator, mock_api):
        self._attach_listener(full_coordinator)
        _close_coro_tasks(full_coordinator)
        older = CallLogEntry("c1", "dev1", datetime(2026, 8, 1, 10, 0), photo_id="p1")
        newer = CallLogEntry("c2", "dev1", datetime(2026, 8, 9, 10, 0), photo_id="p2")
        mock_api.get_call_log = AsyncMock(return_value=[older, newer])
        mock_api.get_call_photo = AsyncMock(return_value=b"raw-photo")
        mock_api.get_dnd_status = AsyncMock(return_value=True)
        mock_api.get_opening_history = AsyncMock(
            return_value=[
                OpeningRecord(timestamp="2026-08-09T10:00:00Z", user="app", door="ZERO"),
            ]
        )
        full_coordinator._photo_fetch_pending = True

        await full_coordinator._async_update_data()

        mock_api.get_call_log.assert_awaited_once_with("tok")
        assert full_coordinator.last_call is newer  # newest by date, not list order
        assert full_coordinator.call_log == [older, newer]
        mock_api.get_call_photo.assert_awaited_once_with("p2")
        # Overlay is a no-op on non-JPEG bytes, so the raw photo comes through
        assert full_coordinator.last_photo == b"raw-photo"
        assert full_coordinator._last_photo_id == "p2"
        assert full_coordinator._photo_fetch_pending is False
        assert full_coordinator.dnd_enabled is True
        assert full_coordinator.last_opening.door == "ZERO"
        # The clean copy is scheduled for /media persistence
        full_coordinator.hass.async_create_task.assert_called_once()

    async def test_photo_skipped_when_already_seen(self, full_coordinator, mock_api):
        self._attach_listener(full_coordinator)
        entry = CallLogEntry("c1", "dev1", datetime(2026, 8, 9), photo_id="p1")
        mock_api.get_call_log = AsyncMock(return_value=[entry])
        full_coordinator._photo_fetch_pending = True
        full_coordinator._last_photo_id = "p1"

        await full_coordinator._async_update_data()

        mock_api.get_call_photo.assert_not_called()
        assert full_coordinator._photo_fetch_pending is False

    async def test_photo_not_fetched_without_pending_ring(self, full_coordinator, mock_api):
        self._attach_listener(full_coordinator)
        entry = CallLogEntry("c1", "dev1", datetime(2026, 8, 9), photo_id="p1")
        mock_api.get_call_log = AsyncMock(return_value=[entry])

        await full_coordinator._async_update_data()

        mock_api.get_call_photo.assert_not_called()
        assert full_coordinator.last_call is entry

    async def test_side_channel_failures_do_not_break_poll(self, full_coordinator, mock_api):
        self._attach_listener(full_coordinator)
        mock_api.get_call_log = AsyncMock(side_effect=RuntimeError("log down"))
        mock_api.get_dnd_status = AsyncMock(side_effect=RuntimeError("dnd down"))
        mock_api.get_opening_history = AsyncMock(side_effect=RuntimeError("history down"))

        data = await full_coordinator._async_update_data()

        assert data["device_id"] == "dev1"
        assert full_coordinator.dnd_enabled is None
        assert full_coordinator.last_opening is None


class TestNotificationLifecycle:
    """FCM listener setup and teardown, including app-token registration."""

    async def test_setup_registers_token_and_starts_listener(
        self, full_coordinator, mock_api, tmp_path
    ):
        (tmp_path / "last_frame_dev1.jpg").write_bytes(b"persisted-frame")
        listener = MagicMock()
        listener.register = AsyncMock(return_value="fcm_tok")
        listener.start = AsyncMock()
        with patch(
            "custom_components.fermax_blue.coordinator.FermaxNotificationListener",
            return_value=listener,
        ) as listener_cls:
            await full_coordinator.setup_notifications(tmp_path)

        callback_arg = listener_cls.call_args.kwargs["notification_callback"]
        assert callback_arg == full_coordinator._handle_notification
        mock_api.register_app_token.assert_awaited_once_with("fcm_tok", active=True)
        listener.start.assert_awaited_once()
        assert full_coordinator._notification_start_time is not None
        assert full_coordinator._storage_path == tmp_path
        # Persisted camera frame restored for the preview
        assert full_coordinator.last_photo == b"persisted-frame"

    async def test_setup_without_token_does_not_register(
        self, full_coordinator, mock_api, tmp_path
    ):
        listener = MagicMock()
        listener.register = AsyncMock(return_value=None)
        listener.start = AsyncMock()
        with patch(
            "custom_components.fermax_blue.coordinator.FermaxNotificationListener",
            return_value=listener,
        ):
            await full_coordinator.setup_notifications(tmp_path)

        mock_api.register_app_token.assert_not_called()
        listener.start.assert_not_awaited()
        assert full_coordinator._notification_start_time is None

    async def test_stop_unregisters_token(self, full_coordinator, mock_api):
        listener = MagicMock()
        listener.fcm_token = "fcm_tok"
        listener.stop = AsyncMock()
        full_coordinator.notification_listener = listener

        await full_coordinator.stop_notifications()

        mock_api.register_app_token.assert_awaited_once_with("fcm_tok", active=False)
        listener.stop.assert_awaited_once()

    async def test_stop_without_listener_is_noop(self, full_coordinator, mock_api):
        await full_coordinator.stop_notifications()
        mock_api.register_app_token.assert_not_called()

    async def test_stop_without_token_still_stops_listener(self, full_coordinator, mock_api):
        listener = MagicMock()
        listener.fcm_token = None
        listener.stop = AsyncMock()
        full_coordinator.notification_listener = listener

        await full_coordinator.stop_notifications()

        mock_api.register_app_token.assert_not_called()
        listener.stop.assert_awaited_once()


class TestHandleNotification:
    """FCM push handling: dedup, grace period, ACK, ring state, stream kick-off."""

    @staticmethod
    def _call_data(**overrides):
        data = {
            "FermaxNotificationType": "Call",
            "RoomId": "room1",
            "SocketUrl": "https://signaling-pro-duoxme.fermax.io",
            "FermaxToken": "ftok",
            "PreviewTimeout": "29",
        }
        data.update(overrides)
        return data

    def _fire(self, coordinator, data, persistent_id="n1"):
        with (
            patch("custom_components.fermax_blue.coordinator.async_dispatcher_send") as dispatch,
            patch(
                "custom_components.fermax_blue.coordinator.async_call_later",
                return_value=MagicMock(),
            ) as call_later,
        ):
            coordinator._handle_notification({"data": data}, persistent_id)
        return dispatch, call_later

    def test_grace_period_swallows_redelivered_ring(self, full_coordinator, mock_api):
        _close_coro_tasks(full_coordinator)
        full_coordinator._notification_start_time = time.monotonic()

        self._fire(full_coordinator, self._call_data())

        assert full_coordinator.doorbell_ringing is False
        mock_api.ack_notification.assert_not_called()
        assert len(full_coordinator._processed_notifications) == 0

    def test_duplicate_persistent_id_processed_once(self, full_coordinator, mock_api):
        _close_coro_tasks(full_coordinator)

        self._fire(full_coordinator, self._call_data(), persistent_id="dup")
        self._fire(full_coordinator, self._call_data(), persistent_id="dup")

        assert mock_api.ack_notification.call_count == 1
        assert list(full_coordinator._processed_notifications).count("dup") == 1

    def test_ring_marks_state_and_dispatches_door_signal(self, full_coordinator, mock_api):
        _close_coro_tasks(full_coordinator)

        dispatch, _ = self._fire(
            full_coordinator, self._call_data(AccessDoorKey="ZERO"), persistent_id="ring1"
        )

        assert full_coordinator.doorbell_ringing is True
        assert full_coordinator._photo_fetch_pending is True
        dispatch.assert_called_once_with(
            full_coordinator.hass, SIGNAL_DOORBELL_RING.format("dev1", "ZERO")
        )
        # ACK falls back to the persistent id and flags the message as a call
        mock_api.ack_notification.assert_called_once_with("ring1", is_call=True)

    def test_ring_reset_callback_clears_state(self, full_coordinator):
        _close_coro_tasks(full_coordinator)
        full_coordinator.async_set_updated_data = MagicMock()
        full_coordinator.data = {}
        stale_timer = MagicMock()
        full_coordinator._doorbell_reset_unsub = stale_timer

        with (
            patch("custom_components.fermax_blue.coordinator.async_dispatcher_send") as dispatch,
            patch(
                "custom_components.fermax_blue.coordinator.async_call_later",
                return_value=MagicMock(),
            ) as call_later,
        ):
            full_coordinator._handle_notification({"data": self._call_data()}, "r1")
            assert full_coordinator.doorbell_ringing is True
            stale_timer.assert_called_once()  # pending reset timer cancelled
            reset_cb = call_later.call_args.args[2]
            reset_cb(None)

        assert full_coordinator.doorbell_ringing is False
        assert full_coordinator._doorbell_reset_unsub is None
        full_coordinator.async_set_updated_data.assert_called_once_with({})
        dispatch.assert_any_call(full_coordinator.hass, SIGNAL_CALL_ENDED.format("dev1"))

    def test_untrusted_socket_url_replaced_with_default(self, full_coordinator):
        _close_coro_tasks(full_coordinator)
        full_coordinator.ring_preview = True
        full_coordinator._start_stream = MagicMock()

        self._fire(full_coordinator, self._call_data(SocketUrl="https://evil.com"))

        assert full_coordinator._start_stream.call_args.args[1] == DEFAULT_SIGNALING_URL

    def test_autoon_starts_stream_without_ring(self, full_coordinator, mock_api):
        _close_coro_tasks(full_coordinator)
        full_coordinator._start_stream = MagicMock()

        dispatch, _ = self._fire(
            full_coordinator,
            {
                "FermaxNotificationType": "Autoon",
                "RoomId": "room9",
                "SocketUrl": "https://signaling-pro-duoxme.fermax.io",
                "FermaxToken": "ftok",
            },
            persistent_id="auto1",
        )

        assert full_coordinator._start_stream.call_args.args[0] == "room9"
        assert full_coordinator._start_stream.call_args.kwargs["receive_only"] is False
        assert full_coordinator.doorbell_ringing is False
        assert full_coordinator._photo_fetch_pending is False
        dispatch.assert_not_called()  # no doorbell ring signal for auto-on
        mock_api.ack_notification.assert_called_once_with("auto1", is_call=False)

    def test_auto_respond_mode_schedules_response(self, full_coordinator):
        _close_coro_tasks(full_coordinator)
        full_coordinator.call_mode = CALL_MODE_AUTO_RESPOND
        full_coordinator._auto_response_file = "/config/answer.mp3"
        full_coordinator._start_stream = MagicMock()
        full_coordinator._auto_respond = MagicMock()

        self._fire(full_coordinator, self._call_data())

        assert full_coordinator._start_stream.call_args.kwargs["receive_only"] is False
        full_coordinator._auto_respond.assert_called_once()

    def test_auto_respond_skipped_without_audio_file(self, full_coordinator):
        _close_coro_tasks(full_coordinator)
        full_coordinator.call_mode = CALL_MODE_AUTO_RESPOND
        full_coordinator._auto_response_file = ""
        full_coordinator._start_stream = MagicMock()
        full_coordinator._auto_respond = MagicMock()

        self._fire(full_coordinator, self._call_data())

        full_coordinator._start_stream.assert_called_once()
        full_coordinator._auto_respond.assert_not_called()


class TestOpenDoorSelection:
    """Door lookup: named door, first-available fallback, and the empty case."""

    async def test_unknown_door_falls_back_to_first_available(self, full_coordinator, mock_api):
        mock_api.open_door = AsyncMock(return_value=True)

        with patch("custom_components.fermax_blue.coordinator.async_dispatcher_send") as dispatch:
            assert await full_coordinator.open_door("SIDE") is True

        mock_api.open_door.assert_awaited_once_with(
            "dev1", {"block": 100, "subblock": -1, "number": 0}
        )
        dispatch.assert_called_once_with(full_coordinator.hass, SIGNAL_DOOR_OPENED.format("dev1"))

    async def test_no_doors_configured_returns_false(self, full_coordinator, mock_api):
        full_coordinator.pairing = Pairing(
            device_id="dev1", tag="Home", installation_id="inst_1", access_doors={}
        )

        with patch("custom_components.fermax_blue.coordinator.async_dispatcher_send") as dispatch:
            assert await full_coordinator.open_door() is False

        mock_api.open_door.assert_not_called()
        dispatch.assert_not_called()

    async def test_failed_open_does_not_signal(self, full_coordinator, mock_api):
        mock_api.open_door = AsyncMock(return_value=False)

        with patch("custom_components.fermax_blue.coordinator.async_dispatcher_send") as dispatch:
            assert await full_coordinator.open_door() is False

        dispatch.assert_not_called()


class TestCameraPreviewLifecycle:
    """Auto-on preview: activation, timeout-driven deactivation, failures."""

    async def test_no_fcm_token_aborts(self, full_coordinator, mock_api):
        assert await full_coordinator.start_camera_preview() is None
        mock_api.auto_on.assert_not_called()

    async def test_successful_preview_activates_camera_with_timeout(
        self, full_coordinator, mock_api
    ):
        listener = MagicMock()
        listener.fcm_token = "tok"
        full_coordinator.notification_listener = listener
        divert = DivertResponse(
            reason="call_starting",
            divert_service="blueStream",
            code=1.0,
            description="Auto on is starting",
            directed_to="tok",
        )
        mock_api.auto_on = AsyncMock(return_value=divert)
        full_coordinator.async_set_updated_data = MagicMock()
        full_coordinator.data = {}
        stale_timer = MagicMock()
        full_coordinator._camera_timeout_unsub = stale_timer

        with (
            patch(
                "custom_components.fermax_blue.coordinator.streaming_deps_available",
                return_value=True,
            ),
            patch(
                "custom_components.fermax_blue.coordinator.async_call_later",
                return_value=MagicMock(),
            ) as call_later,
        ):
            result = await full_coordinator.start_camera_preview()
            assert result is divert
            assert full_coordinator.camera_active is True
            stale_timer.assert_called_once()  # previous timeout cancelled
            mock_api.auto_on.assert_awaited_once_with("dev1", "tok")
            assert call_later.call_args.args[1] == CAMERA_TIMEOUT_SECONDS
            deactivate = call_later.call_args.args[2]
            deactivate(None)

        assert full_coordinator.camera_active is False
        assert full_coordinator._camera_timeout_unsub is None
        assert full_coordinator.async_set_updated_data.call_count == 2

    async def test_failed_auto_on_leaves_camera_off(self, full_coordinator, mock_api):
        listener = MagicMock()
        listener.fcm_token = "tok"
        full_coordinator.notification_listener = listener
        mock_api.auto_on = AsyncMock(return_value=None)

        with patch(
            "custom_components.fermax_blue.coordinator.streaming_deps_available",
            return_value=True,
        ):
            assert await full_coordinator.start_camera_preview() is None

        assert full_coordinator.camera_active is False


class TestChangeVideoSource:
    """Video source change requires an FCM token and returns the API result."""

    async def test_no_listener_returns_none(self, full_coordinator, mock_api):
        assert await full_coordinator.change_video_source() is None
        mock_api.change_video_source.assert_not_called()

    async def test_delegates_with_token(self, full_coordinator, mock_api):
        listener = MagicMock()
        listener.fcm_token = "tok"
        full_coordinator.notification_listener = listener
        divert = DivertResponse(
            reason="call_starting",
            divert_service="blueStream",
            code=1.0,
            description="Change video source",
            directed_to="tok",
        )
        mock_api.change_video_source = AsyncMock(return_value=divert)

        assert await full_coordinator.change_video_source() is divert
        mock_api.change_video_source.assert_awaited_once_with("dev1", "tok")


class TestStreamLifecycle:
    """Stream session bring-up, auto-stop timer, and end-of-stream cleanup."""

    async def _start(
        self,
        coordinator,
        *,
        fermax_token="ftok",
        receive_only=False,
        preview_timeout=None,
        start_ok=True,
    ):
        listener = MagicMock()
        listener.fcm_token = "fcm_tok"
        coordinator.notification_listener = listener
        session = MagicMock()
        session.start = AsyncMock(return_value=start_ok)
        with (
            patch(
                "custom_components.fermax_blue.coordinator.streaming_deps_available",
                return_value=True,
            ),
            patch(
                "custom_components.fermax_blue.coordinator.FermaxStreamSession",
                return_value=session,
            ) as session_cls,
            patch("custom_components.fermax_blue.coordinator.async_dispatcher_send") as dispatch,
            patch(
                "custom_components.fermax_blue.coordinator.async_call_later",
                return_value=MagicMock(),
            ) as call_later,
        ):
            await coordinator._start_stream(
                "room1",
                "https://signaling-pro-duoxme.fermax.io",
                fermax_token,
                receive_only=receive_only,
                preview_timeout=preview_timeout,
            )
        return session, session_cls, dispatch, call_later

    async def test_no_listener_never_builds_session(self, full_coordinator):
        with (
            patch(
                "custom_components.fermax_blue.coordinator.streaming_deps_available",
                return_value=True,
            ),
            patch("custom_components.fermax_blue.coordinator.FermaxStreamSession") as session_cls,
        ):
            await full_coordinator._start_stream("room1", "https://signaling-pro-duoxme.fermax.io")

        session_cls.assert_not_called()
        assert full_coordinator.stream_session is None

    async def test_no_fcm_token_never_builds_session(self, full_coordinator, mock_api):
        listener = MagicMock()
        listener.fcm_token = None
        full_coordinator.notification_listener = listener

        with (
            patch(
                "custom_components.fermax_blue.coordinator.streaming_deps_available",
                return_value=True,
            ),
            patch("custom_components.fermax_blue.coordinator.FermaxStreamSession") as session_cls,
        ):
            await full_coordinator._start_stream("room1", "https://signaling-pro-duoxme.fermax.io")

        session_cls.assert_not_called()
        mock_api.get_access_token.assert_not_called()

    async def test_oauth_token_fetched_when_no_push_token(self, full_coordinator, mock_api):
        mock_api.get_access_token = AsyncMock(return_value="oauth-tok")

        _, session_cls, dispatch, _ = await self._start(full_coordinator, fermax_token="")

        assert session_cls.call_args.kwargs["oauth_token"] == "oauth-tok"
        assert session_cls.call_args.kwargs["fcm_token"] == "fcm_tok"
        assert full_coordinator.camera_active is True
        dispatch.assert_called_once_with(full_coordinator.hass, SIGNAL_CAMERA_ON.format("dev1"))

    async def test_push_token_used_directly(self, full_coordinator, mock_api):
        _, session_cls, _, _ = await self._start(full_coordinator, fermax_token="ftok")

        assert session_cls.call_args.kwargs["oauth_token"] == "ftok"
        mock_api.get_access_token.assert_not_called()

    async def test_start_failure_clears_session(self, full_coordinator):
        _, _, dispatch, call_later = await self._start(full_coordinator, start_ok=False)

        assert full_coordinator.stream_session is None
        assert full_coordinator.camera_active is False
        dispatch.assert_not_called()
        call_later.assert_not_called()

    async def test_zero_preview_timeout_falls_back_to_default(self, full_coordinator):
        full_coordinator.stream_duration = 120

        _, _, _, call_later = await self._start(
            full_coordinator, receive_only=True, preview_timeout="0"
        )

        assert call_later.call_args.args[1] == DEFAULT_PREVIEW_TIMEOUT

    async def test_auto_stop_timer_stops_stream(self, full_coordinator):
        _, _, _, call_later = await self._start(full_coordinator)
        auto_stop = call_later.call_args.args[2]
        _close_coro_tasks(full_coordinator)
        full_coordinator.stop_stream = MagicMock()

        auto_stop(None)

        assert full_coordinator._stream_stop_unsub is None
        full_coordinator.stop_stream.assert_called_once()

    async def test_stream_end_saves_preview_and_resets_state(self, full_coordinator):
        session, session_cls, _, call_later = await self._start(full_coordinator)
        session.latest_frame = b"display-frame"
        session.latest_frame_raw = b"raw-frame"
        stop_timer = call_later.return_value
        full_coordinator.async_set_updated_data = MagicMock()
        full_coordinator.data = {}
        full_coordinator._save_last_photo = MagicMock(return_value=None)
        full_coordinator.hass.async_create_task = MagicMock()
        on_end = session_cls.call_args.kwargs["on_end"]

        on_end()

        stop_timer.assert_called_once()  # pending auto-stop timer cancelled
        assert full_coordinator._stream_stop_unsub is None
        assert full_coordinator.stream_session is None
        assert full_coordinator.camera_active is False
        # Overlay is a no-op on non-JPEG bytes, and the raw frame is persisted
        assert full_coordinator.last_photo == b"raw-frame"
        full_coordinator._save_last_photo.assert_called_once_with(b"raw-frame")
        full_coordinator.async_set_updated_data.assert_called_once_with({})


class TestAutoRespond:
    """The auto-response audio is sent only once the stream is up."""

    async def test_sends_audio_once_stream_is_active(self, full_coordinator):
        session = MagicMock()
        session.is_active = True
        session.send_audio = AsyncMock()
        full_coordinator._stream_session = session
        full_coordinator._auto_response_file = "/config/answer.mp3"

        with patch("custom_components.fermax_blue.coordinator.asyncio.sleep", new=AsyncMock()):
            await full_coordinator._auto_respond()

        session.send_audio.assert_awaited_once_with("/config/answer.mp3")

    async def test_gives_up_when_stream_never_starts(self, full_coordinator):
        full_coordinator._stream_session = None
        full_coordinator._auto_response_file = "/config/answer.mp3"

        with patch(
            "custom_components.fermax_blue.coordinator.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            await full_coordinator._auto_respond()

        assert sleep.await_count == 20  # polled the whole window, then bailed
