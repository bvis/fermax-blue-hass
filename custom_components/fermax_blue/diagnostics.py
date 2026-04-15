"""Diagnostics support for Fermax Blue."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {
    "password",
    "username",
    "access_token",
    "fcm_token",
    "token",
    "fermax_auth_basic",
    "firebase_api_key",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinators = hass.data[DOMAIN].get(config_entry.entry_id, [])

    devices = []
    for coordinator in coordinators:
        listener = coordinator.notification_listener
        device: dict[str, Any] = {
            "device_id": coordinator.pairing.device_id,
            "tag": coordinator.pairing.tag,
            "coordinator_data": coordinator.data,
            "notification_listener": ("running" if listener and listener.is_started else "stopped"),
            "fcm_token": listener.fcm_token if listener else None,
            "stream_active": (
                coordinator.stream_session is not None and coordinator.stream_session.is_active
            )
            if coordinator.stream_session
            else False,
        }
        if coordinator.device_info:
            device["device_info"] = {
                "connection_state": coordinator.device_info.connection_state,
                "status": coordinator.device_info.status,
                "family": coordinator.device_info.family,
                "device_type": coordinator.device_info.device_type,
                "subtype": coordinator.device_info.subtype,
                "wireless_signal": coordinator.device_info.wireless_signal,
                "photocaller": coordinator.device_info.photocaller,
            }
        devices.append(device)

    return async_redact_data(
        {
            "config_entry": {
                "data": dict(config_entry.data),
                "options": dict(config_entry.options),
            },
            "devices": devices,
        },
        TO_REDACT,
    )
