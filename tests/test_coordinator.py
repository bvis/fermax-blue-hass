"""Tests for the Fermax Blue coordinator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fermax_blue.api import (
    AccessDoor,
    DeviceInfo,
    FermaxBlueApi,
    Pairing,
)
from custom_components.fermax_blue.const import CALL_MODE_NOTIFY, CALL_MODE_RECORD
from custom_components.fermax_blue.coordinator import (
    FermaxBlueCoordinator,
    _is_trusted_signaling_url,
)


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
