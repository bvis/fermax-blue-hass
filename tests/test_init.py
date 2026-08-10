"""Tests for the Fermax Blue integration entry point (__init__.py)."""

from __future__ import annotations

import os
import sys
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.fermax_blue import (
    _async_options_updated,
    _generate_tts_audio,
    async_migrate_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.fermax_blue.const import (
    CONF_FERMAX_AUTH_BASIC,
    CONF_FERMAX_AUTH_URL,
    CONF_FERMAX_BASE_URL,
    CONF_FIREBASE_API_KEY,
    CONF_FIREBASE_APP_ID,
    CONF_FIREBASE_PACKAGE_NAME,
    CONF_FIREBASE_PROJECT_ID,
    CONF_FIREBASE_SENDER_ID,
    CONF_RECORDING_RETENTION,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FCM_WATCHDOG_INTERVAL,
    PLATFORMS,
    RECORDINGS_DIR,
)

MODULE = "custom_components.fermax_blue"

DAY = 86400


@pytest.fixture
def mock_hass(tmp_path):
    """Return a lightweight fake HomeAssistant with real dict-based hass.data."""
    hass = MagicMock()
    hass.data = {}
    hass.config.config_dir = str(tmp_path)
    hass.config.media_dirs = {"local": str(tmp_path / "media")}
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock()
    hass.config_entries.async_update_entry = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.bus.async_listen_once = MagicMock(return_value=MagicMock())
    # Setup fires a one-off cleanup coroutine; close it to avoid warnings.
    hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())
    return hass


@pytest.fixture
def entry():
    """Return a fake config entry with full v2 data."""
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.version = 2
    entry.data = {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "secret",
        CONF_FERMAX_AUTH_URL: "https://oauth.example.com/oauth/token",
        CONF_FERMAX_BASE_URL: "https://api.example.com",
        CONF_FERMAX_AUTH_BASIC: "Basic abc",
        CONF_FIREBASE_API_KEY: "AIza-key",
        CONF_FIREBASE_SENDER_ID: "123456789012",
        CONF_FIREBASE_APP_ID: "1:123:android:abc",
        CONF_FIREBASE_PROJECT_ID: "proj",
        CONF_FIREBASE_PACKAGE_NAME: "com.fermax.blue.app",
    }
    entry.options = {}
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock(return_value=MagicMock())
    return entry


def _make_coordinator():
    """Return a coordinator mock exposing what __init__.py relies on."""
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.setup_notifications = AsyncMock()
    coordinator.stop_notifications = AsyncMock()
    coordinator.ensure_notifications_running = AsyncMock()
    coordinator.api = AsyncMock()
    coordinator.stream_session = None
    coordinator.update_interval = timedelta(minutes=DEFAULT_SCAN_INTERVAL)
    return coordinator


async def _run_setup(mock_hass, entry, api, coordinators):
    """Run async_setup_entry with the module boundaries patched out."""
    with (
        patch(f"{MODULE}.create_async_httpx_client", return_value=MagicMock()),
        patch(f"{MODULE}.FermaxBlueApi", return_value=api) as api_cls,
        patch(f"{MODULE}.FermaxBlueCoordinator", side_effect=list(coordinators)) as coord_cls,
        patch(f"{MODULE}.async_track_time_interval", return_value=MagicMock()) as track,
    ):
        result = await async_setup_entry(mock_hass, entry)
    return result, api_cls, coord_cls, track


def _tracked_callback(track, interval):
    """Return the periodic callback registered for the given interval."""
    for call in track.call_args_list:
        if call.args[2] == interval:
            return call.args[1]
    raise AssertionError(f"no periodic callback registered for {interval}")


def _service_call(**data):
    """Return a fake ServiceCall carrying the given data."""
    call = MagicMock()
    call.data = data
    return call


def _activate_stream(coordinator):
    """Give the coordinator an active stream session; return it."""
    session = MagicMock()
    session.is_active = True
    session.send_audio = AsyncMock()
    coordinator.stream_session = session
    return session


async def _registered_service_handler(mock_hass, entry, mock_api, coordinator):
    """Run setup and return the registered send_audio service handler."""
    await _run_setup(mock_hass, entry, mock_api, [coordinator])
    call = mock_hass.services.async_register.call_args
    assert call.args[0] == DOMAIN
    assert call.args[1] == "send_audio"
    return call.args[2]


