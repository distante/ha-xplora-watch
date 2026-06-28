"""Tests for XploraBinarySensor.is_on and .icon."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.binary_sensor import BINARY_SENSOR_TYPES, XploraBinarySensor
from custom_components.xplora_watch.const import (
    ATTR_TRACKER_LAT,
    ATTR_TRACKER_LNG,
    BINARY_SENSOR_CHARGING,
    BINARY_SENSOR_SAFEZONE,
    BINARY_SENSOR_STATE,
    CONF_HOME_SAFEZONE,
    HOME,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _description(key: str):
    """Look up a BinarySensorEntityDescription from BINARY_SENSOR_TYPES by key."""
    return next(description for description in BINARY_SENSOR_TYPES if description.key == key)


def _make_binary_sensor(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: XploraDataUpdateCoordinator,
    key: str,
) -> XploraBinarySensor:
    ward = coordinator.controller.watchs[0]["ward"]
    sensor = XploraBinarySensor(config_entry, coordinator, ward, DEFAULT_WUID, _description(key))
    sensor.hass = hass
    return sensor


async def test_is_on_charging_passthrough(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    coordinator_with_data.data[DEFAULT_WUID]["isCharging"] = True
    sensor = _make_binary_sensor(hass, mock_config_entry_phone, coordinator_with_data, BINARY_SENSOR_CHARGING)
    assert sensor.is_on is True


async def test_is_on_charging_false(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    coordinator_with_data.data[DEFAULT_WUID]["isCharging"] = False
    sensor = _make_binary_sensor(hass, mock_config_entry_phone, coordinator_with_data, BINARY_SENSOR_CHARGING)
    assert sensor.is_on is False


async def test_is_on_state_passthrough(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    coordinator_with_data.data[DEFAULT_WUID]["isOnline"] = True
    sensor = _make_binary_sensor(hass, mock_config_entry_phone, coordinator_with_data, BINARY_SENSOR_STATE)
    assert sensor.is_on is True

    coordinator_with_data.data[DEFAULT_WUID]["isOnline"] = False
    assert sensor.is_on is False


async def test_is_on_safezone_off_uses_raw_flag(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """CONF_HOME_SAFEZONE defaults to "off" in mock_config_entry_phone, so the raw isSafezone flag is used."""
    coordinator_with_data.data[DEFAULT_WUID]["isSafezone"] = True
    sensor = _make_binary_sensor(hass, mock_config_entry_phone, coordinator_with_data, BINARY_SENSOR_SAFEZONE)
    assert sensor.is_on is True

    coordinator_with_data.data[DEFAULT_WUID]["isSafezone"] = False
    assert sensor.is_on is False


async def test_is_on_safezone_on_within_radius_returns_false(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
    mock_home_zone: None,
) -> None:
    """With CONF_HOME_SAFEZONE "on" and watch location inside the home radius, is_on is False."""
    hass.config_entries.async_update_entry(
        mock_config_entry_phone,
        options={**mock_config_entry_phone.options, CONF_HOME_SAFEZONE: STATE_ON},
    )
    # mock_home_zone and the default watch location are both at (52.5200, 13.4050), well within radius=100.
    coordinator_with_data.data[DEFAULT_WUID][ATTR_TRACKER_LAT] = 52.5200
    coordinator_with_data.data[DEFAULT_WUID][ATTR_TRACKER_LNG] = 13.4050
    sensor = _make_binary_sensor(hass, mock_config_entry_phone, coordinator_with_data, BINARY_SENSOR_SAFEZONE)

    assert sensor.is_on is False


async def test_is_on_safezone_on_outside_radius_falls_back_to_raw_flag(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
    mock_home_zone: None,
) -> None:
    """With CONF_HOME_SAFEZONE "on" but the watch far outside the radius, is_on falls back to the raw isSafezone flag."""
    hass.config_entries.async_update_entry(
        mock_config_entry_phone,
        options={**mock_config_entry_phone.options, CONF_HOME_SAFEZONE: STATE_ON},
    )
    coordinator_with_data.data[DEFAULT_WUID][ATTR_TRACKER_LAT] = 10.0
    coordinator_with_data.data[DEFAULT_WUID][ATTR_TRACKER_LNG] = 10.0
    coordinator_with_data.data[DEFAULT_WUID]["isSafezone"] = True
    sensor = _make_binary_sensor(hass, mock_config_entry_phone, coordinator_with_data, BINARY_SENSOR_SAFEZONE)

    assert sensor.is_on is True


async def test_is_on_safezone_on_missing_home_zone_returns_false(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """With CONF_HOME_SAFEZONE "on" but no zone.home state at all, is_on returns False."""
    assert hass.states.get(HOME) is None
    hass.config_entries.async_update_entry(
        mock_config_entry_phone,
        options={**mock_config_entry_phone.options, CONF_HOME_SAFEZONE: STATE_ON},
    )
    sensor = _make_binary_sensor(hass, mock_config_entry_phone, coordinator_with_data, BINARY_SENSOR_SAFEZONE)

    assert sensor.is_on is False


async def test_icon_charging_off_returns_battery_unknown(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    coordinator_with_data.data[DEFAULT_WUID]["isCharging"] = False
    sensor = _make_binary_sensor(hass, mock_config_entry_phone, coordinator_with_data, BINARY_SENSOR_CHARGING)
    assert sensor.icon == "mdi:battery-unknown"


async def test_icon_charging_on_falls_back_to_entity_description(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """When charging is True, icon falls back to _attr_icon/entity_description.icon (None here, no icon set)."""
    coordinator_with_data.data[DEFAULT_WUID]["isCharging"] = True
    sensor = _make_binary_sensor(hass, mock_config_entry_phone, coordinator_with_data, BINARY_SENSOR_CHARGING)
    assert sensor.icon == sensor.entity_description.icon


async def test_icon_non_charging_type_falls_back_to_entity_description(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    sensor = _make_binary_sensor(hass, mock_config_entry_phone, coordinator_with_data, BINARY_SENSOR_STATE)
    assert sensor.icon == sensor.entity_description.icon
