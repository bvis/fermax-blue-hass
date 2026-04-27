"""The Fermax Blue integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
from datetime import timedelta
from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.httpx_client import create_async_httpx_client

from .api import FermaxBlueApi
from .const import (
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
    DEFAULT_RECORDING_RETENTION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    RECORDINGS_DIR,
)
from .coordinator import FermaxBlueCoordinator

_LOGGER = logging.getLogger(__name__)

type FermaxBlueConfigEntry = ConfigEntry[list[FermaxBlueCoordinator]]


_V2_REQUIRED_KEYS = {
    CONF_FERMAX_AUTH_URL,
    CONF_FERMAX_BASE_URL,
    CONF_FERMAX_AUTH_BASIC,
    CONF_FIREBASE_API_KEY,
    CONF_FIREBASE_SENDER_ID,
    CONF_FIREBASE_APP_ID,
    CONF_FIREBASE_PROJECT_ID,
    CONF_FIREBASE_PACKAGE_NAME,
}


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate a config entry to the current version.

    Version history:
      1 — Original format: only username + password (credentials were hardcoded).
      2 — API URL, auth basic, and Firebase credentials are now supplied by the user.
    """
    _LOGGER.debug("Migrating Fermax Blue config entry from version %s", config_entry.version)

    if config_entry.version < 2:
        # If the entry already has all v2 fields (added manually before the
        # VERSION bump existed), just promote it to v2.
        if _V2_REQUIRED_KEYS.issubset(config_entry.data.keys()):
            hass.config_entries.async_update_entry(config_entry, version=2)
            _LOGGER.info("Fermax Blue config entry migrated to version 2 (fields already present)")
            return True

        _LOGGER.error(
            "Fermax Blue config entry (version %s) cannot be automatically migrated to "
            "version 2. The integration now requires API and Firebase credentials that "
            "must be supplied manually (run extract_credentials.py to obtain them). "
            "Please delete this integration entry and re-add it.",
            config_entry.version,
        )
        return False

    return True