class TestMigration:
    """async_migrate_entry version handling."""

    async def test_v1_with_all_v2_fields_is_promoted(self, mock_hass, entry):
        entry.version = 1

        assert await async_migrate_entry(mock_hass, entry) is True
        mock_hass.config_entries.async_update_entry.assert_called_once_with(entry, version=2)

    async def test_v1_without_credentials_cannot_migrate(self, mock_hass, entry):
        entry.version = 1
        entry.data = {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"}

        assert await async_migrate_entry(mock_hass, entry) is False
        mock_hass.config_entries.async_update_entry.assert_not_called()

    async def test_current_version_is_untouched(self, mock_hass, entry):
        assert await async_migrate_entry(mock_hass, entry) is True
        mock_hass.config_entries.async_update_entry.assert_not_called()


class TestSetupEntry:
    """The happy path of async_setup_entry."""

    async def test_api_is_built_from_entry_data_and_probed(self, mock_hass, entry, mock_api):
        result, api_cls, _, _ = await _run_setup(mock_hass, entry, mock_api, [_make_coordinator()])

        assert result is True
        args = api_cls.call_args
        assert args.args[0] == "user@example.com"
        assert args.args[1] == "secret"
        assert args.kwargs["auth_url"] == "https://oauth.example.com/oauth/token"
        assert args.kwargs["base_url"] == "https://api.example.com"
        assert args.kwargs["auth_basic"] == "Basic abc"
        mock_api.authenticate.assert_awaited_once()
        mock_api.get_pairings.assert_awaited_once()

    async def test_coordinator_created_per_pairing_and_started(self, mock_hass, entry, mock_api):
        coordinator = _make_coordinator()
        pairing = mock_api.get_pairings.return_value[0]

        _, _, coord_cls, _ = await _run_setup(mock_hass, entry, mock_api, [coordinator])

        args = coord_cls.call_args
        assert args.args[0] is mock_hass
        assert args.args[1] is mock_api
        assert args.args[2] is pairing
        assert args.args[3] == DEFAULT_SCAN_INTERVAL
        assert args.args[4] == ""
        assert args.args[5] == {
            "firebase_api_key": "AIza-key",
            "firebase_sender_id": 123456789012,
            "firebase_app_id": "1:123:android:abc",
            "firebase_project_id": "proj",
            "firebase_package_name": "com.fermax.blue.app",
        }
        coordinator.async_config_entry_first_refresh.assert_awaited_once()
        storage = Path(mock_hass.config.config_dir) / ".storage" / DOMAIN
        coordinator.setup_notifications.assert_awaited_once_with(storage)
        assert storage.is_dir()

    async def test_coordinators_stored_and_platforms_forwarded(self, mock_hass, entry, mock_api):
        coordinator = _make_coordinator()

        await _run_setup(mock_hass, entry, mock_api, [coordinator])

        assert entry.runtime_data == [coordinator]
        assert mock_hass.data[DOMAIN][entry.entry_id] == [coordinator]
        mock_hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
            entry, PLATFORMS
        )

    async def test_options_override_scan_interval_and_auto_response(
        self, mock_hass, entry, mock_api
    ):
        entry.options = {CONF_SCAN_INTERVAL: 15, "auto_response_file": "/media/hello.mp3"}

        _, _, coord_cls, _ = await _run_setup(mock_hass, entry, mock_api, [_make_coordinator()])

        args = coord_cls.call_args
        assert args.args[3] == 15
        assert args.args[4] == "/media/hello.mp3"

    async def test_send_audio_service_registered_once(self, mock_hass, entry, mock_api):
        await _run_setup(mock_hass, entry, mock_api, [_make_coordinator()])

        call = mock_hass.services.async_register.call_args
        assert call.args[0] == DOMAIN
        assert call.args[1] == "send_audio"

    async def test_send_audio_not_reregistered(self, mock_hass, entry, mock_api):
        mock_hass.services.has_service.return_value = True

        await _run_setup(mock_hass, entry, mock_api, [_make_coordinator()])

        mock_hass.services.async_register.assert_not_called()

    async def test_listeners_and_timers_registered_for_unload(self, mock_hass, entry, mock_api):
        _, _, _, track = await _run_setup(mock_hass, entry, mock_api, [_make_coordinator()])

        intervals = [call.args[2] for call in track.call_args_list]
        assert timedelta(hours=24) in intervals  # recording cleanup
        assert timedelta(seconds=FCM_WATCHDOG_INTERVAL) in intervals  # FCM watchdog
        assert mock_hass.bus.async_listen_once.call_args.args[0] == EVENT_HOMEASSISTANT_STOP
        entry.add_update_listener.assert_called_once_with(_async_options_updated)
        # cleanup timer + watchdog timer + stop listener + update listener
        assert entry.async_on_unload.call_count == 4
        mock_hass.async_create_task.assert_called_once()  # startup cleanup pass


