"""Tests for _async_remove_orphaned_switch_entities (upgrade cleanup) in __init__.py.

The alarm/silent per-entry switches were replaced by list sensors; this purges the leftover
`switch.*` registry rows so they don't linger as "Unavailable" after an upgrade.
"""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch import _async_remove_orphaned_switch_entities


async def test_removes_only_switch_entities_of_this_entry(hass, mock_config_entry_phone: MockConfigEntry) -> None:
    registry = er.async_get(hass)
    alarm_switch = registry.async_get_or_create(
        "switch", "xplora_watch", "kid_one_watch_alarm_v1_wuid_userid", config_entry=mock_config_entry_phone
    )
    silent_switch = registry.async_get_or_create(
        "switch", "xplora_watch", "kid_one_watch_silent_v1_wuid_userid", config_entry=mock_config_entry_phone
    )
    # A non-switch entity of the same entry must be left untouched.
    battery = registry.async_get_or_create(
        "sensor", "xplora_watch", "kid_one_watch_battery_wuid_userid", config_entry=mock_config_entry_phone
    )

    _async_remove_orphaned_switch_entities(hass, mock_config_entry_phone)

    assert registry.async_get(alarm_switch.entity_id) is None
    assert registry.async_get(silent_switch.entity_id) is None
    assert registry.async_get(battery.entity_id) is not None


async def test_no_switch_entities_is_a_noop(hass, mock_config_entry_phone: MockConfigEntry) -> None:
    registry = er.async_get(hass)
    sensor = registry.async_get_or_create(
        "sensor", "xplora_watch", "kid_one_watch_alarms_wuid_userid", config_entry=mock_config_entry_phone
    )

    # Must not raise and must leave the (non-switch) list sensor in place.
    _async_remove_orphaned_switch_entities(hass, mock_config_entry_phone)

    assert registry.async_get(sensor.entity_id) is not None