async def async_setup_entry(hass: HomeAssistant, entry: FermaxBlueConfigEntry) -> bool:
    """Set up Fermax Blue from a config entry."""
    client = create_async_httpx_client(hass)
    api = FermaxBlueApi(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        client=client,
        auth_url=entry.data[CONF_FERMAX_AUTH_URL],
        base_url=entry.data[CONF_FERMAX_BASE_URL],
        auth_basic=entry.data[CONF_FERMAX_AUTH_BASIC],
    )

    try:
        await api.authenticate()
        pairings = await api.get_pairings()
    except Exception as err:
        await api.close()
        raise ConfigEntryNotReady(f"Failed to connect to Fermax API: {err}") from err

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    auto_response_file = entry.options.get("auto_response_file", "")
    firebase_config: dict[str, str | int] = {
        "firebase_api_key": entry.data[CONF_FIREBASE_API_KEY],
        "firebase_sender_id": int(entry.data[CONF_FIREBASE_SENDER_ID]),
        "firebase_app_id": entry.data[CONF_FIREBASE_APP_ID],
        "firebase_project_id": entry.data[CONF_FIREBASE_PROJECT_ID],
        "firebase_package_name": entry.data[CONF_FIREBASE_PACKAGE_NAME],
    }

    coordinators: list[FermaxBlueCoordinator] = []

    for pairing in pairings:
        coordinator = FermaxBlueCoordinator(
            hass, api, pairing, scan_interval, auto_response_file, firebase_config
        )
        await coordinator.async_config_entry_first_refresh()

        storage_path = Path(hass.config.config_dir) / ".storage" / DOMAIN
        await asyncio.to_thread(storage_path.mkdir, parents=True, exist_ok=True)
        await coordinator.setup_notifications(storage_path)

        coordinators.append(coordinator)

    entry.runtime_data = coordinators
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinators

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register send_audio service
    async def _handle_send_audio(call: ServiceCall) -> None:
        """Handle the send_audio service call."""
        audio_file = call.data.get("audio_file")
        message = call.data.get("message")
        language = call.data.get("language", "es")

        if not audio_file and not message:
            _LOGGER.error("send_audio: either audio_file or message is required")
            return

        # Validate audio_file path against HA media directories
        if audio_file:

            def _validate_path() -> bool:
                allowed_dirs = [
                    Path(str(d)).resolve()
                    for d in [
                        *hass.config.media_dirs.values(),
                        Path(hass.config.config_dir) / "media",
                    ]
                ]
                try:
                    resolved = Path(str(audio_file)).resolve()
                    return any(resolved == d or d in resolved.parents for d in allowed_dirs)
                except (OSError, ValueError):
                    return False

            if not await asyncio.to_thread(_validate_path):
                _LOGGER.error(
                    "send_audio: path %s is outside allowed media directories or invalid",
                    audio_file,
                )
                return

        # Find the coordinator with an active stream
        active_coordinator = None
        for coord in coordinators:
            if coord.stream_session and coord.stream_session.is_active:
                active_coordinator = coord
                break

        if not active_coordinator or not active_coordinator.stream_session:
            _LOGGER.error("send_audio: no active video stream")
            return

        # If message provided, generate TTS audio file
        if message and not audio_file:
            audio_file = await _generate_tts_audio(hass, message, language)
            if not audio_file:
                _LOGGER.error("send_audio: failed to generate TTS audio")
                return

        tts_generated = bool(message and not call.data.get("audio_file"))
        if audio_file:
            await active_coordinator.stream_session.send_audio(audio_file)
            # Clean up TTS temp file after use
            if tts_generated:
                with contextlib.suppress(OSError):
                    await asyncio.to_thread(Path(audio_file).unlink)

    if not hass.services.has_service(DOMAIN, "send_audio"):
        hass.services.async_register(
            DOMAIN,
            "send_audio",
            _handle_send_audio,
            schema=vol.Schema(
                {
                    vol.Optional("entity_id"): str,
                    vol.Optional("audio_file"): str,
                    vol.Optional("message"): str,
                    vol.Optional("language", default="es"): str,
                }
            ),
        )

    # Schedule recording cleanup
    async def _cleanup_old_recordings(_now: object = None) -> None:
        """Delete recordings older than retention period."""
        import time

        media_root = hass.config.media_dirs.get("local", "/media")
        recordings_path = Path(media_root) / RECORDINGS_DIR
        retention = entry.options.get(CONF_RECORDING_RETENTION, DEFAULT_RECORDING_RETENTION)
        cutoff = time.time() - (retention * 86400)

        def _do_cleanup() -> list[str]:
            """Perform blocking filesystem cleanup; return list of deleted filenames."""
            if not recordings_path.exists():
                return []
            deleted: list[str] = []
            for f in recordings_path.iterdir():
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted.append(f.name)
            return deleted

        deleted_files = await asyncio.to_thread(_do_cleanup)
        for name in deleted_files:
            _LOGGER.debug("Deleted old recording: %s", name)

    # Run cleanup once at startup (non-blocking) and daily
    hass.async_create_task(_cleanup_old_recordings())
    from datetime import timedelta as _td

    from homeassistant.helpers.event import async_track_time_interval

    entry.async_on_unload(async_track_time_interval(hass, _cleanup_old_recordings, _td(hours=24)))

    async def _async_shutdown(event: Event) -> None:
        """Clean up on shutdown."""
        for coordinator in coordinators:
            await coordinator.stop_notifications()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_shutdown))
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(hass: HomeAssistant, entry: FermaxBlueConfigEntry) -> None:
    """Handle options update — apply hot-reloadable options without full reload."""
    coordinators = hass.data[DOMAIN].get(entry.entry_id, [])

    new_scan = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    auto_response_file = entry.options.get("auto_response_file", "")

    needs_reload = False
    for coordinator in coordinators:
        coordinator._auto_response_file = auto_response_file
        # Scan interval change requires reload for update_interval to take effect
        old_scan = coordinator.update_interval
        if old_scan and old_scan != timedelta(minutes=new_scan):
            needs_reload = True

    if needs_reload:
        await hass.config_entries.async_reload(entry.entry_id)
    else:
        _LOGGER.info("Options updated (hot reload, no restart needed)")


async def _generate_tts_audio(hass: HomeAssistant, message: str, language: str) -> str | None:
    """Generate a WAV file from text using Google Translate TTS."""
    try:
        from gtts import gTTS

        def _generate_sync() -> str:
            tts = gTTS(text=message, lang=language)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tts.save(f.name)
                return f.name

        path = await asyncio.to_thread(_generate_sync)
        _LOGGER.info("TTS audio generated: %s", path)
        return path
    except ImportError:
        _LOGGER.debug("gtts not available, trying HA tts service")
    except Exception:
        _LOGGER.exception("Failed to generate TTS audio")

    # Fallback: try using HA's built-in TTS
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tts_file:
            tts_file.close()

        await hass.services.async_call(
            "tts",
            "google_translate_say",
            {
                "message": message,
                "language": language,
                "entity_id": "media_player.none",  # Dummy, we just want the file
            },
            blocking=True,
        )
        # HA TTS generates files in /config/tts/
        import glob

        def _find_latest_tts() -> str | None:
            files = sorted(
                glob.glob("/config/tts/*.mp3"),
                key=lambda f: Path(f).stat().st_mtime,
                reverse=True,
            )
            return files[0] if files else None

        result = await asyncio.to_thread(_find_latest_tts)
        if result:
            return result
    except Exception:
        _LOGGER.debug("HA TTS fallback failed", exc_info=True)

    return None


async def async_unload_entry(hass: HomeAssistant, entry: FermaxBlueConfigEntry) -> bool:
    """Unload a config entry."""
    coordinators = hass.data[DOMAIN].get(entry.entry_id, [])
    for coordinator in coordinators:
        await coordinator.stop_notifications()
        await coordinator.api.close()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
