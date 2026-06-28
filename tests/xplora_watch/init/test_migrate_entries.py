"""Tests for _async_migrate_entries' unique_id rewrite branches in custom_components/xplora_watch/__init__.py."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch import _async_migrate_entries

NEW_UID = "userid"


async def test_already_migrated_unique_id_is_left_unchanged(hass, mock_config_entry_phone: MockConfigEntry) -> None:
    """An id already in the new `_`-only format containing new_uid is a no-op."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "xplora_watch", "watch_battery_watchid_userid", config_entry=mock_config_entry_phone)

    await _async_migrate_entries(hass, mock_config_entry_phone, NEW_UID)

    assert registry.async_get(entry.entity_id).unique_id == "watch_battery_watchid_userid"


async def test_legacy_dash_separated_id_containing_new_uid_is_rewritten(hass, mock_config_entry_phone: MockConfigEntry) -> None:
    """A legacy dash-separated id that already contains new_uid is normalized to `_`-separated lowercase."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "xplora_watch", "Watch-Battery-watchid-userid", config_entry=mock_config_entry_phone)

    await _async_migrate_entries(hass, mock_config_entry_phone, NEW_UID)

    assert registry.async_get(entry.entity_id).unique_id == "watch_battery_watchid_userid"


async def test_id_without_new_uid_gets_new_uid_appended(hass, mock_config_entry_phone: MockConfigEntry) -> None:
    """An id that doesn't contain new_uid at all gets it appended before normalization."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", "xplora_watch", "Watch-Battery-watchid", config_entry=mock_config_entry_phone)

    await _async_migrate_entries(hass, mock_config_entry_phone, NEW_UID)

    assert registry.async_get(entry.entity_id).unique_id == "watch_battery_watchid_userid"


async def test_collision_with_existing_unique_id_is_skipped(hass, mock_config_entry_phone: MockConfigEntry) -> None:
    """If the computed new_unique_id is already taken by another entity, migration is skipped (no-op)."""
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "xplora_watch", "watch_battery_watchid_userid", config_entry=mock_config_entry_phone)
    colliding = registry.async_get_or_create(
        "sensor",
        "xplora_watch",
        "Watch_Battery-watchid-userid",
        config_entry=mock_config_entry_phone,
        suggested_object_id="other",
    )

    await _async_migrate_entries(hass, mock_config_entry_phone, NEW_UID)

    assert registry.async_get(colliding.entity_id).unique_id == "Watch_Battery-watchid-userid"
