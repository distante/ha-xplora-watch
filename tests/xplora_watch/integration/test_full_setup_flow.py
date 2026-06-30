"""End-to-end smoke tests: config entry setup through real platform forwarding.

Unlike tests/xplora_watch/init/test_setup_entry.py (which mocks platform forwarding to keep
focus on __init__.py's own orchestration logic), these tests let the real platforms load so
entities actually land in the entity registry -- a final, broader sanity check on top of the
piecemeal coverage already validated in coordinator/, config_flow/, services/, and components/.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import DOMAIN


def _patch_fs_helpers():
    return (
        patch("custom_components.xplora_watch.create_www_directory", new=AsyncMock()),
        patch("custom_components.xplora_watch.create_service_yaml_file", new=AsyncMock()),
    )


async def test_full_setup_creates_entities_for_every_platform(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    mock_graphql,
    mock_geocoding_openstreetmap,
    mock_entity_picture,
) -> None:
    patch_www, patch_yaml = _patch_fs_helpers()
    with patch_www, patch_yaml:
        result = await hass.config_entries.async_setup(mock_config_entry_phone.entry_id)
        await hass.async_block_till_done()

    assert result is True
    assert mock_config_entry_phone.state.value == "loaded"

    registry = er.async_get(hass)
    entries = [e for e in registry.entities.values() if e.config_entry_id == mock_config_entry_phone.entry_id]
    platforms_seen = {e.domain for e in entries}

    assert "sensor" in platforms_seen
    assert "binary_sensor" in platforms_seen
    assert "device_tracker" in platforms_seen
    assert len(entries) > 0

    # Registered entity_ids use the concise branded form (with the trailing account token) and
    # are NOT prefixed with the device name. Regression guard: setting entity_id directly avoids
    # the device-name prefix that an overridden suggested_object_id would add (which produced ids
    # like "sensor.kid_one_watch_xplora_kid_one_watch_battery").
    entity_ids = {e.entity_id for e in entries}
    assert "sensor.xplora_kid_one_watch_battery_parent_name" in entity_ids
    assert all(e.entity_id.split(".", 1)[1].startswith("xplora_") for e in entries)


async def test_full_setup_then_unload_removes_coordinator(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    mock_graphql,
    mock_geocoding_openstreetmap,
    mock_entity_picture,
) -> None:
    patch_www, patch_yaml = _patch_fs_helpers()
    with patch_www, patch_yaml:
        await hass.config_entries.async_setup(mock_config_entry_phone.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry_phone.entry_id in hass.data[DOMAIN]

        unload_ok = await hass.config_entries.async_unload(mock_config_entry_phone.entry_id)
        await hass.async_block_till_done()

    assert unload_ok is True
    assert mock_config_entry_phone.entry_id not in hass.data[DOMAIN]
