"""Tests for XploraDataUpdateCoordinator.data_loop()."""

from __future__ import annotations

from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID, make_device_list_payload, make_watch_last_locate_payload


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


async def test_data_loop_offline_when_device_list_reports_offline(coordinator: XploraDataUpdateCoordinator, graphql_operations) -> None:
    """deviceList's onlineStatus="OFFLINE" makes the watch offline."""
    graphql_operations["deviceList"] = {"data": make_device_list_payload(online_status="OFFLINE")}

    await coordinator.controller.setDevices([DEFAULT_WUID])
    data = await coordinator.data_loop([DEFAULT_WUID], message_limit=10, remove_message=False)

    assert data[DEFAULT_WUID]["isOnline"] is False
