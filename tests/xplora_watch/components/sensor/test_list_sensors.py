"""Tests for the stable alarm/silent list sensors (XploraListSensor)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.const import SENSOR_ALARMS, SENSOR_SILENTS
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.sensor import LIST_SENSOR_TYPES, XploraListSensor
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _description(key: str):
    return next(d for d in LIST_SENSOR_TYPES if d.key == key)


def _make_sensor(hass: HomeAssistant, config_entry: ConfigEntry, coordinator: XploraDataUpdateCoordinator, key: str) -> XploraListSensor:
    ward = coordinator.controller.watchs[0]["ward"]
    sensor = XploraListSensor(config_entry, coordinator, ward, DEFAULT_WUID, _description(key))
    sensor.hass = hass
    return sensor


async def test_alarms_sensor_state_and_attributes(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_ALARMS)

    # State is the entry count; the list is exposed under the "alarm" attribute.
    assert sensor.native_value == 1
    attrs = sensor.extra_state_attributes
    assert attrs["entry_id"] == mock_config_entry_phone.entry_id
    assert attrs["wuid"] == DEFAULT_WUID
    alarms = attrs["alarm"]
    assert len(alarms) == 1
    item = alarms[0]
    assert item["id"] == "alarm-1"
    assert item["start"] == "07:00"  # occurMin 420 -> HH:MM
    # weekRepeat "1111100" -> index 0=Sun..4=Thu set; rendered + canonical keys.
    assert item["weekdays"] == ["sun", "mon", "tue", "wed", "thu"]
    assert item["days"] == "Sun, Mon, Tue, Wed, Thu"


async def test_silents_sensor_state_and_attributes(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_SILENTS)

    assert sensor.native_value == 1
    silents = sensor.extra_state_attributes["silent"]
    assert len(silents) == 1
    assert silents[0]["start"] == "08:00"
    assert silents[0]["end"] == "15:00"


async def test_list_sensor_naming_and_unique_id(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_SILENTS)
    assert sensor._attr_has_entity_name is True
    assert sensor._attr_name == "Silents"
    assert sensor.entity_id.startswith("sensor.")
    # Role marker, then the trailing account token ("parent_name", the default display name).
    assert sensor.entity_id.endswith("_silents_parent_name")
    # unique_id is lower-cased with spaces/dashes -> underscores; assert the stable shape.
    assert "_silents_" in sensor._attr_unique_id
    assert sensor._attr_unique_id.endswith(coordinator_with_data.user_id.lower().replace("-", "_"))
    assert sensor._attr_unique_id == sensor._attr_unique_id.lower()


async def test_empty_list_yields_zero_state(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """An empty list -> state 0, and the entity still exists (no appear/disappear churn)."""
    coordinator_with_data.data[DEFAULT_WUID]["alarm"] = []
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data, SENSOR_ALARMS)
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes["alarm"] == []