class TestSetupEntryFailure:
    """async_setup_entry error paths."""

    async def test_auth_failure_raises_not_ready_and_closes_api(self, mock_hass, entry, mock_api):
        mock_api.authenticate.side_effect = OSError("network down")

        with (
            patch(f"{MODULE}.create_async_httpx_client", return_value=MagicMock()),
            patch(f"{MODULE}.FermaxBlueApi", return_value=mock_api),
            pytest.raises(ConfigEntryNotReady, match="Failed to connect"),
        ):
            await async_setup_entry(mock_hass, entry)

        mock_api.close.assert_awaited_once()
        assert DOMAIN not in mock_hass.data
        mock_hass.config_entries.async_forward_entry_setups.assert_not_awaited()

    async def test_pairing_failure_raises_not_ready_and_closes_api(
        self, mock_hass, entry, mock_api
    ):
        mock_api.get_pairings.side_effect = RuntimeError("api 500")

        with (
            patch(f"{MODULE}.create_async_httpx_client", return_value=MagicMock()),
            patch(f"{MODULE}.FermaxBlueApi", return_value=mock_api),
            pytest.raises(ConfigEntryNotReady),
        ):
            await async_setup_entry(mock_hass, entry)

        mock_api.close.assert_awaited_once()


class TestSendAudioService:
    """The send_audio service handler registered at setup."""

    async def test_valid_file_sent_to_active_stream(self, mock_hass, entry, mock_api, tmp_path):
        coordinator = _make_coordinator()
        handler = await _registered_service_handler(mock_hass, entry, mock_api, coordinator)
        session = _activate_stream(coordinator)
        media_dir = tmp_path / "media"
        media_dir.mkdir(exist_ok=True)
        clip = media_dir / "clip.mp3"
        clip.write_bytes(b"audio")

        await handler(_service_call(audio_file=str(clip)))

        session.send_audio.assert_awaited_once_with(str(clip))
        assert clip.exists()  # user files are never deleted

    async def test_path_outside_media_dirs_rejected(self, mock_hass, entry, mock_api):
        coordinator = _make_coordinator()
        handler = await _registered_service_handler(mock_hass, entry, mock_api, coordinator)
        session = _activate_stream(coordinator)

        await handler(_service_call(audio_file="/etc/passwd"))

        session.send_audio.assert_not_awaited()

    async def test_unresolvable_path_rejected(self, mock_hass, entry, mock_api):
        coordinator = _make_coordinator()
        handler = await _registered_service_handler(mock_hass, entry, mock_api, coordinator)
        session = _activate_stream(coordinator)

        # An embedded null byte makes Path.resolve() raise ValueError
        await handler(_service_call(audio_file="bad\x00path.mp3"))

        session.send_audio.assert_not_awaited()

    async def test_requires_audio_file_or_message(self, mock_hass, entry, mock_api):
        coordinator = _make_coordinator()
        handler = await _registered_service_handler(mock_hass, entry, mock_api, coordinator)
        session = _activate_stream(coordinator)

        await handler(_service_call())

        session.send_audio.assert_not_awaited()

    async def test_no_active_stream_sends_nothing(self, mock_hass, entry, mock_api):
        coordinator = _make_coordinator()
        handler = await _registered_service_handler(mock_hass, entry, mock_api, coordinator)
        session = _activate_stream(coordinator)
        session.is_active = False

        with patch(f"{MODULE}._generate_tts_audio", AsyncMock()) as generate:
            await handler(_service_call(message="anyone home?"))

        session.send_audio.assert_not_awaited()
        generate.assert_not_awaited()

    async def test_message_generates_tts_and_cleans_up(self, mock_hass, entry, mock_api, tmp_path):
        coordinator = _make_coordinator()
        handler = await _registered_service_handler(mock_hass, entry, mock_api, coordinator)
        session = _activate_stream(coordinator)
        tts_file = tmp_path / "tts.mp3"
        tts_file.write_bytes(b"speech")

        with patch(
            f"{MODULE}._generate_tts_audio", AsyncMock(return_value=str(tts_file))
        ) as generate:
            await handler(_service_call(message="hello", language="en"))

        generate.assert_awaited_once_with(mock_hass, "hello", "en")
        session.send_audio.assert_awaited_once_with(str(tts_file))
        assert not tts_file.exists()  # generated temp file is removed after use

    async def test_tts_failure_sends_nothing(self, mock_hass, entry, mock_api):
        coordinator = _make_coordinator()
        handler = await _registered_service_handler(mock_hass, entry, mock_api, coordinator)
        session = _activate_stream(coordinator)

        with patch(f"{MODULE}._generate_tts_audio", AsyncMock(return_value=None)):
            await handler(_service_call(message="hello"))

        session.send_audio.assert_not_awaited()


