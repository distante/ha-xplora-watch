"""Tests for async_setup_entry's orchestration logic in custom_components/xplora_watch/__init__.py."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import DOMAIN
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_USER_ID, make_device_list_payload


def _patch_fs_helpers():
    """Patch the filesystem helper imported by reference into __init__.py."""
    return patch("custom_components.xplora_watch.create_www_directory", new=AsyncMock())


async def test_setup_entry_happy_path(hass, mock_config_entry_phone: MockConfigEntry, mock_graphql, mock_geocoding_openstreetmap) -> None:
    """async_setup_entry sets up the coordinator, stores it, and forwards platforms."""
    patch_www = _patch_fs_helpers()
    with (
        patch_www,
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()) as mock_forward,
    ):
        result = await hass.config_entries.async_setup(mock_config_entry_phone.entry_id)
        await hass.async_block_till_done()

    assert result is True
    coordinator = hass.data[DOMAIN][mock_config_entry_phone.entry_id]
    assert coordinator.username == "Parent Name"
    assert coordinator.user_id == DEFAULT_USER_ID

    mock_forward.assert_awaited_once()
    forwarded_entry, forwarded_platforms = mock_forward.await_args.args
    assert forwarded_entry is mock_config_entry_phone
    # `notify` is deliberately excluded: it is a legacy `BaseNotificationService` (no
    # `async_setup_entry`) and is loaded via `discovery.async_load_platform`, not config-entry
    # forwarding -- forwarding it raises AttributeError on modern HA.
    assert {platform.value for platform in forwarded_platforms} == {
        "binary_sensor",
        "button",
        "device_tracker",
        "sensor",
    }


async def test_setup_entry_non_admin_watch_logs_info_and_still_succeeds(
    hass,
    mock_config_entry_phone: MockConfigEntry,
    mock_graphql,
    mock_geocoding_openstreetmap,
    graphql_operations,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A watch where the user is not the primary guardian logs an info message but still sets up."""
    # is_admin is derived from the deviceList item's guardianType (no Contacts call).
    graphql_operations["deviceList"] = {"data": make_device_list_payload(guardian_type="SECOND")}

    patch_www = _patch_fs_helpers()
    with (
        patch_www,
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
        caplog.at_level(logging.INFO),
    ):
        result = await hass.config_entries.async_setup(mock_config_entry_phone.entry_id)
        await hass.async_block_till_done()

    assert result is True
    assert "is not the primary guardian of the watch" in caplog.text
    assert mock_config_entry_phone.entry_id in hass.data[DOMAIN]
