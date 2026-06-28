"""Tests for XploraConfigFlow.async_step_user (the initial menu step)."""

from __future__ import annotations

from homeassistant.data_entry_flow import FlowResultType

from custom_components.xplora_watch.const import DOMAIN


async def test_user_step_shows_menu_with_email_and_phone_options(hass) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "user"
    assert result["menu_options"] == ["user_email", "user_phone"]
