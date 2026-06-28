"""Config flow for Xplora® Watch Version 2."""

from __future__ import annotations

import logging
from collections import OrderedDict
from types import MappingProxyType
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries, core
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow, OptionsFlowWithConfigEntry
from homeassistant.const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_COUNTRY_CODE,
    CONF_EMAIL,
    CONF_LANGUAGE,
    CONF_PASSWORD,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    STATE_OFF,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util.read_only_dict import ReadOnlyDict

from .config import resolve_language
from .const import (
    CONF_AUTO_FETCH_HISTORY,
    CONF_AUTO_MARK_READ,
    CONF_HISTORY_RETENTION_DAYS,
    CONF_HOME_LATITUDE,
    CONF_HOME_LONGITUDE,
    CONF_HOME_RADIUS,
    CONF_HOME_SAFEZONE,
    CONF_MAPS,
    CONF_MESSAGE,
    CONF_OPENCAGE_APIKEY,
    CONF_PHONENUMBER,
    CONF_REFRESH_ON_CARD_RENDER,
    CONF_REMOVE_MESSAGE,
    CONF_SCAN_INTERVAL_FUNCTIONS,
    CONF_SIGNIN_TYP,
    CONF_TIMEZONE,
    CONF_USERLANG,
    CONF_WATCHES,
    DEFAULT_AUTO_FETCH_HISTORY,
    DEFAULT_HISTORY_RETENTION_DAYS,
    DEFAULT_LANGUAGE,
    DEFAULT_REFRESH_ON_CARD_RENDER,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_FUNCTIONS,
    DOMAIN,
    HISTORY_RETENTION_DAYS_MAX,
    HISTORY_RETENTION_DAYS_MIN,
    HOME,
    HOME_SAFEZONE,
    MANUFACTURER,
    MAPS,
    SCAN_INTERVAL_FUNCTIONS_OPTIONS,
    SCAN_INTERVAL_OPTIONS,
    SIGNIN,
    SUPPORTED_LANGUAGES,
    normalize_history_retention_days,
    normalize_scan_interval,
    normalize_scan_interval_functions,
)
from .const_schema import DATA_SCHEMA_EMAIL, DATA_SCHEMA_PHONE
from .demo import make_controller
from .helper import watch_user_label
from .pyxplora_api.exception_classes import Error, LoginError, PhoneOrEmailFail
from .pyxplora_api.pyxplora_api_async import PyXploraApi
from .pyxplora_api.status import UserContactType

_LOGGER = logging.getLogger(__name__)


@callback
async def sign_in(hass: core.HomeAssistant, data: dict[str, Any] | MappingProxyType[str, Any]) -> PyXploraApi:
    """Sign in to the Xplora® API."""
    controller: PyXploraApi = make_controller(
        countrycode=data.get(CONF_COUNTRY_CODE) or "",
        phoneNumber=data.get(CONF_PHONENUMBER) or "",
        password=data.get(CONF_PASSWORD, ""),
        userLang=data.get(CONF_USERLANG) or "",
        timeZone=data.get(CONF_TIMEZONE) or "",
        email=data.get(CONF_EMAIL, None),
        session=aiohttp_client.async_get_clientsession(hass),
    )
    await controller.init()
    return controller


