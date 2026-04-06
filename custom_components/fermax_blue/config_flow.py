"""Config flow for Fermax Blue integration."""

from __future__ import annotations

import logging
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
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

CONF_AUTO_RESPONSE = "auto_response"
CONF_AUTO_RESPONSE_FILE = "auto_response_file"

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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

    async def _async_validate_and_create(
        self, data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Validate credentials and create the config entry."""
        errors: dict[str, str] = {}
        client = get_async_client(self.hass)
        api = FermaxBlueApi(
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
            client=client,
            auth_url=data[CONF_FERMAX_AUTH_URL],
            base_url=data[CONF_FERMAX_BASE_URL],
            auth_basic=data[CONF_FERMAX_AUTH_BASIC],
        )

        pairings = []
        try:
            await api.authenticate()
            pairings = await api.get_pairings()
        except FermaxAuthError:
            errors["base"] = "invalid_auth"
        except Exception:
            _LOGGER.exception("Unexpected error during authentication")
            errors["base"] = "cannot_connect"

        if not errors:
            if not pairings:
                errors["base"] = "no_devices"
            else:
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

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> FermaxBlueOptionsFlow:
        """Return the options flow."""
        return FermaxBlueOptionsFlow()


class FermaxBlueOptionsFlow(OptionsFlow):
    """Handle options for Fermax Blue."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                    vol.Optional(
                        CONF_RECORDING_RETENTION,
                        default=self.config_entry.options.get(
                            CONF_RECORDING_RETENTION, DEFAULT_RECORDING_RETENTION
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=1, max=90),
                    ),
                    vol.Optional(
                        CONF_AUTO_RESPONSE,
                        default=self.config_entry.options.get(
                            CONF_AUTO_RESPONSE, False
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_AUTO_RESPONSE_FILE,
                        default=self.config_entry.options.get(
                            CONF_AUTO_RESPONSE_FILE, "/config/media/mi_mensaje.wav"
                        ),
                    ): str,
                }
            ),
        )
