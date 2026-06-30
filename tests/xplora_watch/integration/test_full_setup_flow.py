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


async def test_full_setup_migrates_a_pre_token_entity_in_place(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    mock_graphql,
    mock_geocoding_openstreetmap,
    mock_entity_picture,
) -> None:
    """An upgraded install's pre-token entity is renamed to the tokened slug *during setup*.

    Pre-seed the battery sensor at its old, un-tokened default slug (with the unchanged unique_id),
    then run full setup: the same registry entry must end up at the tokened slug (history preserved,
    its registry id stable) with no leftover old slug and no HA `_2` collision suffix.
    """
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor",
        "xplora_watch",
        "kid_one_watch_battery_watch_id_001_user_id_001",
        config_entry=mock_config_entry_phone,
        suggested_object_id="xplora_kid_one_watch_battery",
    )
    assert old.entity_id == "sensor.xplora_kid_one_watch_battery"
    original_registry_id = old.id

    patch_www, patch_yaml = _patch_fs_helpers()
    # Isolate the slug migration from the unrelated unique_id migration: with the dashed test
    # fixture ids (`user-id-001`), `_async_migrate_entries` would re-append the account id to the
    # pre-seeded unique_id (real Xplora ids are dash-free, so it is a no-op in production). Patching
    # it to a no-op reproduces that production no-op and keeps this test focused on the slug rename.
    patch_uid = patch("custom_components.xplora_watch._async_migrate_entries", new=AsyncMock(return_value=True))
    with patch_uid, patch_www, patch_yaml:
        await hass.config_entries.async_setup(mock_config_entry_phone.entry_id)
        await hass.async_block_till_done()

    # Renamed in place: same registry entry (history follows), now at the tokened slug.
    migrated = registry.async_get("sensor.xplora_kid_one_watch_battery_parent_name")
    assert migrated is not None
    assert migrated.id == original_registry_id
    # Old slug gone; no `_2` collision suffix from the platform re-claiming the slug.
    assert registry.async_get("sensor.xplora_kid_one_watch_battery") is None
    assert registry.async_get("sensor.xplora_kid_one_watch_battery_parent_name_2") is None


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
