"""Tests for XploraDataUpdateCoordinator.data_loop()."""

from __future__ import annotations

from custom_components.xplora_watch.const import ATTR_LAST_UPDATE_STATUS, LAST_UPDATE_NO_RESPONSE, LAST_UPDATE_OK
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import (
    DEFAULT_WUID,
    make_ask_watch_locate_payload,
    make_device_list_payload,
    make_watch_last_locate_payload,
)


async def test_data_loop_happy_path(coordinator: XploraDataUpdateCoordinator) -> None:
    """Default fixtures: battery/charging/online status all come from the single deviceList call."""
    await coordinator.controller.setDevices([DEFAULT_WUID])
    data = await coordinator.data_loop([DEFAULT_WUID], message_limit=10, remove_message=False)

    entry = data[DEFAULT_WUID]
    assert entry["battery"] == 80
    assert entry["isCharging"] is False
    assert entry["isOnline"] is True
    assert entry["isSafezone"] is True


async def test_data_loop_overlays_fresh_fix_over_stale_device_list(coordinator: XploraDataUpdateCoordinator, graphql_operations) -> None:
    """A refresh forces a fresh fix (askWatchLocate + WatchLastLocate) and overlays it.

    Regression for the "xplora_watch.see always returns old data" bug: deviceList only carries
    the watch's *last stored* position/battery, so the loop must overlay the fresh WatchLastLocate
    fix. Here deviceList is stale (battery 80 @ Berlin, old tm) while a fresh fix reports battery
    25 at a new position with an advanced tm -- the fresh values must win.
    """
    graphql_operations["deviceList"] = {"data": make_device_list_payload(battery=80, lat="52.5200", lng="13.4050")}
    graphql_operations["WatchLastLocate"] = {
        "data": make_watch_last_locate_payload(battery=25, lat="48.1371", lng="11.5754", is_charging=True)
    }
    # Advance the fresh fix's tm past deviceList's (1700000000) so the poll detects a new fix.
    graphql_operations["WatchLastLocate"]["data"]["watchLastLocate"]["tm"] = 1700000600

    await coordinator.controller.setDevices([DEFAULT_WUID])
    data = await coordinator.data_loop([DEFAULT_WUID], message_limit=10, remove_message=False)

    entry = data[DEFAULT_WUID]
    assert entry["battery"] == 25  # fresh fix wins over the stale deviceList battery (80)
    assert entry["isCharging"] is True
    assert entry["lat"] == 48.1371  # get_location() coerces the fresh fix's lat/lng to float
    assert entry["lng"] == 11.5754


async def test_data_loop_inverts_is_in_safe_zone(coordinator: XploraDataUpdateCoordinator, graphql_operations) -> None:
    """isInSafeZone=True on the location flips coordinator.is_safezone to False.

    The refresh overlays a fresh `WatchLastLocate` fix onto the `deviceList` status (see
    `_refresh_watch_fix`), so `isInSafeZone` is now sourced from the fresh fix -- set it on both
    so the safezone flag is unambiguous regardless of which one wins.
    """
    graphql_operations["deviceList"] = {"data": make_device_list_payload(is_in_safe_zone=True)}
    graphql_operations["WatchLastLocate"] = {"data": make_watch_last_locate_payload(is_in_safe_zone=True)}

    await coordinator.controller.setDevices([DEFAULT_WUID])
    data = await coordinator.data_loop([DEFAULT_WUID], message_limit=10, remove_message=False)

    assert data[DEFAULT_WUID]["isSafezone"] is False


async def test_data_loop_carries_watch_reported_safezone_label(coordinator: XploraDataUpdateCoordinator) -> None:
    """The watch-reported `safeZoneLabel` (already in every location payload) lands in the data.

    The `current_safezone` sensor reads it; no extra API call is involved -- both `deviceList`
    and `WatchLastLocate` carry the field (the default fixtures report "Home").
    """
    await coordinator.controller.setDevices([DEFAULT_WUID])
    data = await coordinator.data_loop([DEFAULT_WUID], message_limit=10, remove_message=False)

    assert data[DEFAULT_WUID]["safeZoneLabel"] == "Home"


async def test_data_loop_overlays_fresh_fix_safezone_label(coordinator: XploraDataUpdateCoordinator, graphql_operations) -> None:
    """A fresh `WatchLastLocate` fix's `safeZoneLabel` wins over the stale `deviceList` one.

    Mirrors the battery/position overlay: deviceList reports the *last stored* state ("Home"),
    while the forced fresh fix says the watch moved into the "School" zone -- the data must
    carry "School".
    """
    fresh = make_watch_last_locate_payload()
    fresh["watchLastLocate"]["safeZoneLabel"] = "School"
    # Advance the fresh fix's tm past deviceList's (1700000000) so the poll detects a new fix.
    fresh["watchLastLocate"]["tm"] = 1700000600
    graphql_operations["WatchLastLocate"] = {"data": fresh}

    await coordinator.controller.setDevices([DEFAULT_WUID])
    data = await coordinator.data_loop([DEFAULT_WUID], message_limit=10, remove_message=False)

    assert data[DEFAULT_WUID]["safeZoneLabel"] == "School"


async def test_data_loop_offline_when_device_list_reports_offline(coordinator: XploraDataUpdateCoordinator, graphql_operations) -> None:
    """deviceList's onlineStatus="OFFLINE" makes the watch offline."""
    graphql_operations["deviceList"] = {"data": make_device_list_payload(online_status="OFFLINE")}

    await coordinator.controller.setDevices([DEFAULT_WUID])
    data = await coordinator.data_loop([DEFAULT_WUID], message_limit=10, remove_message=False)

    assert data[DEFAULT_WUID]["isOnline"] is False


async def test_data_loop_non_admin_no_response_is_still_ok(coordinator: XploraDataUpdateCoordinator, graphql_operations) -> None:
    """A secondary guardian records `ok` even when the watch doesn't accept the locate request.

    A `guardianType != "FIRST"` account is a contact, not a location-tracking guardian: the server
    returns it no fresh fix (the deviceList carries `location: null` for it), so a no-response from
    `askWatchLocate` is the expected steady state, not a failure. The completed refresh must record
    `ok` -- otherwise these accounts show the "watch didn't respond" warning on every single poll
    while their chat/online/unread-count data is perfectly correct.
    """
    graphql_operations["askWatchLocate"] = {"data": make_ask_watch_locate_payload(success=False)}

    await coordinator.controller.setDevices([DEFAULT_WUID])
    coordinator.is_admin[DEFAULT_WUID] = False  # secondary guardian / contact
    data = await coordinator.data_loop([DEFAULT_WUID], message_limit=10, remove_message=False)

    assert data[DEFAULT_WUID][ATTR_LAST_UPDATE_STATUS] == LAST_UPDATE_OK


async def test_data_loop_admin_no_response_records_no_response(coordinator: XploraDataUpdateCoordinator, graphql_operations) -> None:
    """A primary guardian still records `no_response` when the watch doesn't accept the locate.

    Preserves the warning that matters for location tracking: for `guardianType == "FIRST"` the
    `askWatchLocate` verdict is the real "could not update the location" signal.
    """
    graphql_operations["askWatchLocate"] = {"data": make_ask_watch_locate_payload(success=False)}

    await coordinator.controller.setDevices([DEFAULT_WUID])
    coordinator.is_admin[DEFAULT_WUID] = True  # primary guardian
    data = await coordinator.data_loop([DEFAULT_WUID], message_limit=10, remove_message=False)

    assert data[DEFAULT_WUID][ATTR_LAST_UPDATE_STATUS] == LAST_UPDATE_NO_RESPONSE
