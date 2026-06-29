"""Tests for XploraSensor.native_value and extra_state_attributes."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.const import (
    ATTR_TRACKER_LAT,
    ATTR_TRACKER_LNG,
    CONF_REFRESH_ON_CARD_RENDER,
    SENSOR_BATTERY,
    SENSOR_DISTANCE,
    SENSOR_MESSAGE,
    SENSOR_STEP_DAY,
    SENSOR_XCOIN,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.sensor import SENSOR_TYPES, XploraSensor
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _description(key: str):
    """Look up a SensorEntityDescription from SENSOR_TYPES by key."""
    return next(description for description in SENSOR_TYPES if description.key == key)


def _make_sensor(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: XploraDataUpdateCoordinator,
    key: str,
) -> XploraSensor:
    ward = coordinator.controller.watchs[0]["ward"]
    sensor = XploraSensor(config_entry, coordinator, ward, DEFAULT_WUID, _description(key))
    sensor.hass = hass
    return sensor


async def test_native_value_battery(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_BATTERY)
    assert sensor.native_value == 80


async def test_battery_naming_and_unchanged_unique_id(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """has_entity_name role-only name + branded entity_id seed, with unique_id kept unchanged."""
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_BATTERY)

    # Role-only name (device supplies "Kid One Watch") -> friendly "Kid One Watch Battery".
    assert sensor._attr_has_entity_name is True
    assert sensor._attr_name == "Battery"
    # entity_id is set directly (not via suggested_object_id) so it is NOT device-name-prefixed.
    assert sensor.entity_id == "sensor.xplora_kid_one_watch_battery"
    # unique_id is deliberately preserved (no migration) so existing history is kept.
    assert sensor._attr_unique_id == "kid_one_watch_battery_watch_id_001_user_id_001"


async def test_native_value_step_day(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_STEP_DAY)
    assert sensor.native_value == 1234


async def test_native_value_xcoin(hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator) -> None:
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_XCOIN)
    assert sensor.native_value == 10


async def test_native_value_message_returns_unread_count(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """SENSOR_MESSAGE's native_value is the unread count, not the chats dict."""
    coordinator_with_data.data[DEFAULT_WUID]["unreadMsg"] = 3
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_MESSAGE)
    assert sensor.native_value == 3


async def test_native_value_distance_with_lat_lng(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator, mock_home_zone
) -> None:
    """With lat/lng present and zone.home set, distance is computed (home == watch location here)."""
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_DISTANCE)
    assert sensor.native_value == 0


async def test_native_value_distance_without_lat_lng(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """Missing lat or lng short-circuits to -1 without touching the home zone at all."""
    coordinator_with_data.data[DEFAULT_WUID][ATTR_TRACKER_LAT] = None
    coordinator_with_data.data[DEFAULT_WUID][ATTR_TRACKER_LNG] = None
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_DISTANCE)
    assert sensor.native_value == -1


async def test_extra_state_attributes_message_merges_chats(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """SENSOR_MESSAGE merges the chats dict into attributes when it is truthy."""
    coordinator_with_data.data[DEFAULT_WUID][SENSOR_MESSAGE] = {"list": [{"id": "msg-1"}]}
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_MESSAGE)

    attributes = sensor.extra_state_attributes

    # The chats dict is merged in, plus `entry_id`/`wuid` (service targeting) and
    # `account_user_id` (so the chat card can tell outgoing from incoming messages). The base
    # entity also contributes the shared `refresh_on_card_render` flag the cards read.
    assert attributes == {
        CONF_REFRESH_ON_CARD_RENDER: False,
        "list": [{"id": "msg-1"}],
        "entry_id": mock_config_entry_phone.entry_id,
        "wuid": DEFAULT_WUID,
        "account_user_id": coordinator_with_data.user_id,
    }


async def test_extra_state_attributes_message_exposes_ids_when_no_chats_yet(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """Even with no chats fetched yet (empty dict), SENSOR_MESSAGE still exposes the static ids.

    Regression for the cold-start deadlock: chats are fetched only by the `read_message` service,
    which the chat card can only call once it can read `entry_id`/`wuid` from this sensor. Gating
    those ids on a non-empty chat dict meant the card could never issue the first fetch (no chats
    -> no ids -> can't fetch -> no chats). The ids must be present from the start; the chat payload
    (`list`) is simply absent until the first `read_message`.
    """
    coordinator_with_data.data[DEFAULT_WUID][SENSOR_MESSAGE] = {}
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_MESSAGE)

    attributes = sensor.extra_state_attributes

    assert attributes == {
        CONF_REFRESH_ON_CARD_RENDER: False,
        "entry_id": mock_config_entry_phone.entry_id,
        "wuid": DEFAULT_WUID,
        "account_user_id": coordinator_with_data.user_id,
    }


async def test_extra_state_attributes_other_types_add_user(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """Non-message sensor types add the `user` attribute (plus the shared base-entity flag)."""
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_BATTERY)

    attributes = sensor.extra_state_attributes

    assert attributes == {
        CONF_REFRESH_ON_CARD_RENDER: False,
        "user": coordinator_with_data.controller.getUserName(),
    }
