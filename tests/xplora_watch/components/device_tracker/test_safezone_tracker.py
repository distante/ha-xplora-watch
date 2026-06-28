"""Tests for XploraSafezoneTracker's properties (device_tracker.py)."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.device_tracker import XploraSafezoneTracker
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID

WARD = {"id": DEFAULT_WUID, "name": "Kid One"}


async def _get_real_safezone(coordinator: XploraDataUpdateCoordinator) -> dict:
    return (await coordinator.controller.getWatchSafeZones(DEFAULT_WUID))[0]


async def test_safezone_tracker_properties(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    safezone = await _get_real_safezone(coordinator_with_data)
    tracker = XploraSafezoneTracker(mock_config_entry_phone, safezone, coordinator_with_data, DEFAULT_WUID, WARD)

    assert tracker.latitude == 52.5200
    assert tracker.longitude == 13.4050
    assert tracker.source_type == SourceType.GPS
    assert tracker.location_accuracy == 100
    assert tracker.location_name == "Home"
    assert tracker.extra_state_attributes["address"] == "Teststrasse 1, Berlin"
