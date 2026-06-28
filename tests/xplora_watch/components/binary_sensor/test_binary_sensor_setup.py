"""Tests for binary_sensor.py's async_setup_entry entity creation/filtering."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.binary_sensor import XploraBinarySensor, async_setup_entry
from custom_components.xplora_watch.const import (
    BINARY_SENSOR_CHARGING,
    BINARY_SENSOR_SAFEZONE,
    BINARY_SENSOR_STATE,
    CONF_WATCHES,
    DOMAIN,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _capture() -> tuple[list, object]:
    """Build a captured-entities list plus an async_add_entities-shaped closure."""
    captured: list = []

    def capture_entities(new_entities, update_before_add=False) -> None:  # noqa: ARG001
        captured.extend(new_entities)

    return captured, capture_entities


async def test_async_setup_entry_creates_all_binary_sensors(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """All 3 binary sensor types are created for any watch in CONF_WATCHES (type selection is gone)."""
    hass.data.setdefault(DOMAIN, {})[mock_config_entry_phone.entry_id] = coordinator_with_data
    captured, capture_entities = _capture()

    await async_setup_entry(hass, mock_config_entry_phone, capture_entities)

    assert len(captured) == 3
    assert all(isinstance(entity, XploraBinarySensor) for entity in captured)
    keys = {entity.entity_description.key for entity in captured}
    assert keys == {BINARY_SENSOR_CHARGING, BINARY_SENSOR_SAFEZONE, BINARY_SENSOR_STATE}


async def test_charging_and_state_enabled_safezone_disabled_by_default(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """Charging + online-state are enabled by default; safezone is disabled-by-default.

    All binary sensors are now always created (gated only by CONF_WATCHES); visibility is per
    entity via `entity_registry_enabled_default`, not a type selection.
    """
    hass.data.setdefault(DOMAIN, {})[mock_config_entry_phone.entry_id] = coordinator_with_data
    captured, capture_entities = _capture()

    await async_setup_entry(hass, mock_config_entry_phone, capture_entities)

    enabled = {e.entity_description.key for e in captured if e.entity_registry_enabled_default}
    disabled = {e.entity_description.key for e in captured if not e.entity_registry_enabled_default}
    assert enabled == {BINARY_SENSOR_CHARGING, BINARY_SENSOR_STATE}
    assert disabled == {BINARY_SENSOR_SAFEZONE}


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
    """Every created binary sensor entity is wired to the same coordinator instance."""
    hass.data.setdefault(DOMAIN, {})[mock_config_entry_phone.entry_id] = coordinator_with_data
    captured, capture_entities = _capture()

    await async_setup_entry(hass, mock_config_entry_phone, capture_entities)

    assert captured
    for entity in captured:
        assert entity.coordinator is coordinator_with_data
        assert entity.watch_uid == DEFAULT_WUID
