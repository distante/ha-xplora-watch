"""Seam A: config-entry setup -> entity-registry contents for a Guardian vs a Contact watch.

A watch the account is only a *Contact* of is sent no battery/location/alarm data and may not
control the watch, so the integration creates only the entities a Contact can actually use and
strips the Guardian-only ones it created in an earlier version (ref:XW-009). These tests let the
real platforms load (like test_full_setup_flow.py) and assert which entities land in the registry --
external behavior, not internal flags. The Guardian/Contact role rides on `deviceList.guardianType`,
so a Contact is built with `make_device_list_payload(guardian_type="SECOND")`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.xplora_watch.fixtures.graphql_payloads import make_device_list_payload

# Entities a Contact keeps: online status, steps, xcoin, chat, last-update and the Update button.
# Slugs carry the trailing account token ("parent_name", from the default login display name).
CONTACT_KEPT_ENTITY_IDS = {
    "binary_sensor.xplora_kid_one_watch_state_parent_name",
    "sensor.xplora_kid_one_watch_step_day_parent_name",
    "sensor.xplora_kid_one_watch_xcoin_parent_name",
    "sensor.xplora_kid_one_watch_message_parent_name",
    "sensor.xplora_kid_one_watch_last_update_parent_name",
    "button.xplora_kid_one_watch_update_parent_name",
}

# Guardian-only entities a Contact must NOT get (the device tracker is covered by the domain check).
GUARDIAN_ONLY_ENTITY_IDS = {
    "sensor.xplora_kid_one_watch_battery_parent_name",
    "sensor.xplora_kid_one_watch_distance_parent_name",
    "sensor.xplora_kid_one_watch_alarms_parent_name",
    "sensor.xplora_kid_one_watch_silents_parent_name",
    "sensor.xplora_kid_one_watch_location_history_parent_name",
    "binary_sensor.xplora_kid_one_watch_charging_parent_name",
    "binary_sensor.xplora_kid_one_watch_safezone_parent_name",
    "button.xplora_kid_one_watch_reboot_parent_name",
    "button.xplora_kid_one_watch_shutdown_parent_name",
    "button.xplora_kid_one_watch_refresh_functions_parent_name",
    "device_tracker.xplora_kid_one_watch_tracker_parent_name",
}


def _patch_fs_helpers():
    return (
        patch("custom_components.xplora_watch.create_www_directory", new=AsyncMock()),
        patch("custom_components.xplora_watch.create_service_yaml_file", new=AsyncMock()),
    )


async def _setup_and_collect_entity_ids(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    patch_www, patch_yaml = _patch_fs_helpers()
    with patch_www, patch_yaml:
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert result is True
    assert entry.state.value == "loaded"
    registry = er.async_get(hass)
    return {e.entity_id for e in registry.entities.values() if e.config_entry_id == entry.entry_id}


async def test_contact_watch_creates_only_contact_allowed_entities(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    mock_graphql,
    mock_geocoding_openstreetmap,
    mock_entity_picture,
    graphql_operations,
) -> None:
    """A confirmed Contact (SECOND) gets only the kept entities and none of the Guardian-only ones."""
    graphql_operations["deviceList"] = {"data": make_device_list_payload(guardian_type="SECOND")}

    entity_ids = await _setup_and_collect_entity_ids(hass, mock_config_entry_phone)

    assert CONTACT_KEPT_ENTITY_IDS <= entity_ids
    assert GUARDIAN_ONLY_ENTITY_IDS.isdisjoint(entity_ids)
    # The device-tracker platform creates nothing at all for a Contact (every tracker is location-based).
    assert not any(eid.startswith("device_tracker.") for eid in entity_ids)


async def test_guardian_watch_creates_the_full_set(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    mock_graphql,
    mock_geocoding_openstreetmap,
    mock_entity_picture,
) -> None:
    """Regression: a Guardian (FIRST, the fixture default) still gets every entity."""
    entity_ids = await _setup_and_collect_entity_ids(hass, mock_config_entry_phone)

    assert CONTACT_KEPT_ENTITY_IDS <= entity_ids
    assert GUARDIAN_ONLY_ENTITY_IDS <= entity_ids


async def test_unknown_role_is_treated_as_guardian(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    mock_graphql,
    mock_geocoding_openstreetmap,
    mock_entity_picture,
    graphql_operations,
) -> None:
    """A watch whose role couldn't be resolved (no guardianType) fails open to the full set."""
    payload = make_device_list_payload()
    del payload["deviceList"][0]["guardianType"]
    graphql_operations["deviceList"] = {"data": payload}

    entity_ids = await _setup_and_collect_entity_ids(hass, mock_config_entry_phone)

    # No silent stripping -- the Guardian-only entities are all present.
    assert GUARDIAN_ONLY_ENTITY_IDS <= entity_ids


async def test_setup_removes_guardian_only_entity_left_over_for_a_contact_watch(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    mock_graphql,
    mock_geocoding_openstreetmap,
    mock_entity_picture,
    graphql_operations,
) -> None:
    """Upgrade cleanup: a Guardian-only entity registered by an earlier version is swept away, while
    a kept Contact entity survives."""
    graphql_operations["deviceList"] = {"data": make_device_list_payload(guardian_type="SECOND")}

    registry = er.async_get(hass)
    # Pre-seed entities as a prior version left them, in the legacy dash-separated unique-id shape:
    # a Guardian-only battery sensor and a kept online (state) binary sensor. `_async_migrate_entries`
    # normalizes these to the canonical `kid_one_watch_<kind>_watch_id_001_user_id_001` during setup,
    # then the contact sweep removes the battery and leaves the online state. (A canonical-from-the-
    # start id would be double-suffixed by that same migration here, because this fixture's user id
    # carries dashes -- not a concern with real, dash-free Xplora user ids.)
    stale_battery = registry.async_get_or_create(
        "sensor", "xplora_watch", "Kid One-watch-battery-watch-id-001-user-id-001", config_entry=mock_config_entry_phone
    )
    kept_state = registry.async_get_or_create(
        "binary_sensor", "xplora_watch", "Kid One-watch-state-watch-id-001-user-id-001", config_entry=mock_config_entry_phone
    )

    await _setup_and_collect_entity_ids(hass, mock_config_entry_phone)

    assert registry.async_get(stale_battery.entity_id) is None
    assert registry.async_get(kept_state.entity_id) is not None
