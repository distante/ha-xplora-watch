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
    `if distance_to_home` gate on `address` below would then suppress it. (`last tracking` is
    no longer gated on distance -- ADR 0007 -- so it is unaffected either way.)
    """
    coordinator_with_data.data[DEFAULT_WUID]["lat"] = 52.53
    coordinator_with_data.data[DEFAULT_WUID]["lng"] = 13.41
    tracker = _make_tracker(hass, mock_config_entry_phone, coordinator_with_data)

    attrs = tracker.extra_state_attributes

    assert attrs["Home Distance (m)"] is not None
    assert attrs["address"] == "Teststrasse 1, 12345 Berlin, Germany"
    # `last tracking` is the watch's fix time as an ISO-8601 UTC string (ADR 0007), not a local
    # "%Y-%m-%d %H:%M:%S" string -- so cards can compute a relative age unambiguously.
    assert attrs["last tracking"] == "2023-11-14T22:13:20+00:00"
    assert attrs["imei"] == "imei-0001"


async def test_last_tracking_is_ungated_from_distance_but_address_stays_gated(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """`last tracking` reflects the fix time regardless of whether a distance-to-home can be
    computed (ADR 0007) -- clearing lat/lng skips the distance calc, yet the fix time still shows.
    `address` remains gated on distance (deliberately out of ADR 0007's scope)."""
    coordinator_with_data.data[DEFAULT_WUID]["lat"] = None
    coordinator_with_data.data[DEFAULT_WUID]["lng"] = None
    tracker = _make_tracker(hass, mock_config_entry_phone, coordinator_with_data)

    attrs = tracker.extra_state_attributes

    assert attrs["Home Distance (m)"] is None
    assert attrs["address"] is None
    assert attrs["last tracking"] == "2023-11-14T22:13:20+00:00"


async def test_last_tracking_present_at_home_even_when_distance_is_zero(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
    mock_home_zone,
) -> None:
    """Regression (ADR 0007): the fixture watch sits at the home coords, so distance-to-home is
    exactly 0.0 -- which is falsy. The old `if distance_to_home` gate blanked `last tracking` while
    the kid was *at home*, the one place it should still say how old the fix is."""
    tracker = _make_tracker(hass, mock_config_entry_phone, coordinator_with_data)

    attrs = tracker.extra_state_attributes

    assert attrs["Home Distance (m)"] == 0
    assert attrs["last tracking"] == "2023-11-14T22:13:20+00:00"


async def test_last_tracking_is_none_when_fix_time_unknown(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
    mock_home_zone,
) -> None:
    """Unknown fix time stays unknown end-to-end: with no `tm`, `get_location` emits None (never a
    fabricated now()), and that None reaches the tracker attribute. Driving `get_location` -- rather
    than poking the resolved data dict -- is what gives this teeth against the removed fabrication."""
    coordinator_with_data.device["tm"] = None
    coordinator_with_data.get_location()
    # Route the None through the REAL resolver: `get_data` is what wires `self.last_track_time` into
    # the per-watch dict the tracker reads. Re-running it (instead of hand-copying the key) gives the
    # attribute assertion below teeth against `get_data` ever sourcing `lastTrackTime` from elsewhere.
    coordinator_with_data.data[DEFAULT_WUID] = coordinator_with_data.get_data(DEFAULT_WUID, {})[DEFAULT_WUID]
    tracker = _make_tracker(hass, mock_config_entry_phone, coordinator_with_data)

    assert coordinator_with_data.last_track_time is None
    assert tracker.extra_state_attributes["last tracking"] is None
