"""Tests for _async_migrate_entity_slugs (one-time tokened-slug upgrade) in __init__.py.

The integration began appending the per-account token to every entity slug
(``xplora_<ward>_watch_<key>`` -> ``..._<token>``) so the same watch linked to several accounts
no longer collides. Newly-created entities already carry it; this migration renames the entities an
existing install created *before* the token, so old and new look the same. It reuses the same
setup-time registry-maintenance seam as `_async_migrate_entries` / the contact sweep: pre-seed the
entity registry, run the function with the coordinator + watch ids, and assert the resulting
entity-registry `entity_id` (history/long-term statistics follow the registry rename; the
`unique_id` is untouched).
"""

from __future__ import annotations

import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch import _async_migrate_entity_slugs
from custom_components.xplora_watch.const import CONF_ACCOUNT_ALIAS
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_USER_ID, DEFAULT_WUID

# unique_id exactly as the entity platforms build it: ``{ward}_watch_{key}_{wuid}_{user_id}``,
# normalized (spaces/dashes -> underscores, lower-cased). The token feature left unique_id unchanged.
BATTERY_UNIQUE_ID = "kid_one_watch_battery_watch_id_001_user_id_001"
# The pre-token default slug the platform generated before the token was introduced (no trailing token).
OLD_BATTERY_OBJECT_ID = "xplora_kid_one_watch_battery"
# The tokened slug for the fixture's account (no alias -> display name "Parent Name").
NEW_BATTERY_OBJECT_ID = "xplora_kid_one_watch_battery_parent_name"


def _seed(registry, entry, domain, unique_id, object_id):
    """Pre-seed a registry entry whose current entity_id is ``object_id`` (a pre-upgrade install)."""
    return registry.async_get_or_create(domain, "xplora_watch", unique_id, config_entry=entry, suggested_object_id=object_id)


