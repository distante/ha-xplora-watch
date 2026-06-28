"""Tests for sensor.py's async_setup_entry entity creation/filtering."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import (
    CONF_WATCHES,
    DOMAIN,
    SENSOR_ALARMS,
    SENSOR_BATTERY,
    SENSOR_DISTANCE,
    SENSOR_LAST_UPDATE,
    SENSOR_LOCATION_HISTORY,
    SENSOR_MESSAGE,
    SENSOR_SILENTS,
    SENSOR_STEP_DAY,
    SENSOR_XCOIN,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.sensor import XploraHistorySensor, XploraListSensor, XploraSensor, async_setup_entry
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _capture() -> tuple[list, object]:
    """Build a captured-entities list plus an async_add_entities-shaped closure."""
    captured: list = []

    def capture_entities(new_entities, update_before_add=False) -> None:  # noqa: ARG001
        captured.extend(new_entities)

    return captured, capture_entities


async def test_async_setup_entry_creates_all_sensors(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """All value sensors plus the alarm/silent list sensors are created per watch in CONF_WATCHES."""
    hass.data.setdefault(DOMAIN, {})[mock_config_entry_phone.entry_id] = coordinator_with_data
    captured, capture_entities = _capture()

    await async_setup_entry(hass, mock_config_entry_phone, capture_entities)

    # 6 value sensors (XploraSensor) + 2 list sensors (XploraListSensor) + 1 history sensor.
    assert len(captured) == 9
    value_keys = {e.entity_description.key for e in captured if isinstance(e, XploraSensor)}
    list_keys = {e.entity_description.key for e in captured if isinstance(e, XploraListSensor)}
    history_keys = {e.entity_description.key for e in captured if isinstance(e, XploraHistorySensor)}
    assert value_keys == {SENSOR_BATTERY, SENSOR_STEP_DAY, SENSOR_XCOIN, SENSOR_MESSAGE, SENSOR_DISTANCE, SENSOR_LAST_UPDATE}
    assert list_keys == {SENSOR_ALARMS, SENSOR_SILENTS}
    assert history_keys == {SENSOR_LOCATION_HISTORY}


async def test_only_battery_enabled_by_default(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """Battery is enabled by default; step/xcoin/message/distance are disabled-by-default.

    All sensors are now always created (gated only by CONF_WATCHES); which ones show up in the
    UI is controlled per entity via `entity_registry_enabled_default`, not a type selection.
    """
    hass.data.setdefault(DOMAIN, {})[mock_config_entry_phone.entry_id] = coordinator_with_data
    captured, capture_entities = _capture()

    await async_setup_entry(hass, mock_config_entry_phone, capture_entities)

    enabled = {e.entity_description.key for e in captured if e.entity_registry_enabled_default}
    disabled = {e.entity_description.key for e in captured if not e.entity_registry_enabled_default}
    # Battery and the last-update status are enabled by default; the rest (incl. location history)
    # are opt-in.
    assert enabled == {SENSOR_BATTERY, SENSOR_LAST_UPDATE}
    assert disabled == {
        SENSOR_STEP_DAY,
        SENSOR_XCOIN,
        SENSOR_MESSAGE,
        SENSOR_DISTANCE,
        SENSOR_ALARMS,
        SENSOR_SILENTS,
        SENSOR_LOCATION_HISTORY,
    }


async def test_async_setup_entry_filters_out_watch_not_in_conf_watches(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """No entities are created when CONF_WATCHES excludes the only configured watch."""
    hass.config_entries.async_update_entry(
        mock_config_entry_phone,
        options={**mock_config_entry_phone.options, CONF_WATCHES: ["some-other-watch-id"]},
    )
    hass.data.setdefault(DOMAIN, {})[mock_config_entry_phone.entry_id] = coordinator_with_data
    captured, capture_entities = _capture()

    await async_setup_entry(hass, mock_config_entry_phone, capture_entities)

    assert captured == []


async def test_async_setup_entry_no_options_creates_no_entities(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """No entities are created when the config entry has no options at all."""
    hass.config_entries.async_update_entry(mock_config_entry_phone, options={})
    hass.data.setdefault(DOMAIN, {})[mock_config_entry_phone.entry_id] = coordinator_with_data
    captured, capture_entities = _capture()

    await async_setup_entry(hass, mock_config_entry_phone, capture_entities)

    assert captured == []


async def test_async_setup_entry_entities_use_coordinator(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """Every created sensor entity is wired to the same coordinator instance."""
    hass.data.setdefault(DOMAIN, {})[mock_config_entry_phone.entry_id] = coordinator_with_data
    captured, capture_entities = _capture()

    await async_setup_entry(hass, mock_config_entry_phone, capture_entities)

    assert captured
    for entity in captured:
        assert entity.coordinator is coordinator_with_data
        assert entity.watch_uid == DEFAULT_WUID
