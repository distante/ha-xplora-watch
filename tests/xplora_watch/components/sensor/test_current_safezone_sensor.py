"""Tests for the `current_safezone` sensor: the watch-reported safezone label (ADR 0006).

The sensor is a PURE watch report: its state is the name of the Xplora safezone the watch says it
is currently inside, and unknown (`None`) when the watch reports being outside every safezone --
never a literal pseudo-state that could collide with a user's zone name. The `home_is_safezone`
option keeps affecting only the in/out binary sensor, not this label.
"""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant

from custom_components.xplora_watch import sensor as sensor_module
from custom_components.xplora_watch.binary_sensor import BINARY_SENSOR_TYPES, XploraBinarySensor
from custom_components.xplora_watch.const import BINARY_SENSOR_SAFEZONE, CONF_HOME_SAFEZONE
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.sensor import SENSOR_TYPES, XploraSensor
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID

CURRENT_SAFEZONE_KEY = "current_safezone"


def _description():
    description = next((d for d in SENSOR_TYPES if d.key == CURRENT_SAFEZONE_KEY), None)
    assert description is not None, "current_safezone sensor description is missing from SENSOR_TYPES"
    return description


def _make_sensor(hass: HomeAssistant, config_entry: ConfigEntry, coordinator: XploraDataUpdateCoordinator) -> XploraSensor:
    ward = coordinator.controller.watchs[0]["ward"]
    sensor = XploraSensor(config_entry, coordinator, ward, DEFAULT_WUID, _description())
    sensor.hass = hass
    return sensor


async def test_state_is_the_watch_reported_label_when_inside(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    # The watch reports being inside a safezone (`isSafezone` is the INVERTED alert flag:
    # False == inside/safe); the fixtures' location payloads label it "Home".
    coordinator_with_data.data[DEFAULT_WUID]["isSafezone"] = False
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data)

    assert sensor.native_value == "Home"


async def test_state_is_unknown_when_outside_every_safezone(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """Outside every safezone -> unknown, even though the payload still carries a label string.

    `None` (unknown), NEVER a literal pseudo-state like "outside": a user's safezone could be
    named anything, so a fixed string could collide with a real zone name.
    """
    # Fixture default: isInSafeZone False -> the inverted `isSafezone` alert flag is True (outside),
    # while the payload's `safeZoneLabel` still reads "Home".
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data)

    assert coordinator_with_data.data[DEFAULT_WUID]["isSafezone"] is True
    assert sensor.native_value is None


async def test_state_is_unknown_when_inside_but_label_empty(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    coordinator_with_data.data[DEFAULT_WUID]["isSafezone"] = False
    coordinator_with_data.data[DEFAULT_WUID]["safeZoneLabel"] = ""
    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data)

    assert sensor.native_value is None


async def test_label_ignores_home_is_safezone_while_binary_sensor_honors_it(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator, mock_home_zone
) -> None:
    """Decision split (ADR 0006): `home_is_safezone` shapes only the in/out binary sensor.

    With the option ON and the watch inside the home radius, the binary sensor reports safe
    (False) even though the WATCH says it is outside every Xplora safezone -- but the label
    sensor stays a pure watch report and remains unknown.
    """
    hass.config_entries.async_update_entry(
        mock_config_entry_phone, options={**mock_config_entry_phone.options, CONF_HOME_SAFEZONE: STATE_ON}
    )
    # Watch-reported: outside every safezone (fixture default), positioned exactly at zone.home.
    assert coordinator_with_data.data[DEFAULT_WUID]["isSafezone"] is True

    label_sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data)
    binary_description = next(d for d in BINARY_SENSOR_TYPES if d.key == BINARY_SENSOR_SAFEZONE)
    ward = coordinator_with_data.controller.watchs[0]["ward"]
    binary = XploraBinarySensor(mock_config_entry_phone, coordinator_with_data, ward, DEFAULT_WUID, binary_description)
    binary.hass = hass

    assert binary.is_on is False  # home radius wins for the in/out alert
    assert label_sensor.native_value is None  # ...but the label stays the watch's own report


async def test_current_safezone_is_diagnostic_disabled_by_default_and_translated(
    hass: HomeAssistant, mock_config_entry_phone, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    description = _description()
    assert description.entity_category == EntityCategory.DIAGNOSTIC
    assert description.entity_registry_enabled_default is False

    sensor = _make_sensor(hass, mock_config_entry_phone, coordinator_with_data)
    # Named via translations (entity.sensor.current_safezone.name), not a code-derived title.
    assert sensor._attr_translation_key == CURRENT_SAFEZONE_KEY
    assert getattr(sensor, "_attr_name", None) is None
    assert sensor.entity_id == "sensor.xplora_kid_one_watch_current_safezone_parent_name"
    assert sensor._attr_unique_id == "kid_one_watch_current_safezone_watch_id_001_user_id_001"


def test_current_safezone_name_is_translated_in_every_language() -> None:
    translations = Path(sensor_module.__file__).parent / "translations"
    expected = {"en": "Current safe zone", "de": "Aktuelle Sicherheitszone"}
    for lang_file in sorted(translations.glob("*.json")):
        data = json.loads(lang_file.read_text(encoding="utf-8"))
        name = data.get("entity", {}).get("sensor", {}).get(CURRENT_SAFEZONE_KEY, {}).get("name")
        assert name, f"{lang_file.name} misses entity.sensor.{CURRENT_SAFEZONE_KEY}.name"
        if lang_file.stem in expected:
            assert name == expected[lang_file.stem]