async def test_default_slug_entity_is_renamed_to_tokened_slug(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    """A default-slug entity is renamed to the tokened slug; the unique_id is untouched."""
    registry = er.async_get(hass)
    entry = _seed(registry, mock_config_entry_phone, "sensor", BATTERY_UNIQUE_ID, OLD_BATTERY_OBJECT_ID)
    assert entry.entity_id == f"sensor.{OLD_BATTERY_OBJECT_ID}"

    await _async_migrate_entity_slugs(hass, mock_config_entry_phone, coordinator, [DEFAULT_WUID])

    migrated = registry.async_get(f"sensor.{NEW_BATTERY_OBJECT_ID}")
    assert migrated is not None
    assert migrated.unique_id == BATTERY_UNIQUE_ID
    # The old slug is gone (renamed, not duplicated/orphaned).
    assert registry.async_get(f"sensor.{OLD_BATTERY_OBJECT_ID}") is None


async def test_registry_entry_is_preserved_not_duplicated(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    """The same registry entry is renamed in place: its registry id and unique_id are stable, so
    history / long-term statistics metadata follows the rename rather than being orphaned."""
    registry = er.async_get(hass)
    entry = _seed(registry, mock_config_entry_phone, "sensor", BATTERY_UNIQUE_ID, OLD_BATTERY_OBJECT_ID)
    original_registry_id = entry.id

    await _async_migrate_entity_slugs(hass, mock_config_entry_phone, coordinator, [DEFAULT_WUID])

    # The unique_id still resolves -- to the new entity_id -- and to the *same* registry entry.
    new_entity_id = registry.async_get_entity_id("sensor", "xplora_watch", BATTERY_UNIQUE_ID)
    assert new_entity_id == f"sensor.{NEW_BATTERY_OBJECT_ID}"
    assert registry.async_get(new_entity_id).id == original_registry_id
    # Exactly one entry for this config entry (nothing duplicated).
    assert len(er.async_entries_for_config_entry(registry, mock_config_entry_phone.entry_id)) == 1


async def test_migration_is_idempotent(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    """A second run is a no-op: the token is not appended twice."""
    registry = er.async_get(hass)
    _seed(registry, mock_config_entry_phone, "sensor", BATTERY_UNIQUE_ID, OLD_BATTERY_OBJECT_ID)

    await _async_migrate_entity_slugs(hass, mock_config_entry_phone, coordinator, [DEFAULT_WUID])
    await _async_migrate_entity_slugs(hass, mock_config_entry_phone, coordinator, [DEFAULT_WUID])

    assert registry.async_get(f"sensor.{NEW_BATTERY_OBJECT_ID}") is not None
    # No double-tokened slug was created.
    assert registry.async_get(f"sensor.{NEW_BATTERY_OBJECT_ID}_parent_name") is None
    assert len(er.async_entries_for_config_entry(registry, mock_config_entry_phone.entry_id)) == 1


async def test_manually_renamed_entity_is_left_untouched(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    """An entity whose current entity_id is NOT the old default slug (user renamed it) is skipped."""
    registry = er.async_get(hass)
    _seed(registry, mock_config_entry_phone, "sensor", BATTERY_UNIQUE_ID, "my_custom_battery")

    await _async_migrate_entity_slugs(hass, mock_config_entry_phone, coordinator, [DEFAULT_WUID])

    assert registry.async_get("sensor.my_custom_battery") is not None
    assert registry.async_get(f"sensor.{NEW_BATTERY_OBJECT_ID}") is None


async def test_mismatch_skip_is_logged_at_debug(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator: XploraDataUpdateCoordinator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When a known-watch entity is skipped because its current id isn't the pre-token default (a
    user rename, or a kind whose slug isn't its unique-id core), the skip is observable at DEBUG so
    an under-migration isn't silent."""
    registry = er.async_get(hass)
    _seed(registry, mock_config_entry_phone, "sensor", BATTERY_UNIQUE_ID, "my_custom_battery")

    with caplog.at_level(logging.DEBUG, logger="custom_components.xplora_watch"):
        await _async_migrate_entity_slugs(hass, mock_config_entry_phone, coordinator, [DEFAULT_WUID])

    assert any(
        record.levelno == logging.DEBUG and "sensor.my_custom_battery" in record.getMessage() and "slug" in record.getMessage().lower()
        for record in caplog.records
    ), "the mismatch-skip path must log the skipped entity at DEBUG"


async def test_pre_alias_entry_uses_account_id_fallback_when_display_name_empty(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    """A pre-alias entry whose Account display name is empty migrates using the account-id fallback."""
    coordinator.username = ""  # getUserName() empty -> token falls back to the opaque account id
    registry = er.async_get(hass)
    _seed(registry, mock_config_entry_phone, "sensor", BATTERY_UNIQUE_ID, OLD_BATTERY_OBJECT_ID)

    await _async_migrate_entity_slugs(hass, mock_config_entry_phone, coordinator, [DEFAULT_WUID])

    # The opaque account id ("user-id-001") slugifies into the trailing segment ("user_id_001").
    expected = f"sensor.{OLD_BATTERY_OBJECT_ID}_{DEFAULT_USER_ID.replace('-', '_')}"
    assert registry.async_get(expected) is not None
    assert registry.async_get(f"sensor.{OLD_BATTERY_OBJECT_ID}") is None


async def test_user_set_alias_drives_the_migrated_slug(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    """When the user set an alias, it (top of the resolver) is the trailing slug segment."""
    hass.config_entries.async_update_entry(mock_config_entry_phone, data={**mock_config_entry_phone.data, CONF_ACCOUNT_ALIAS: "Mom"})
    registry = er.async_get(hass)
    _seed(registry, mock_config_entry_phone, "sensor", BATTERY_UNIQUE_ID, OLD_BATTERY_OBJECT_ID)

    await _async_migrate_entity_slugs(hass, mock_config_entry_phone, coordinator, [DEFAULT_WUID])

    assert registry.async_get("sensor.xplora_kid_one_watch_battery_mom") is not None
    assert registry.async_get(f"sensor.{OLD_BATTERY_OBJECT_ID}") is None


async def test_collision_with_existing_tokened_slug_is_skipped(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    """If the tokened slug is already taken by another entity, the rename is skipped (no clobber)."""
    registry = er.async_get(hass)
    default = _seed(registry, mock_config_entry_phone, "sensor", BATTERY_UNIQUE_ID, OLD_BATTERY_OBJECT_ID)
    # A different entity already occupying the target tokened entity_id.
    occupier = _seed(registry, mock_config_entry_phone, "sensor", "some_other_unique_id", NEW_BATTERY_OBJECT_ID)

    await _async_migrate_entity_slugs(hass, mock_config_entry_phone, coordinator, [DEFAULT_WUID])

    # The default-slug entity keeps its old id; the occupier is untouched.
    assert registry.async_get(default.entity_id).unique_id == BATTERY_UNIQUE_ID
    assert registry.async_get(occupier.entity_id).unique_id == "some_other_unique_id"


async def test_entity_for_an_unknown_watch_is_left_untouched(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    """An entity whose unique_id does not end in a known watch's ``_{wuid}_{user_id}`` tail is skipped
    (its core can't be recovered, so it is conservatively left alone)."""
    registry = er.async_get(hass)
    _seed(registry, mock_config_entry_phone, "sensor", "kid_one_watch_battery_other_wuid_user_id_001", OLD_BATTERY_OBJECT_ID)

    await _async_migrate_entity_slugs(hass, mock_config_entry_phone, coordinator, [DEFAULT_WUID])

    assert registry.async_get(f"sensor.{OLD_BATTERY_OBJECT_ID}") is not None
    assert registry.async_get(f"sensor.{NEW_BATTERY_OBJECT_ID}") is None


async def test_token_that_slugifies_to_empty_is_a_noop(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    """If the resolved token slugifies to nothing, there is no trailing segment to add -- skip all."""
    # An all-punctuation alias wins the resolver (it is non-empty) but slugifies to "".
    hass.config_entries.async_update_entry(mock_config_entry_phone, data={**mock_config_entry_phone.data, CONF_ACCOUNT_ALIAS: "!!!"})
    registry = er.async_get(hass)
    _seed(registry, mock_config_entry_phone, "sensor", BATTERY_UNIQUE_ID, OLD_BATTERY_OBJECT_ID)

    await _async_migrate_entity_slugs(hass, mock_config_entry_phone, coordinator, [DEFAULT_WUID])

    assert registry.async_get(f"sensor.{OLD_BATTERY_OBJECT_ID}") is not None
