"""The Fermax Blue integration."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.typing import ConfigType
from homeassistant.requirements import RequirementsNotFound, async_process_requirements

from .api import FermaxAuthError, FermaxBlueApi
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
    FCM_WATCHDOG_INTERVAL,
    PLATFORMS,
    RECORDINGS_DIR,
    STREAMING_REQUIREMENTS,
    WEBRTC_TOKENS,
)
from .coordinator import FermaxBlueConfigEntry, FermaxBlueCoordinator
from .media_source import is_allowed_media_path
from .streaming import streaming_deps_available
from .webrtc_bridge import FermaxWebRtcView

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_SEND_AUDIO = "send_audio"
SEND_AUDIO_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("audio_file"): str,
        vol.Optional("message"): str,
        vol.Optional("language", default="es"): str,
    }
)
STREAMING_ISSUE = "streaming_unavailable"
# Imported inside coroutines by the overlay, TTS and live-video paths: loading
# them once at setup, in a worker thread, keeps that disk I/O off the event loop
PRELOAD_MODULES = ("PIL.Image", "PIL.ImageDraw", "PIL.ImageFont", "numpy", "gtts")
STREAMING_PRELOAD_MODULES = (
    "av",
    "aiortc",
    "aiortc.codecs.h264",
    "aiortc.contrib.media",
    "aiortc.mediastreams",
    "pymediasoup",
    "pymediasoup.handlers.aiortc_handler",
)


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


async def _async_install_streaming_deps(hass: HomeAssistant) -> None:
    """Install the live-video deps, or carry on without them."""
    try:
        await async_process_requirements(
            hass, DOMAIN, list(STREAMING_REQUIREMENTS), is_built_in=False
        )
    except RequirementsNotFound:
        _LOGGER.warning(
            "Live video is unavailable: %s cannot be installed alongside the av version "
            "this Home Assistant ships. Everything else works; live video comes back by "
            "itself once a compatible aiortc release is out (see the README)",
            ", ".join(STREAMING_REQUIREMENTS),
        )
    importlib.invalidate_caches()
    streaming_deps_available.cache_clear()
    if streaming_deps_available():
        ir.async_delete_issue(hass, DOMAIN, STREAMING_ISSUE)
    else:
        ir.async_create_issue(
            hass,
            DOMAIN,
            STREAMING_ISSUE,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=STREAMING_ISSUE,
            learn_more_url="https://github.com/bvis/fermax-blue-hass#known-limitations",
        )


def _import_modules(names: tuple[str, ...]) -> None:
    """Import what is installed of names (runs in a worker thread)."""
    for name in names:
        with contextlib.suppress(ImportError):
            importlib.import_module(name)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions (once, whatever entries load)."""
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_AUDIO, _async_send_audio, schema=SEND_AUDIO_SCHEMA
    )
    return True


def _service_coordinator(
    hass: HomeAssistant, entity_id: str | None
) -> FermaxBlueCoordinator | None:
    """The intercom an action targets: the entity's, or else the one streaming."""
    coordinators = [
        coordinator
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        for coordinator in entry.runtime_data
    ]
    if entity_id is None:
        return next((c for c in coordinators if c.has_active_stream), None)
    entity = er.async_get(hass).async_get(entity_id)
    device = (
        dr.async_get(hass).async_get(entity.device_id)
        if entity and entity.platform == DOMAIN and entity.device_id
        else None
    )
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_entity",
            translation_placeholders={"entity_id": entity_id},
        )
    return next(
        (c for c in coordinators if (DOMAIN, c.pairing.device_id) in device.identifiers), None
    )


