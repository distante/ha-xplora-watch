"""Tests for XploraBaseEntity (entity.py)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.config import resolve
from custom_components.xplora_watch.const import CONF_REFRESH_ON_CARD_RENDER, DOMAIN, MANUFACTURER, TRACKER_UPDATE_STR
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.entity import XploraBaseEntity
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WARD_NAME, DEFAULT_WUID


def _make_entity(config_entry: MockConfigEntry, coordinator: XploraDataUpdateCoordinator, wuid: str = DEFAULT_WUID) -> XploraBaseEntity:
    return XploraBaseEntity(config_entry, None, coordinator, wuid)


async def test_init_sets_device_info_and_watch_uid(
    mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """Device info fields and watch_uid are populated from the coordinator/config entry."""
    entity = _make_entity(mock_config_entry_phone, coordinator_with_data)

    assert entity.watch_uid == DEFAULT_WUID
    device_info = entity._attr_device_info
    assert device_info["identifiers"] == {(DOMAIN, f"{mock_config_entry_phone.unique_id}_{DEFAULT_WUID}")}
    assert device_info["manufacturer"] == MANUFACTURER
    assert device_info["model"] == coordinator_with_data.data[DEFAULT_WUID].get("model")
    assert device_info["sw_version"] == coordinator_with_data.os_version
    # The device name is human-friendly ("<child> Watch"), with the watch id NOT appended.
    assert entity.watch_name == DEFAULT_WARD_NAME
    assert device_info["name"] == f"{DEFAULT_WARD_NAME} Watch"
    assert DEFAULT_WUID not in device_info["name"]

    # has_entity_name composition + concise branded object id (already slugified; DEFAULT_WARD_NAME
    # "Kid One" -> "kid_one"). Assigned to entity_id by subclasses so HA uses it verbatim (no
    # device-name prefix).
    assert entity._attr_has_entity_name is True
    assert entity.branded_object_id("battery") == "xplora_kid_one_watch_battery"


async def test_init_is_admin_true_by_default(
    mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """The default Contacts fixture has guardianType FIRST matching the user, so is_admin is True."""
    # is_admin is keyed per watch (wuid).
    assert coordinator_with_data.is_admin[DEFAULT_WUID] is True

    entity = _make_entity(mock_config_entry_phone, coordinator_with_data)

    assert entity.is_admin == " (Admin)-"


async def test_init_is_admin_false_when_not_admin(
    mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """When the coordinator's is_admin map says False for this watch, the suffix is plain '-'."""
    coordinator_with_data.is_admin[DEFAULT_WUID] = False

    entity = _make_entity(mock_config_entry_phone, coordinator_with_data)

    assert entity.is_admin == "-"


async def test_extra_state_attributes_exposes_refresh_on_card_render(
    mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """Every entity surfaces the `refresh_on_card_render` flag so the cards can read it; it mirrors
    the resolved option (default False), and reflects an explicit opt-in."""
    entity = _make_entity(mock_config_entry_phone, coordinator_with_data)
    assert entity.extra_state_attributes == {CONF_REFRESH_ON_CARD_RENDER: False}

    # With the option enabled, the same attribute reflects the opt-in.
    entity._resolved_options = resolve({CONF_REFRESH_ON_CARD_RENDER: True})
    assert entity.extra_state_attributes[CONF_REFRESH_ON_CARD_RENDER] is True


def test_states_disable_returns_false() -> None:
    """_states('DISABLE') returns False."""
    entity = XploraBaseEntity.__new__(XploraBaseEntity)
    assert entity._states("DISABLE") is False


def test_states_other_values_return_true() -> None:
    """_states returns True for anything other than the literal string 'DISABLE'."""
    entity = XploraBaseEntity.__new__(XploraBaseEntity)
    for status in ("ENABLE", "", "FOO"):
        assert entity._states(status) is True


async def test_async_added_to_hass_registers_dispatcher(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """async_added_to_hass subscribes to TRACKER_UPDATE_STR; async_will_remove_from_hass unsubscribes."""
    entity = _make_entity(mock_config_entry_phone, coordinator_with_data)
    entity.hass = hass
    entity.entity_id = "device_tracker.test_watch"

    assert entity._unsub_dispatchers == []

    await entity.async_added_to_hass()
    assert len(entity._unsub_dispatchers) == 1

    await entity.async_will_remove_from_hass()
    assert entity._unsub_dispatchers == []


async def test_async_receive_data_updates_when_device_matches(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """Dispatching TRACKER_UPDATE_STR for the matching watch_uid updates _location/_location_name.

    Note: this is currently dead code in production -- nothing else in the codebase dispatches
    TRACKER_UPDATE_STR -- so this test exercises the mechanism directly rather than a real call path.
    """
    entity = _make_entity(mock_config_entry_phone, coordinator_with_data)
    entity.hass = hass
    entity.entity_id = "device_tracker.test_watch"
    await entity.async_added_to_hass()

    async_dispatcher_send(hass, TRACKER_UPDATE_STR, DEFAULT_WUID, (1.23, 4.56), "Some Address")
    await hass.async_block_till_done()

    assert entity._location_name == "Some Address"
    assert entity._location == (1.23, 4.56)


async def test_async_receive_data_ignores_non_matching_device(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """Dispatching TRACKER_UPDATE_STR for a different device id is a no-op for this entity."""
    entity = _make_entity(mock_config_entry_phone, coordinator_with_data)
    entity.hass = hass
    entity.entity_id = "device_tracker.test_watch"
    await entity.async_added_to_hass()

    async_dispatcher_send(hass, TRACKER_UPDATE_STR, "some-other-watch-id", (9.99, 9.99), "Other Address")
    await hass.async_block_till_done()

    assert not hasattr(entity, "_location_name")
    assert not hasattr(entity, "_location")
