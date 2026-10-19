"""Config flow for Fermax Blue integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import FermaxAuthError, FermaxBlueApi, Pairing
from .const import (
    CONF_FERMAX_AUTH_BASIC,
    CONF_FERMAX_AUTH_URL,
    CONF_FERMAX_BASE_URL,
    CONF_FIREBASE_API_KEY,
    CONF_FIREBASE_APP_ID,
    CONF_FIREBASE_PACKAGE_NAME,
    CONF_FIREBASE_PROJECT_ID,
    CONF_FIREBASE_SENDER_ID,
    CONF_FORCE_STREAMING_DEPS,
    CONF_RECORDING_RETENTION,
    CONF_SCAN_INTERVAL,
    DEFAULT_RECORDING_RETENTION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .media_source import is_allowed_media_path

CONF_AUTO_RESPONSE_FILE = "auto_response_file"

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR})


def _https_url(value: str) -> str:
    """Validate that a URL uses HTTPS scheme."""
    url: str = vol.Url()(value)
    if not url.startswith("https://"):
        raise vol.Invalid("URL must use HTTPS")
    return url


STEP_CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_FERMAX_AUTH_URL): str,
        vol.Required(CONF_FERMAX_BASE_URL): str,
        vol.Required(CONF_FERMAX_AUTH_BASIC): str,
        vol.Required(CONF_FIREBASE_API_KEY): str,
        vol.Required(CONF_FIREBASE_SENDER_ID): str,
        vol.Required(CONF_FIREBASE_APP_ID): str,
        vol.Required(CONF_FIREBASE_PROJECT_ID): str,
        vol.Required(CONF_FIREBASE_PACKAGE_NAME): str,
    }
)


class FermaxBlueConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fermax Blue."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step — user credentials."""
        if user_input is not None:
            self._user_data = user_input
            return await self.async_step_credentials()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the API/Firebase credentials step."""
        if user_input is not None:
            data = {**self._user_data, **user_input}
            return await self._async_validate_and_create(data)

        return self.async_show_form(
            step_id="credentials",
            data_schema=STEP_CREDENTIALS_SCHEMA,
        )

    async def _async_try_login(self, data: dict[str, Any]) -> tuple[dict[str, str], list[Pairing]]:
        """Log in with data (URLs normalised in place); return form errors and pairings."""
        errors: dict[str, str] = {}
        for field in (CONF_FERMAX_AUTH_URL, CONF_FERMAX_BASE_URL):
            try:
                data[field] = _https_url(data[field])
            except vol.Invalid:
                errors[field] = "invalid_url"
        if errors:
            return errors, []

        api = FermaxBlueApi(
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
            client=get_async_client(self.hass),
            auth_url=data[CONF_FERMAX_AUTH_URL],
            base_url=data[CONF_FERMAX_BASE_URL],
            auth_basic=data[CONF_FERMAX_AUTH_BASIC],
        )
        pairings: list[Pairing] = []
        try:
            await api.authenticate()
            pairings = await api.get_pairings()
        except FermaxAuthError:
            errors["base"] = "invalid_auth"
        except Exception:
            _LOGGER.exception("Unexpected error during authentication")
            errors["base"] = "cannot_connect"
        if not errors and not pairings:
            errors["base"] = "no_devices"
        return errors, pairings

    async def _async_validate_and_create(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Validate credentials and create the config entry."""
        errors, pairings = await self._async_try_login(data)
        if not errors:
            await self.async_set_unique_id(data[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Fermax Blue ({pairings[0].tag})",
                data=data,
            )

        # On error, go back to credentials step to let user fix
        return self.async_show_form(
            step_id="credentials",
            data_schema=STEP_CREDENTIALS_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],  # noqa: V107
    ) -> ConfigFlowResult:
        """Fermax rejected the stored password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the account's new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, _ = await self._async_try_login({**entry.data, **user_input})
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the API and Firebase credentials of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            errors, _ = await self._async_try_login(data)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={key: data[key] for key in user_input}
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_CREDENTIALS_SCHEMA, {**entry.data, **(user_input or {})}
            ),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> FermaxBlueOptionsFlow:
        """Return the options flow."""
        return FermaxBlueOptionsFlow()


class FermaxBlueOptionsFlow(OptionsFlow):
    """Handle options for Fermax Blue."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            audio = user_input.get(CONF_AUTO_RESPONSE_FILE, "")
            if audio and not await asyncio.to_thread(is_allowed_media_path, self.hass, audio):
                errors[CONF_AUTO_RESPONSE_FILE] = "invalid_path"
            else:
                return self.async_create_entry(data=user_input)

        current = {**self.config_entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                    vol.Optional(
                        CONF_RECORDING_RETENTION,
                        default=current.get(CONF_RECORDING_RETENTION, DEFAULT_RECORDING_RETENTION),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=1, max=90),
                    ),
                    vol.Optional(
                        CONF_AUTO_RESPONSE_FILE,
                        default=current.get(CONF_AUTO_RESPONSE_FILE, ""),
                    ): str,
                    vol.Optional(
                        CONF_FORCE_STREAMING_DEPS,
                        default=current.get(CONF_FORCE_STREAMING_DEPS, False),
                    ): bool,
                }
            ),
            errors=errors,
        )
