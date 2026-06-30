"""Tests for XploraConfigFlow.async_step_user_email."""

from __future__ import annotations

from typing import Any

from homeassistant.data_entry_flow import FlowResultType

from custom_components.xplora_watch.const import CONF_ACCOUNT_ALIAS, DOMAIN
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_ACCOUNT_NAME

EMAIL_USER_INPUT: dict[str, Any] = {
    "email": "parent@example.com",
    "password": "secret",
    "timezone": "Europe/Berlin",
    "userlang": "en-GB",
    "language": "en",
}


def _alias_default(result) -> str:
    """Pull the pre-filled default of the alias field out of a form's data_schema."""
    schema = result["data_schema"].schema
    alias_key = next(key for key in schema if getattr(key, "schema", key) == CONF_ACCOUNT_ALIAS)
    return alias_key.default() if callable(alias_key.default) else alias_key.default


async def _start_email_flow(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "user_email"})


async def test_user_email_no_input_shows_form(hass) -> None:
    result = await _start_email_flow(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user_email"


async def test_user_email_valid_credentials_advance_to_alias_step(hass, mock_graphql) -> None:
    """After valid credentials the flow advances to the alias step, pre-filled with getUserName()."""
    result = await _start_email_flow(hass)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], EMAIL_USER_INPUT)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "alias"
    # The required alias field is pre-filled with the Account display name from getUserName().
    assert _alias_default(result) == DEFAULT_ACCOUNT_NAME


async def test_user_email_happy_path_creates_entry_with_alias(hass, mock_graphql) -> None:
    result = await _start_email_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], EMAIL_USER_INPUT)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ACCOUNT_ALIAS: "Dad"})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Xplora®"
    # The credentials AND the submitted alias are stored in the entry's data.
    assert result["data"] == {**EMAIL_USER_INPUT, CONF_ACCOUNT_ALIAS: "Dad"}


async def test_user_email_invalid_email_shows_error(hass, mock_graphql, graphql_operations) -> None:
    graphql_operations["CheckEmailOrPhoneExist"] = {"data": {"checkEmailOrPhoneExist": False}}

    result = await _start_email_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], EMAIL_USER_INPUT)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user_email"
    assert result["errors"] == {"base": "phone_email_invalid"}


async def test_user_email_login_failure_shows_error(hass, mock_graphql, graphql_operations) -> None:
    graphql_operations["signInWithEmailOrPhone"] = {"errors": [{"message": "bad creds"}]}

    result = await _start_email_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], EMAIL_USER_INPUT)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user_email"
    assert result["errors"] == {"base": "pass_invalid"}


async def test_user_email_duplicate_unique_id_aborts(hass, mock_graphql, mock_config_entry_email) -> None:
    result = await _start_email_flow(hass)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], EMAIL_USER_INPUT)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
