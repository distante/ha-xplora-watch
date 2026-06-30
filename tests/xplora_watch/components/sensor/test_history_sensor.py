"""Tests for the opt-in location-history sensor (XploraHistorySensor)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.const import (
    ATTR_HISTORY_POINTS,
    ATTR_HISTORY_TOTAL_POINTS,
    ATTR_HISTORY_WINDOW_HOURS,
    ATTR_LOCATION_HISTORY,
    LOC_HISTORY_ATTR_WINDOW_HOURS,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.sensor import HISTORY_SENSOR_TYPE, XploraHistorySensor
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID

_POINTS = [
    {"tm": 1700000000000, "lat": 52.52, "lng": 13.405, "rad": 35, "addr": "Teststrasse 1", "poi": "Home", "locateType": "GPS"},
    {"tm": 1700003600000, "lat": 52.521, "lng": 13.406, "rad": 40, "addr": "Schulstrasse 4", "poi": "School", "locateType": "WIFI"},
]


def _make_sensor(hass: HomeAssistant, config_entry: ConfigEntry, coordinator: XploraDataUpdateCoordinator) -> XploraHistorySensor:
    ward = coordinator.controller.watchs[0]["ward"]
    sensor = XploraHistorySensor(config_entry, coordinator, ward, DEFAULT_WUID, HISTORY_SENSOR_TYPE)
    sensor.hass = hass
    return sensor


async def test_history_sensor_state_and_attributes(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """State is the bounded point count; the points + total + window ride along as attributes."""
    coordinator_with_data.data[DEFAULT_WUID][ATTR_LOCATION_HISTORY] = {"points": _POINTS, "total": 9}
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data)

    assert sensor.native_value == 2
    attrs = sensor.extra_state_attributes
    assert attrs[ATTR_HISTORY_POINTS] == _POINTS
    assert attrs[ATTR_HISTORY_TOTAL_POINTS] == 9
    assert attrs[ATTR_HISTORY_WINDOW_HOURS] == LOC_HISTORY_ATTR_WINDOW_HOURS
    assert attrs["entry_id"] == mock_config_entry_phone.entry_id
    assert attrs["wuid"] == DEFAULT_WUID


async def test_history_sensor_empty_slice(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """With no accumulated points the state is 0 and the point list is empty (not missing)."""
    coordinator_with_data.data[DEFAULT_WUID][ATTR_LOCATION_HISTORY] = {"points": [], "total": 0}
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data)

    assert sensor.native_value == 0
    assert sensor.extra_state_attributes[ATTR_HISTORY_POINTS] == []


async def test_history_sensor_points_excluded_from_recorder(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """The (potentially large) point list is kept out of the recorder; the count state still records."""
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data)
    assert ATTR_HISTORY_POINTS in sensor._unrecorded_attributes


async def test_history_sensor_naming_and_unique_id(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """Stable, disabled-by-default entity whose unique_id carries the history marker."""
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data)
    assert sensor._attr_has_entity_name is True
    assert sensor._attr_name == "Location History"
    assert sensor.entity_id.startswith("sensor.")
    # Role marker, then the trailing account token ("parent_name", the default display name).
    assert sensor.entity_id.endswith("_location_history_parent_name")
    assert "_location_history_" in sensor._attr_unique_id
    assert sensor._attr_unique_id == sensor._attr_unique_id.lower()
    assert HISTORY_SENSOR_TYPE.entity_registry_enabled_default is False
