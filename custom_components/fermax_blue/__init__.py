"""The Fermax Blue integration."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.helpers.httpx_client import create_async_httpx_client

from .api import FermaxBlueApi
from .const import (
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


async def async_setup_entry(hass: HomeAssistant, entry: FermaxBlueConfigEntry) -> bool:
    """Set up Fermax Blue from a config entry."""
    client = create_async_httpx_client(hass)
    api = FermaxBlueApi(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        client=client,
    )

    try:
        await api.authenticate()
        pairings = await api.get_pairings()
    except Exception:
        await api.close()
        raise

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    auto_response_enabled = entry.options.get("auto_response", False)
    auto_response_file = (
        entry.options.get("auto_response_file", "") if auto_response_enabled else ""
    )
    coordinators: list[FermaxBlueCoordinator] = []

    for pairing in pairings:
        coordinator = FermaxBlueCoordinator(
            hass, api, pairing, scan_interval, auto_response_file
        )
        await coordinator.async_config_entry_first_refresh()

        storage_path = Path(hass.config.config_dir) / ".storage" / DOMAIN
        storage_path.mkdir(parents=True, exist_ok=True)
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

        if audio_file:
            await active_coordinator.stream_session.send_audio(audio_file)

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

        recordings_path = Path(hass.config.config_dir) / "media" / RECORDINGS_DIR
        if not recordings_path.exists():
            return
        retention = entry.options.get(
            CONF_RECORDING_RETENTION, DEFAULT_RECORDING_RETENTION
        )
        cutoff = time.time() - (retention * 86400)
        for f in recordings_path.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                _LOGGER.debug("Deleted old recording: %s", f.name)

    # Run cleanup once at startup and daily
    await _cleanup_old_recordings()
    from datetime import timedelta as _td

    from homeassistant.helpers.event import async_track_time_interval

    entry.async_on_unload(
        async_track_time_interval(hass, _cleanup_old_recordings, _td(hours=24))
    )

    async def _async_shutdown(event: Event) -> None:
        """Clean up on shutdown."""
        for coordinator in coordinators:
            await coordinator.stop_notifications()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_shutdown)
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: FermaxBlueConfigEntry
) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _generate_tts_audio(
    hass: HomeAssistant, message: str, language: str
) -> str | None:
    """Generate a WAV file from text using Google Translate TTS."""
    try:
        from gtts import gTTS

        tts = gTTS(text=message, lang=language)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tts.save(f.name)
            _LOGGER.info("TTS audio generated: %s", f.name)
            return f.name
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

        tts_files = sorted(
            glob.glob("/config/tts/*.mp3"),
            key=lambda f: Path(f).stat().st_mtime,
            reverse=True,
        )
        if tts_files:
            return tts_files[0]
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