class TestRecordingCleanup:
    """The daily recording retention job."""

    async def _cleanup(self, mock_hass, entry, mock_api):
        _, _, _, track = await _run_setup(mock_hass, entry, mock_api, [_make_coordinator()])
        return _tracked_callback(track, timedelta(hours=24))

    async def test_deletes_only_expired_recordings(self, mock_hass, entry, mock_api, tmp_path):
        cleanup = await self._cleanup(mock_hass, entry, mock_api)
        recordings = tmp_path / "media" / RECORDINGS_DIR
        recordings.mkdir(parents=True)
        old = recordings / "old.mp4"
        fresh = recordings / "fresh.mp4"
        old.write_bytes(b"x")
        fresh.write_bytes(b"x")
        expired = time.time() - 11 * DAY  # default retention is 10 days
        os.utime(old, (expired, expired))
        subdir = recordings / "keep-dir"
        subdir.mkdir()

        await cleanup()

        assert not old.exists()
        assert fresh.exists()
        assert subdir.exists()  # directories are never touched

    async def test_honors_retention_option(self, mock_hass, entry, mock_api, tmp_path):
        entry.options = {CONF_RECORDING_RETENTION: 1}
        cleanup = await self._cleanup(mock_hass, entry, mock_api)
        recordings = tmp_path / "media" / RECORDINGS_DIR
        recordings.mkdir(parents=True)
        stale = recordings / "two-days.mp4"
        stale.write_bytes(b"x")
        two_days = time.time() - 2 * DAY
        os.utime(stale, (two_days, two_days))

        await cleanup()

        assert not stale.exists()

    async def test_missing_recordings_dir_is_noop(self, mock_hass, entry, mock_api, tmp_path):
        cleanup = await self._cleanup(mock_hass, entry, mock_api)

        await cleanup()  # must not raise

        assert not (tmp_path / "media" / RECORDINGS_DIR).exists()


class TestFcmWatchdog:
    """The periodic FCM listener revival job."""

    async def test_revives_all_listeners(self, mock_hass, entry, mock_api):
        coordinators = [_make_coordinator(), _make_coordinator()]
        mock_api.get_pairings.return_value = [MagicMock(), MagicMock()]

        _, _, _, track = await _run_setup(mock_hass, entry, mock_api, coordinators)
        watchdog = _tracked_callback(track, timedelta(seconds=FCM_WATCHDOG_INTERVAL))
        await watchdog()

        for coordinator in coordinators:
            coordinator.ensure_notifications_running.assert_awaited_once()

    async def test_one_failing_listener_does_not_break_others(self, mock_hass, entry, mock_api):
        coordinators = [_make_coordinator(), _make_coordinator()]
        coordinators[0].ensure_notifications_running.side_effect = RuntimeError("receiver died")
        mock_api.get_pairings.return_value = [MagicMock(), MagicMock()]

        _, _, _, track = await _run_setup(mock_hass, entry, mock_api, coordinators)
        watchdog = _tracked_callback(track, timedelta(seconds=FCM_WATCHDOG_INTERVAL))
        await watchdog()  # must not raise

        coordinators[1].ensure_notifications_running.assert_awaited_once()


class TestShutdownListener:
    """The EVENT_HOMEASSISTANT_STOP hook."""

    async def test_ha_stop_stops_notifications(self, mock_hass, entry, mock_api):
        coordinator = _make_coordinator()
        await _run_setup(mock_hass, entry, mock_api, [coordinator])

        stop_handler = mock_hass.bus.async_listen_once.call_args.args[1]
        await stop_handler(MagicMock())

        coordinator.stop_notifications.assert_awaited_once()


class TestOptionsUpdated:
    """_async_options_updated hot-reload vs full reload."""

    async def test_auto_response_file_hot_reloads(self, mock_hass, entry):
        coordinator = _make_coordinator()
        mock_hass.data = {DOMAIN: {entry.entry_id: [coordinator]}}
        entry.options = {"auto_response_file": "/media/greeting.mp3"}

        await _async_options_updated(mock_hass, entry)

        assert coordinator._auto_response_file == "/media/greeting.mp3"
        mock_hass.config_entries.async_reload.assert_not_awaited()

    async def test_scan_interval_change_triggers_reload(self, mock_hass, entry):
        coordinator = _make_coordinator()  # currently at DEFAULT_SCAN_INTERVAL minutes
        mock_hass.data = {DOMAIN: {entry.entry_id: [coordinator]}}
        entry.options = {CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL + 5}

        await _async_options_updated(mock_hass, entry)

        mock_hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)

    async def test_no_update_interval_never_reloads(self, mock_hass, entry):
        coordinator = _make_coordinator()
        coordinator.update_interval = None
        mock_hass.data = {DOMAIN: {entry.entry_id: [coordinator]}}
        entry.options = {CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL + 5}

        await _async_options_updated(mock_hass, entry)

        mock_hass.config_entries.async_reload.assert_not_awaited()


