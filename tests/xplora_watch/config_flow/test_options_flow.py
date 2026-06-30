"""Tests for XploraOptionsFlowHandler.async_step_init's schema construction (get_options)."""

from __future__ import annotations

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import CONF_ACCOUNT_ALIAS, CONF_WATCHES
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_ACCOUNT_NAME, DEFAULT_WARD_NAME, DEFAULT_WUID


async def test_async_step_init_builds_schema_with_home_zone(
    hass, mock_config_entry_phone: MockConfigEntry, mock_graphql, mock_home_zone
) -> None:
    result = await hass.config_entries.options.async_init(mock_config_entry_phone.entry_id)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["data_schema"] is not None


async def test_async_step_init_watches_show_child_name_label(
    hass, mock_config_entry_phone: MockConfigEntry, mock_graphql, mock_home_zone
) -> None:
    """The watches selector stores the watch id but shows the child's name as the label."""
    result = await hass.config_entries.options.async_init(mock_config_entry_phone.entry_id)

    schema = result["data_schema"].schema
    watches_selector = next(value for key, value in schema.items() if getattr(key, "schema", key) == CONF_WATCHES)
    options = watches_selector.config["options"]
    assert {"value": DEFAULT_WUID, "label": DEFAULT_WARD_NAME} in options


async def test_async_step_init_offers_editable_alias_prefilled_with_display_name(
    hass, mock_config_entry_phone: MockConfigEntry, mock_graphql, mock_home_zone
) -> None:
    """The options flow exposes an editable alias field, pre-filled for a pre-alias entry.

    When no alias has been set yet (neither options nor data), the field defaults to the Account
    display name from ``getUserName()`` so an existing entry can adopt or change it.
    """
    result = await hass.config_entries.options.async_init(mock_config_entry_phone.entry_id)

    schema = result["data_schema"].schema
    alias_key = next(key for key in schema if getattr(key, "schema", key) == CONF_ACCOUNT_ALIAS)
    default = alias_key.default() if callable(alias_key.default) else alias_key.default
    assert default == DEFAULT_ACCOUNT_NAME


async def test_async_step_init_raises_without_home_zone(hass, mock_config_entry_phone: MockConfigEntry, mock_graphql) -> None:
    with pytest.raises(HomeAssistantError):
        await hass.config_entries.options.async_init(mock_config_entry_phone.entry_id)
