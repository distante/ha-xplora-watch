"""Tests for the per-watch last-update status.

Covers the coordinator recording the outcome (ok / no_response) on each refresh, the `last_update`
sensor surfacing it (state + last_update_time attribute), and the update button recording `error`
when the refresh fails.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.button import BUTTON_TYPES, XploraButton
from custom_components.xplora_watch.const import (
    ATTR_LAST_UPDATE_STATUS,
    ATTR_LAST_UPDATE_TIME,
    BUTTON_UPDATE,
    LAST_UPDATE_ERROR,
    LAST_UPDATE_NO_RESPONSE,
    LAST_UPDATE_OK,
    SENSOR_LAST_UPDATE,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.sensor import SENSOR_TYPES, XploraSensor
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID

_STATUSES = (LAST_UPDATE_OK, LAST_UPDATE_NO_RESPONSE)


def _sensor_desc(key: str):
    return next(d for d in SENSOR_TYPES if d.key == key)


def _button_desc(key: str):
    return next(d for d in BUTTON_TYPES if d.key == key)


async def test_coordinator_records_last_update_status(coordinator_with_data: XploraDataUpdateCoordinator) -> None:
    """After a refresh the coordinator stores a status (ok/no_response) and a timestamp per watch."""
    entry = coordinator_with_data.data[DEFAULT_WUID]
    assert entry[ATTR_LAST_UPDATE_STATUS] in _STATUSES
    assert entry[ATTR_LAST_UPDATE_TIME]  # ISO timestamp present


async def test_last_update_sensor_reads_status_and_time(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """The last_update sensor exposes the status as state and the time as an attribute."""
    ward = coordinator_with_data.controller.watchs[0]["ward"]
    sensor = XploraSensor(mock_config_entry_phone, coordinator_with_data, ward, DEFAULT_WUID, _sensor_desc(SENSOR_LAST_UPDATE))
    sensor.hass = hass

    assert sensor.native_value in _STATUSES
    assert ATTR_LAST_UPDATE_TIME in sensor.extra_state_attributes


async def test_update_button_failure_records_error(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """A failed update records `error` for the watch (so the sensor/cards show it) and re-raises."""
    ward = coordinator_with_data.controller.watchs[0]["ward"]
    button = XploraButton(mock_config_entry_phone, coordinator_with_data, ward, DEFAULT_WUID, _button_desc(BUTTON_UPDATE))
    button.hass = hass
    coordinator_with_data.async_update_xplora_data = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(HomeAssistantError):
        await button.async_press()

    assert coordinator_with_data.data[DEFAULT_WUID][ATTR_LAST_UPDATE_STATUS] == LAST_UPDATE_ERROR
    assert coordinator_with_data.data[DEFAULT_WUID][ATTR_LAST_UPDATE_TIME]
