"""The Fermax Blue integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback

from .api import FermaxBlueApi
from .const import DOMAIN, PLATFORMS
from .coordinator import FermaxBlueCoordinator

_LOGGER = logging.getLogger(__name__)

type FermaxBlueConfigEntry = ConfigEntry[list[FermaxBlueCoordinator]]


async def async_setup_entry(
    hass: HomeAssistant, entry: FermaxBlueConfigEntry
) -> bool:
    """Set up Fermax Blue from a config entry."""
    api = FermaxBlueApi(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    await api.authenticate()
    pairings = await api.get_pairings()

    coordinators: list[FermaxBlueCoordinator] = []

    for pairing in pairings:
        coordinator = FermaxBlueCoordinator(hass, api, pairing)
        await coordinator.async_config_entry_first_refresh()

        # Set up FCM notifications
        storage_path = Path(hass.config.config_dir) / ".storage" / DOMAIN
        storage_path.mkdir(parents=True, exist_ok=True)
        await coordinator.setup_notifications(storage_path)

        coordinators.append(coordinator)

    entry.runtime_data = coordinators
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinators

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    async def _async_shutdown(event):
        """Clean up on shutdown."""
        for coordinator in coordinators:
            await coordinator.stop_notifications()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_shutdown)
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: FermaxBlueConfigEntry
) -> bool:
    """Unload a config entry."""
    coordinators = hass.data[DOMAIN].get(entry.entry_id, [])
    for coordinator in coordinators:
        await coordinator.stop_notifications()
        await coordinator.api.close()

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