class TestGenerateTtsAudio:
    """_generate_tts_audio: gTTS first, HA TTS service as fallback."""

    async def test_gtts_generates_a_file(self, mock_hass):
        tts_instance = MagicMock()
        tts_instance.save = MagicMock(side_effect=lambda name: Path(name).write_bytes(b"mp3"))
        gtts_module = MagicMock()
        gtts_module.gTTS = MagicMock(return_value=tts_instance)

        with patch.dict(sys.modules, {"gtts": gtts_module}):
            result = await _generate_tts_audio(mock_hass, "hola", "es")

        gtts_module.gTTS.assert_called_once_with(text="hola", lang="es")
        assert result is not None
        assert result.endswith(".mp3")
        tts_instance.save.assert_called_once_with(result)
        # unlink raises FileNotFoundError if the file was not actually created
        os.unlink(result)
        mock_hass.services.async_call.assert_not_awaited()

    async def test_missing_gtts_falls_back_to_ha_service(self, mock_hass, tmp_path):
        older = tmp_path / "older.mp3"
        newer = tmp_path / "newer.mp3"
        older.write_bytes(b"1")
        newer.write_bytes(b"2")
        past = time.time() - 100
        os.utime(older, (past, past))

        with (
            # None in sys.modules makes `from gtts import gTTS` raise ImportError
            patch.dict(sys.modules, {"gtts": None}),
            patch("glob.glob", return_value=[str(older), str(newer)]),
        ):
            result = await _generate_tts_audio(mock_hass, "hello", "en")

        assert result == str(newer)  # most recent TTS output wins
        call = mock_hass.services.async_call.call_args
        assert call.args[0] == "tts"
        assert call.args[1] == "google_translate_say"
        assert call.kwargs["blocking"] is True

    async def test_gtts_error_falls_back_then_none_without_files(self, mock_hass):
        gtts_module = MagicMock()
        gtts_module.gTTS = MagicMock(side_effect=ValueError("bad language"))

        with (
            patch.dict(sys.modules, {"gtts": gtts_module}),
            patch("glob.glob", return_value=[]),
        ):
            result = await _generate_tts_audio(mock_hass, "hello", "xx")

        assert result is None
        mock_hass.services.async_call.assert_awaited_once()

    async def test_returns_none_when_everything_fails(self, mock_hass):
        mock_hass.services.async_call.side_effect = RuntimeError("tts unavailable")

        with patch.dict(sys.modules, {"gtts": None}):
            result = await _generate_tts_audio(mock_hass, "hello", "en")

        assert result is None


class TestUnloadEntry:
    """async_unload_entry teardown."""

    async def test_unload_stops_everything_and_clears_data(self, mock_hass, entry):
        coordinators = [_make_coordinator(), _make_coordinator()]
        mock_hass.data = {DOMAIN: {entry.entry_id: coordinators}}

        assert await async_unload_entry(mock_hass, entry) is True

        for coordinator in coordinators:
            coordinator.stop_notifications.assert_awaited_once()
            coordinator.api.close.assert_awaited_once()
        mock_hass.config_entries.async_unload_platforms.assert_awaited_once_with(entry, PLATFORMS)
        assert entry.entry_id not in mock_hass.data[DOMAIN]

    async def test_failed_platform_unload_keeps_data(self, mock_hass, entry):
        coordinator = _make_coordinator()
        mock_hass.data = {DOMAIN: {entry.entry_id: [coordinator]}}
        mock_hass.config_entries.async_unload_platforms.return_value = False

        assert await async_unload_entry(mock_hass, entry) is False

        # coordinators are still shut down, but the entry stays registered
        coordinator.stop_notifications.assert_awaited_once()
        assert mock_hass.data[DOMAIN][entry.entry_id] == [coordinator]

    async def test_unload_without_coordinators_succeeds(self, mock_hass, entry):
        mock_hass.data = {DOMAIN: {}}

        assert await async_unload_entry(mock_hass, entry) is True
