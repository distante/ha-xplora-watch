"""`_refresh_watch_fix` overlay must not advance the fix time without a position (ADR 0007).

`tm` is the timestamp OF a lat/lng fix, and it now drives the user-facing "captured N min ago" age.
A `WatchLastLocate` carrying a fresh `tm` but null lat/lng (a normal partial-nullability shape) must
NOT age-stamp the unchanged pin as fresh -- that would reintroduce the "just now over a stale pin"
lie via a tm/position desync instead of the poll/fix desync ADR 0007 removed.
"""

from __future__ import annotations

from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _fresh(tm, lat, lng):
    # Mirrors PyXploraApi.loadWatchLocation's shape: a top-level formatted string plus the raw
    # nested Location under `watch_last_location` (whose epoch `tm` is what reaches `device["tm"]`).
    return {"tm": "2023-11-14 22:13:20", "watch_last_location": {"tm": tm, "lat": lat, "lng": lng}}


async def _overlay(coordinator, monkeypatch, fresh):
    async def _not_reachable(_wuid):
        return False  # single read, no poll sleep; the overlay still runs regardless of reachability

    async def _load(_wuid, with_ask=False):
        return fresh

    monkeypatch.setattr(coordinator.controller, "askWatchLocate", _not_reachable)
    monkeypatch.setattr(coordinator.controller, "loadWatchLocation", _load)
    await coordinator._refresh_watch_fix(DEFAULT_WUID)


async def test_tm_does_not_advance_without_a_position(coordinator, monkeypatch) -> None:
    coordinator.device = {"tm": 1700000000, "lat": 52.5, "lng": 13.4}
    await _overlay(coordinator, monkeypatch, _fresh(1700003600, None, None))
    # No position in the fresh payload -> the pin is unchanged, so its fix age must be too.
    assert coordinator.device["tm"] == 1700000000
    assert coordinator.device["lat"] == 52.5


async def test_tm_advances_with_a_position(coordinator, monkeypatch) -> None:
    coordinator.device = {"tm": 1700000000, "lat": 52.5, "lng": 13.4}
    await _overlay(coordinator, monkeypatch, _fresh(1700003600, 52.53, 13.41))
    # A real new fix (position + tm together) advances both.
    assert coordinator.device["tm"] == 1700003600
    assert coordinator.device["lat"] == 52.53
