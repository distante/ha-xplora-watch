"""Tests for options_update_listener in custom_components/xplora_watch/__init__.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch import options_update_listener


async def test_options_update_listener_reloads_the_entry(hass, mock_config_entry_phone: MockConfigEntry) -> None:
    """Updating options triggers a reload of the config entry."""
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as mock_reload:
        await options_update_listener(hass, mock_config_entry_phone)

    mock_reload.assert_awaited_once_with(mock_config_entry_phone.entry_id)
