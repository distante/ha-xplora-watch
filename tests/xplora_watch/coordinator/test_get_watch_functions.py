"""Tests for XploraDataUpdateCoordinator.get_watch_functions()."""

from __future__ import annotations

from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


async def test_get_watch_functions_sets_fields_from_device(coordinator: XploraDataUpdateCoordinator) -> None:
    """All watch-derived attributes are populated from controller.getWatchAlarm() and the device dict."""
    await coordinator.controller.setDevices([DEFAULT_WUID])
    device = coordinator.controller.getDevice(wuid=DEFAULT_WUID)

    await coordinator.get_watch_functions(DEFAULT_WUID, device)

    assert coordinator.alarm == [
        {
            "id": "alarm-1",
            "vendorId": "vendor-alarm-1",
            "name": "Wake up",
            "start": "07:00",
            "weekRepeat": "1111100",
            "status": "ENABLE",
        }
    ]
    assert coordinator.silent == device.get("getSilentTime", [])
    assert coordinator.imei == "imei-0001"
    assert coordinator.watch_id == DEFAULT_WUID
    assert coordinator.os_version == "1.2.3"
    assert coordinator.model == "GPS-Watch"
    assert coordinator.entity_picture == "https://api.myxplora.com/file?id=file-1"
    assert coordinator._step_day == 1234
    assert coordinator._xcoin == 10


async def test_get_watch_functions_imei_defaults_to_wuid_when_missing(
    coordinator: XploraDataUpdateCoordinator,
) -> None:
    """imei falls back to the wuid itself when the device's getWatches sub-dict has no 'imei' key."""
    device = {
        "getWatchAlarm": [],
        "getSilentTime": [],
        "getWatches": {"osVersion": "1.2.3", "model": "GPS-Watch"},
        "getWatchUserIcons": "",
        "getWatchUserSteps": {"day": 0},
        "getWatchUserXCoins": 0,
    }

    await coordinator.get_watch_functions(DEFAULT_WUID, device)

    assert coordinator.imei == DEFAULT_WUID