async def validate_input(hass: core.HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """

    # No `init()` here: `checkEmailOrPhoneExist` runs under OPEN authorization (static API key,
    # no bearer token), so the old `account.init(signup=False)` only burned ~5 failed credential-
    # less login attempts against the rate-limit-sensitive auth endpoint for nothing.
    account = make_controller(
        session=aiohttp_client.async_create_clientsession(hass),
        email=data.get(CONF_EMAIL),
        phoneNumber=data.get(CONF_PHONENUMBER) or "",
    )
    if not await account.checkEmailOrPhoneExist(
        UserContactType.EMAIL if data.get(CONF_EMAIL) else UserContactType.PHONE,
        email=data.get(CONF_EMAIL) or "",
        countryCode=data.get(CONF_COUNTRY_CODE) or "",
        phoneNumber=data.get(CONF_PHONENUMBER) or "",
    ):
        raise PhoneOrEmailFail()

    try:
        await sign_in(hass=hass, data=data)
    except LoginError as err:
        raise LoginError(err.error_message) from err

    # Return info that you want to store in the config entry.
    return {"title": f"{MANUFACTURER}"}


def validate_options_input(user_input: dict[str, Any]) -> dict[str, str]:
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    errors = {}
    key: str = user_input[CONF_OPENCAGE_APIKEY]
    maps = user_input[CONF_MAPS]

    if maps == MAPS[1] and len(key) == 0:
        errors["base"] = "api_key_error"

    if not user_input[CONF_WATCHES]:
        errors["base"] = "no_watch"

    # Return info that you want to store in the config entry.
    return errors


class XploraConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Xplora® Watch Version 2."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return XploraOptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        return self.async_show_menu(step_id="user", menu_options=["user_email", "user_phone"])

    async def async_step_user_phone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            unique_id = f"{user_input[CONF_PHONENUMBER]}"

            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            info = None
            try:
                info = await validate_input(self.hass, user_input)
            except PhoneOrEmailFail as error:
                _LOGGER.exception(error)
                errors["base"] = "phone_email_invalid"
            except LoginError as error:
                _LOGGER.exception(error)
                errors["base"] = "pass_invalid"
            except Error as error:
                _LOGGER.exception(error)
                errors["base"] = "cannot_connect"

            if info:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(step_id="user_phone", data_schema=vol.Schema(DATA_SCHEMA_PHONE), errors=errors, last_step=True)

    async def async_step_user_email(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            unique_id = f"{user_input[CONF_EMAIL]}"

            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            info = None
            try:
                info = await validate_input(self.hass, user_input)
            except PhoneOrEmailFail as error:
                _LOGGER.exception(error)
                errors["base"] = "phone_email_invalid"
            except LoginError as error:
                _LOGGER.exception(error)
                errors["base"] = "pass_invalid"
            except Error as error:
                _LOGGER.exception(error)
                errors["base"] = "cannot_connect"

            if info:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(step_id="user_email", data_schema=vol.Schema(DATA_SCHEMA_EMAIL), errors=errors, last_step=True)


class XploraOptionsFlowHandler(OptionsFlowWithConfigEntry):
    """Handle a option flow."""

    def get_options(
        self,
        signin_typ: list[str],
        schema: OrderedDict,
        language: str,
        _options: MappingProxyType[str, Any],
        _home_zone: ReadOnlyDict[str, Any],
    ) -> vol.Schema:
        """Set SCHEMA return SCHEMA."""
        return vol.Schema(
            {
                vol.Required(CONF_SIGNIN_TYP, default=signin_typ[0]): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=signin,
                                label=signin,
                            )
                            for signin in signin_typ
                        ],
                        multiple=False,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                **schema,
                vol.Required(CONF_LANGUAGE, default=language): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=language_key,
                                label=language_value,
                            )
                            for language_dict in SUPPORTED_LANGUAGES
                            for language_key, language_value in language_dict.items()
                        ],
                        multiple=False,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_MAPS, default=_options.get(CONF_MAPS, MAPS[0])): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=value,
                                label=value,
                            )
                            for value in MAPS
                        ],
                        multiple=False,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_OPENCAGE_APIKEY, default=_options.get(CONF_OPENCAGE_APIKEY, "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=str(normalize_scan_interval(_options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=str(seconds), label=label)
                            for seconds, label in SCAN_INTERVAL_OPTIONS.get(language, SCAN_INTERVAL_OPTIONS[DEFAULT_LANGUAGE]).items()
                        ],
                        multiple=False,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL_FUNCTIONS,
                    default=str(
                        normalize_scan_interval_functions(_options.get(CONF_SCAN_INTERVAL_FUNCTIONS, DEFAULT_SCAN_INTERVAL_FUNCTIONS))
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=str(seconds), label=label)
                            for seconds, label in SCAN_INTERVAL_FUNCTIONS_OPTIONS.get(
                                language, SCAN_INTERVAL_FUNCTIONS_OPTIONS[DEFAULT_LANGUAGE]
                            ).items()
                        ],
                        multiple=False,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_HOME_SAFEZONE, default=_options.get(CONF_HOME_SAFEZONE, STATE_OFF)): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=value,
                                label=label,
                            )
                            for value, label in HOME_SAFEZONE.get(language, HOME_SAFEZONE[DEFAULT_LANGUAGE]).items()
                        ],
                        multiple=False,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_HOME_LATITUDE, default=_options.get(CONF_HOME_LATITUDE, _home_zone[ATTR_LATITUDE])): cv.latitude,
                vol.Required(CONF_HOME_LONGITUDE, default=_options.get(CONF_HOME_LONGITUDE, _home_zone[ATTR_LONGITUDE])): cv.longitude,
                vol.Required(CONF_HOME_RADIUS, default=_options.get(CONF_HOME_RADIUS, _home_zone[CONF_RADIUS])): cv.positive_int,
                vol.Required(CONF_MESSAGE, default=_options.get(CONF_MESSAGE, 10)): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=100,
                        mode=NumberSelectorMode.SLIDER,
                    ),
                ),
                vol.Required(CONF_REMOVE_MESSAGE, default=_options.get(CONF_REMOVE_MESSAGE, False)): BooleanSelector(),
                vol.Required(CONF_AUTO_MARK_READ, default=_options.get(CONF_AUTO_MARK_READ, False)): BooleanSelector(),
                vol.Required(
                    CONF_REFRESH_ON_CARD_RENDER,
                    default=_options.get(CONF_REFRESH_ON_CARD_RENDER, DEFAULT_REFRESH_ON_CARD_RENDER),
                ): BooleanSelector(),
                vol.Required(
                    CONF_AUTO_FETCH_HISTORY,
                    default=_options.get(CONF_AUTO_FETCH_HISTORY, DEFAULT_AUTO_FETCH_HISTORY),
                ): BooleanSelector(),
                # How many days of location history to retain in the Store (the location-history
                # sensor is opt-in; this only matters once it is enabled). Lets HA keep far more
                # than the app's ~3-day window.
                vol.Required(
                    CONF_HISTORY_RETENTION_DAYS,
                    default=_options.get(CONF_HISTORY_RETENTION_DAYS, DEFAULT_HISTORY_RETENTION_DAYS),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=HISTORY_RETENTION_DAYS_MIN,
                        max=HISTORY_RETENTION_DAYS_MAX,
                        mode=NumberSelectorMode.SLIDER,
                    ),
                ),
            }
        )

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle options flow."""
        errors: dict[str, str] = {}
        # Reuse the live coordinator's already-authenticated controller when the entry is loaded,
        # so opening the options screen doesn't trigger a fresh full login (and its retry churn)
        # against the rate-limit-sensitive auth endpoint every time. Fall back to a one-off login
        # only when no loaded coordinator exists (e.g. the entry failed to set up).
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        controller = coordinator.controller if coordinator is not None else await sign_in(hass=self.hass, data=self.config_entry.data)
        watches = await controller.setDevices()
        _options = self.config_entry.options

        schema = OrderedDict()
        schema[vol.Required(CONF_WATCHES, default=_options.get(CONF_WATCHES, watches))] = SelectSelector(
            SelectSelectorConfig(
                options=[
                    # Keep the watch id as the stored value (it is matched against CONF_WATCHES
                    # throughout), but show the child's name in the list.
                    SelectOptionDict(
                        value=watch,
                        label=watch_user_label(controller, watch),
                    )
                    for watch in watches
                ],
                multiple=True,
                mode=SelectSelectorMode.LIST,
            )
        )

        language: str = resolve_language(self.config_entry)

        signin_typ = [
            (
                SIGNIN.get(language, SIGNIN[DEFAULT_LANGUAGE])[CONF_EMAIL]
                if CONF_EMAIL in self.config_entry.data
                else SIGNIN.get(language, SIGNIN[DEFAULT_LANGUAGE])[CONF_PHONENUMBER]
            )
        ]

        home_state = self.hass.states.get(HOME)
        if home_state is None:
            raise HomeAssistantError(f"Zone '{HOME}' not found")
        options = self.get_options(signin_typ, schema, language, _options, home_state.attributes)

        if user_input is not None:
            errors = validate_options_input(user_input)

            if not errors:
                # The preset selector hands back the seconds as a string; store the canonical
                # int so the coordinator can use it directly (and stays consistent with the
                # legacy int-typed option).
                user_input[CONF_SCAN_INTERVAL] = normalize_scan_interval(user_input.get(CONF_SCAN_INTERVAL))
                user_input[CONF_SCAN_INTERVAL_FUNCTIONS] = normalize_scan_interval_functions(user_input.get(CONF_SCAN_INTERVAL_FUNCTIONS))
                # Store the canonical (clamped) int so the coordinator can use it directly.
                user_input[CONF_HISTORY_RETENTION_DAYS] = normalize_history_retention_days(user_input.get(CONF_HISTORY_RETENTION_DAYS))
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(step_id="init", data_schema=options, errors=errors)
