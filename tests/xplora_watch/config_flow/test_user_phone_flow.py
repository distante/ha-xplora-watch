"""Tests for XploraConfigFlow.async_step_user_phone."""

from __future__ import annotations

from typing import Any

from homeassistant.data_entry_flow import FlowResultType

from custom_components.xplora_watch.const import DOMAIN

PHONE_USER_INPUT: dict[str, Any] = {
    "country_code": "+49",
    "phonenumber": "+491700000001",
    "password": "secret",
    "timezone": "Europe/Berlin",
    "userlang": "en-GB",
    "language": "en",
}


async def _start_phone_flow(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "user_phone"})


async def test_user_phone_no_input_shows_form(hass) -> None:
    result = await _start_phone_flow(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user_phone"


async def test_user_phone_happy_path_creates_entry(hass, mock_graphql) -> None:
    result = await _start_phone_flow(hass)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], PHONE_USER_INPUT)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Xplora®"
    assert result["data"] == PHONE_USER_INPUT


async def test_user_phone_invalid_phone_shows_error(hass, mock_graphql, graphql_operations) -> None:
    graphql_operations["CheckEmailOrPhoneExist"] = {"data": {"checkEmailOrPhoneExist": False}}

    result = await _start_phone_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], PHONE_USER_INPUT)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user_phone"
    assert result["errors"] == {"base": "phone_email_invalid"}


async def test_user_phone_login_failure_shows_error(hass, mock_graphql, graphql_operations) -> None:
    graphql_operations["signInWithEmailOrPhone"] = {"errors": [{"message": "bad creds"}]}

    result = await _start_phone_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], PHONE_USER_INPUT)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user_phone"
    assert result["errors"] == {"base": "pass_invalid"}


async def test_user_phone_duplicate_unique_id_aborts(hass, mock_graphql, mock_config_entry_phone) -> None:
    result = await _start_phone_flow(hass)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], PHONE_USER_INPUT)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
