"""Tests for XploraDeviceTracker's properties (device_tracker.py)."""

from __future__ import annotations

from homeassistant.const import ATTR_BATTERY_LEVEL
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.device_tracker import XploraDeviceTracker
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID

WARD = {"id": DEFAULT_WUID, "name": "Kid One"}


def _make_tracker(hass: HomeAssistant, config_entry: MockConfigEntry, coordinator: XploraDataUpdateCoordinator) -> XploraDeviceTracker:
    return XploraDeviceTracker(hass, config_entry, coordinator, DEFAULT_WUID, WARD, "https://example.com/icon.png")


async def test_tracker_exposes_position_but_no_deprecated_battery_or_address(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    tracker = _make_tracker(hass, mock_config_entry_phone, coordinator_with_data)

    assert tracker.latitude == 52.52
    assert tracker.longitude == 13.405
    assert tracker.location_accuracy == 50
    # The deprecated `battery_level` TrackerEntity override is gone (HA 2027.7 removal); the
    # enabled-by-default battery sensor is the replacement, so the tracker state no longer
    # carries a battery_level attribute.
    assert ATTR_BATTERY_LEVEL not in tracker.state_attributes
    # `address` was dead code -- TrackerEntity never read the property. The resolved address
    # still flows via the `address` extra state attribute (see the attribute tests below).
    assert not hasattr(tracker, "address")


async def test_extra_state_attributes_includes_distance_to_home_when_lat_lng_present(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
    mock_home_zone,
) -> None:
    """Use a watch location distinct from the mocked zone.home coords (both default to the
    same 52.5200/13.4050 point), since a distance of exactly 0 is falsy and the real
    `if distance_to_home else None` checks below would then suppress address/last-track too.
    """
    coordinator_with_data.data[DEFAULT_WUID]["lat"] = 52.53
    coordinator_with_data.data[DEFAULT_WUID]["lng"] = 13.41
    tracker = _make_tracker(hass, mock_config_entry_phone, coordinator_with_data)

    attrs = tracker.extra_state_attributes

    assert attrs["Home Distance (m)"] is not None
    assert attrs["address"] == "Teststrasse 1, 12345 Berlin, Germany"
    assert attrs["last tracking"] is not None
    assert attrs["imei"] == "imei-0001"


async def test_extra_state_attributes_omits_address_and_last_track_when_no_distance(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """Without a zone.home state, get_location_distance_meter raises, so distance_to_home stays
    None unless lat/lng are also missing -- here we clear lat/lng so the distance calc is skipped
    entirely (the no-zone.home + lat/lng-present combination would raise instead, which is a
    separate, real failure mode not exercised here).
    """
    coordinator_with_data.data[DEFAULT_WUID]["lat"] = None
    coordinator_with_data.data[DEFAULT_WUID]["lng"] = None
    tracker = _make_tracker(hass, mock_config_entry_phone, coordinator_with_data)

    attrs = tracker.extra_state_attributes

    assert attrs["Home Distance (m)"] is None
    assert attrs["address"] is None
    assert attrs["last tracking"] is None
