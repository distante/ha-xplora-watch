"""Tests for XploraConfigFlow.async_step_user_email."""

from __future__ import annotations

from typing import Any

from homeassistant.data_entry_flow import FlowResultType

from custom_components.xplora_watch.const import DOMAIN

EMAIL_USER_INPUT: dict[str, Any] = {
    "email": "parent@example.com",
    "password": "secret",
    "timezone": "Europe/Berlin",
    "userlang": "en-GB",
    "language": "en",
}


async def _start_email_flow(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "user_email"})


async def test_user_email_no_input_shows_form(hass) -> None:
    result = await _start_email_flow(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user_email"


async def test_user_email_happy_path_creates_entry(hass, mock_graphql) -> None:
    result = await _start_email_flow(hass)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], EMAIL_USER_INPUT)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Xplora®"
    assert result["data"] == EMAIL_USER_INPUT


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
