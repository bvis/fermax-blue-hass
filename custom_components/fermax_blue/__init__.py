"""The Fermax Blue integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.httpx_client import create_async_httpx_client

from .api import FermaxBlueApi
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, PLATFORMS
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
    coordinators: list[FermaxBlueCoordinator] = []

    for pairing in pairings:
        coordinator = FermaxBlueCoordinator(hass, api, pairing, scan_interval)
        await coordinator.async_config_entry_first_refresh()

        storage_path = Path(hass.config.config_dir) / ".storage" / DOMAIN
        storage_path.mkdir(parents=True, exist_ok=True)
        await coordinator.setup_notifications(storage_path)

        coordinators.append(coordinator)

    entry.runtime_data = coordinators
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinators

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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
