"""Tests for XploraBaseEntity (entity.py)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.config import resolve
from custom_components.xplora_watch.const import (
    CONF_ACCOUNT_ALIAS,
    CONF_REFRESH_ON_CARD_RENDER,
    DOMAIN,
    MANUFACTURER,
    TRACKER_UPDATE_STR,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.entity import XploraBaseEntity
from tests.xplora_watch.fixtures.graphql_payloads import (
    DEFAULT_ACCOUNT_NAME,
    DEFAULT_USER_ID,
    DEFAULT_WARD_NAME,
    DEFAULT_WUID,
    make_login_payload,
)


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
    # The device name is human-friendly ("<child> Watch (<account token>)"), with the watch id
    # NOT appended. The token differentiates the same watch linked to several accounts; with no
    # alias set yet it is the Account display name ("Parent Name"), shown verbatim.
    assert entity.watch_name == DEFAULT_WARD_NAME
    assert entity.account_token == DEFAULT_ACCOUNT_NAME
    assert device_info["name"] == f"{DEFAULT_WARD_NAME} Watch ({DEFAULT_ACCOUNT_NAME})"
    assert DEFAULT_WUID not in device_info["name"]

    # has_entity_name composition + concise branded object id (already slugified; DEFAULT_WARD_NAME
    # "Kid One" -> "kid_one", token "Parent Name" -> trailing "parent_name"). Assigned to entity_id
    # by subclasses so HA uses it verbatim (no device-name prefix).
    assert entity._attr_has_entity_name is True
    assert entity.branded_object_id("battery") == "xplora_kid_one_watch_battery_parent_name"


async def test_account_token_falls_back_to_account_id_when_display_name_empty(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    mock_graphql,
    mock_geocoding_openstreetmap,
    graphql_operations,
) -> None:
    """With no alias and an empty Account display name, the token falls back to the account id.

    The display-name -> account-id step is driven by varying the ``User`` payload: an empty
    ``user.name`` makes ``getUserName()`` return "", so the resolver yields the opaque account id
    in both the device name and the entity slug.
    """
    graphql_operations["signInWithEmailOrPhone"] = {"data": make_login_payload(user_name="")}

    coord = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
    session = aiohttp_client.async_get_clientsession(hass)
    await coord.init(session=session)
    await coord.async_update_xplora_data()

    entity = _make_entity(mock_config_entry_phone, coord)

    assert entity.account_token == DEFAULT_USER_ID
    assert entity._attr_device_info["name"] == f"{DEFAULT_WARD_NAME} Watch ({DEFAULT_USER_ID})"
    # The account id ("user-id-001") is slugified into the trailing slug segment ("user_id_001").
    assert entity.branded_object_id("battery") == "xplora_kid_one_watch_battery_user_id_001"


async def test_same_watch_two_accounts_get_distinct_names_and_slugs(
    mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """Two accounts linking the *same* watch (same wuid) get distinct device names and slugs.

    The account token is the only differentiator: with different Account display names the device
    name suffix and the trailing slug segment diverge, so the two copies are tellable apart even
    though the watch is physically one device.
    """
    # Account A: display name "Parent Name" (fixture default).
    entity_a = _make_entity(mock_config_entry_phone, coordinator_with_data)

    # Account B: same watch, different account display name -> different token.
    coordinator_with_data.username = "Other Parent"
    entity_b = _make_entity(mock_config_entry_phone, coordinator_with_data)

    assert entity_a.watch_uid == entity_b.watch_uid == DEFAULT_WUID  # the one physical watch

    assert entity_a._attr_device_info["name"] == f"{DEFAULT_WARD_NAME} Watch ({DEFAULT_ACCOUNT_NAME})"
    assert entity_b._attr_device_info["name"] == f"{DEFAULT_WARD_NAME} Watch (Other Parent)"
    assert entity_a._attr_device_info["name"] != entity_b._attr_device_info["name"]

    assert entity_a.branded_object_id("battery") == "xplora_kid_one_watch_battery_parent_name"
    assert entity_b.branded_object_id("battery") == "xplora_kid_one_watch_battery_other_parent"
    assert entity_a.branded_object_id("battery") != entity_b.branded_object_id("battery")


async def test_user_set_alias_drives_device_name_and_slug(
    mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """A user-set alias (captured in entry.data at setup) is the account token.

    It overrides the account display name in both the device name suffix and the trailing slug
    segment, so the user's chosen label ("Dad") -- not the Xplora profile name ("Parent Name") --
    differentiates this copy of the watch.
    """
    coordinator_with_data.hass.config_entries.async_update_entry(
        mock_config_entry_phone, data={**mock_config_entry_phone.data, CONF_ACCOUNT_ALIAS: "Dad"}
    )

    entity = _make_entity(mock_config_entry_phone, coordinator_with_data)

    assert entity.account_token == "Dad"
    assert entity._attr_device_info["name"] == f"{DEFAULT_WARD_NAME} Watch (Dad)"
    assert entity.branded_object_id("battery") == "xplora_kid_one_watch_battery_dad"


async def test_alias_option_overrides_data_for_device_name(
    mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """An options-flow alias edit (entry.options) overrides the setup alias (entry.data).

    This is the "edit the alias later" path: the device name reflects the edited value because the
    token is recomputed on each load from options -> data.
    """
    coordinator_with_data.hass.config_entries.async_update_entry(
        mock_config_entry_phone,
        data={**mock_config_entry_phone.data, CONF_ACCOUNT_ALIAS: "Dad"},
        options={**mock_config_entry_phone.options, CONF_ACCOUNT_ALIAS: "Mom"},
    )

    entity = _make_entity(mock_config_entry_phone, coordinator_with_data)

    assert entity.account_token == "Mom"
    assert entity._attr_device_info["name"] == f"{DEFAULT_WARD_NAME} Watch (Mom)"


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