async def _async_send_audio(call: ServiceCall) -> None:
    """Play an audio file or a spoken message through the intercom."""
    hass = call.hass
    audio_file = call.data.get("audio_file")
    message = call.data.get("message")
    language = call.data.get("language", "es")

    if not audio_file and not message:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="send_audio_no_input"
        )

    if audio_file and not await asyncio.to_thread(is_allowed_media_path, hass, str(audio_file)):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="send_audio_invalid_path",
            translation_placeholders={"path": str(audio_file)},
        )

    coordinator = _service_coordinator(hass, call.data.get(ATTR_ENTITY_ID))
    session = coordinator.stream_session if coordinator else None
    if not coordinator or not coordinator.has_active_stream or not session:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_active_stream")

    tts_generated = False
    if not audio_file:
        audio_file = await _generate_tts_audio(str(message), language)
        if not audio_file:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="send_audio_tts_failed"
            )
        tts_generated = True

    await session.send_audio(audio_file)
    if tts_generated:
        with contextlib.suppress(OSError):
            await asyncio.to_thread(Path(audio_file).unlink)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: FermaxBlueConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Let the user delete a device the account no longer has."""
    return not any(
        (DOMAIN, coordinator.pairing.device_id) in device_entry.identifiers
        for coordinator in entry.runtime_data
    )


async def async_setup_entry(hass: HomeAssistant, entry: FermaxBlueConfigEntry) -> bool:
    """Set up Fermax Blue from a config entry."""
    api = FermaxBlueApi(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        client=get_async_client(hass),
        auth_url=entry.data[CONF_FERMAX_AUTH_URL],
        base_url=entry.data[CONF_FERMAX_BASE_URL],
        auth_basic=entry.data[CONF_FERMAX_AUTH_BASIC],
    )

    try:
        await api.authenticate()
        pairings = await api.get_pairings()
    except FermaxAuthError as err:
        await api.close()
        raise ConfigEntryAuthFailed(f"Fermax rejected the credentials: {err}") from err
    except Exception as err:
        await api.close()
        raise ConfigEntryNotReady(f"Failed to connect to Fermax API: {err}") from err

    await _async_install_streaming_deps(hass)
    await asyncio.to_thread(
        _import_modules,
        PRELOAD_MODULES + (STREAMING_PRELOAD_MODULES if streaming_deps_available() else ()),
    )

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
            hass,
            api,
            pairing,
            scan_interval,
            auto_response_file,
            firebase_config,
            config_entry=entry,
        )
        await coordinator.async_config_entry_first_refresh()

        storage_path = Path(hass.config.config_dir) / ".storage" / DOMAIN
        await asyncio.to_thread(storage_path.mkdir, parents=True, exist_ok=True)
        await coordinator.setup_notifications(storage_path)

        coordinators.append(coordinator)

    entry.runtime_data = coordinators

    # go2rtc signaling endpoint: one view for all entries, one token per intercom
    domain_data = hass.data.setdefault(DOMAIN, {})
    tokens: dict[str, FermaxBlueCoordinator] | None = domain_data.get(WEBRTC_TOKENS)
    if tokens is None:
        tokens = domain_data[WEBRTC_TOKENS] = {}
        hass.http.register_view(FermaxWebRtcView(tokens))
    for coordinator in coordinators:
        tokens[coordinator.webrtc_token] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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
    entry.async_create_background_task(
        hass, _cleanup_old_recordings(), f"{DOMAIN} recording cleanup", eager_start=False
    )
    entry.async_on_unload(
        async_track_time_interval(hass, _cleanup_old_recordings, timedelta(hours=24))
    )

    async def _check_new_pairings(_now: datetime | None = None) -> None:
        """Reload when the account gained an intercom, so it gets its entities."""
        try:
            current = {pairing.device_id for pairing in await api.get_pairings()}
        except Exception:
            _LOGGER.debug("Could not check the account's pairings", exc_info=True)
            return
        if current - {c.pairing.device_id for c in coordinators}:
            _LOGGER.info("New Fermax device on the account, reloading the integration")
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(async_track_time_interval(hass, _check_new_pairings, timedelta(hours=24)))

    async def _fcm_watchdog(_now: datetime | None = None) -> None:
        """Revive any FCM listener whose receiver has died."""
        await asyncio.gather(
            *(c.ensure_notifications_running() for c in coordinators),
            return_exceptions=True,
        )

    entry.async_on_unload(
        async_track_time_interval(hass, _fcm_watchdog, timedelta(seconds=FCM_WATCHDOG_INTERVAL))
    )

    async def _async_shutdown(event: Event) -> None:
        """Clean up on shutdown."""
        for coordinator in coordinators:
            await coordinator.stop_notifications()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_shutdown))
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(hass: HomeAssistant, entry: FermaxBlueConfigEntry) -> None:
    """Handle options update — apply hot-reloadable options without full reload."""
    coordinators = entry.runtime_data

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


async def _generate_tts_audio(message: str, language: str) -> str | None:
    """Generate an MP3 file from text using Google Translate TTS."""
    from gtts import gTTS

    def _generate_sync() -> str:
        tts = gTTS(text=message, lang=language)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tts.save(f.name)
            return f.name

    try:
        path = await asyncio.to_thread(_generate_sync)
    except Exception:
        _LOGGER.exception("Failed to generate TTS audio")
        return None
    _LOGGER.info("TTS audio generated: %s", path)
    return path


async def async_unload_entry(hass: HomeAssistant, entry: FermaxBlueConfigEntry) -> bool:
    """Unload a config entry; push and the API stay up if the platforms refuse."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    tokens = hass.data.get(DOMAIN, {}).get(WEBRTC_TOKENS, {})
    for coordinator in entry.runtime_data:
        tokens.pop(coordinator.webrtc_token, None)
        await coordinator.stop_stream()
        await coordinator.stop_notifications()
        await coordinator.api.close()
    return True
