"""Tests for XploraOptionsFlowHandler.async_step_init's user_input submission/validation."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_LANGUAGE, CONF_SCAN_INTERVAL
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import (
    CONF_AUTO_FETCH_HISTORY,
    CONF_HISTORY_RETENTION_DAYS,
    CONF_HOME_LATITUDE,
    CONF_HOME_LONGITUDE,
    CONF_HOME_RADIUS,
    CONF_HOME_SAFEZONE,
    CONF_MAPS,
    CONF_MESSAGE,
    CONF_OPENCAGE_APIKEY,
    CONF_REMOVE_MESSAGE,
    CONF_SIGNIN_TYP,
    CONF_WATCHES,
    HISTORY_RETENTION_DAYS_MAX,
    MAPS,
)
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _base_submit_input(**overrides: Any) -> dict[str, Any]:
    user_input: dict[str, Any] = {
        CONF_SIGNIN_TYP: "Signed up with a phone number",
        CONF_WATCHES: [DEFAULT_WUID],
        CONF_LANGUAGE: "en",
        CONF_MAPS: MAPS[0],
        CONF_OPENCAGE_APIKEY: "",
        # Scan interval is now a preset SelectSelector; the UI hands back the seconds as a string.
        CONF_SCAN_INTERVAL: "1800",
        CONF_HOME_SAFEZONE: "off",
        CONF_HOME_LATITUDE: 52.5200,
        CONF_HOME_LONGITUDE: 13.4050,
        CONF_HOME_RADIUS: 100,
        CONF_MESSAGE: 10,
        CONF_REMOVE_MESSAGE: False,
    }
    user_input.update(overrides)
    return user_input


async def test_submit_happy_path_creates_options_entry(
    hass, mock_config_entry_phone: MockConfigEntry, mock_graphql, mock_home_zone
) -> None:
    result = await hass.config_entries.options.async_init(mock_config_entry_phone.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _base_submit_input(**{CONF_WATCHES: [DEFAULT_WUID], CONF_MAPS: MAPS[0]})
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_WATCHES] == [DEFAULT_WUID]
    assert result["data"][CONF_MAPS] == MAPS[0]
    # The string preset is coerced back to the canonical int before storage.
    assert result["data"][CONF_SCAN_INTERVAL] == 1800
    # The retention field is stored (default filled by the schema when omitted from the submit).
    assert isinstance(result["data"][CONF_HISTORY_RETENTION_DAYS], int)


async def test_submit_history_retention_is_stored(hass, mock_config_entry_phone: MockConfigEntry, mock_graphql, mock_home_zone) -> None:
    """An in-range retention value is normalized (kept) and stored as a canonical int."""
    result = await hass.config_entries.options.async_init(mock_config_entry_phone.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], _base_submit_input(**{CONF_HISTORY_RETENTION_DAYS: 14}))
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HISTORY_RETENTION_DAYS] == 14
    assert result["data"][CONF_HISTORY_RETENTION_DAYS] <= HISTORY_RETENTION_DAYS_MAX


async def test_submit_empty_watches_shows_no_watch_error(
    hass, mock_config_entry_phone: MockConfigEntry, mock_graphql, mock_home_zone
) -> None:
    result = await hass.config_entries.options.async_init(mock_config_entry_phone.entry_id)

    result = await hass.config_entries.options.async_configure(result["flow_id"], _base_submit_input(**{CONF_WATCHES: []}))

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "no_watch"}


async def test_submit_opencage_maps_without_apikey_shows_api_key_error(
    hass, mock_config_entry_phone: MockConfigEntry, mock_graphql, mock_home_zone
) -> None:
    result = await hass.config_entries.options.async_init(mock_config_entry_phone.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _base_submit_input(**{CONF_MAPS: MAPS[1], CONF_OPENCAGE_APIKEY: ""}),
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "api_key_error"}


async def test_submit_auto_fetch_history_stored(hass, mock_config_entry_phone: MockConfigEntry, mock_graphql, mock_home_zone) -> None:
    """``CONF_AUTO_FETCH_HISTORY`` is accepted and stored when submitted."""
    result = await hass.config_entries.options.async_init(mock_config_entry_phone.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _base_submit_input(**{CONF_AUTO_FETCH_HISTORY: True}),
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_AUTO_FETCH_HISTORY] is True
