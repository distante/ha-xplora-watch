"""Tests for the per-day location-history cache (fetch/store/prune/persist + gating).

The history is fetched by a standalone `LocHistory` request (NOT part of the `setDevices` functions
bundle), gated so it is issued ONLY when the opt-in location-history sensor is registered and
enabled. TODAY is always fetched fresh; PAST days are immutable and cached per day (network only on
first view). Buckets are pruned to the configured retention, persisted to `.storage`, and exposed
as a bounded slice on the sensor plus per-day via `async_fetch_history_day` (the websocket backend).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import (
    ATTR_HISTORY_TM,
    ATTR_LOCATION_HISTORY,
    ATTR_WATCH,
    LOC_HISTORY_ATTR_MAX_POINTS,
    SENSOR_LOCATION_HISTORY,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator

from ..fixtures.graphql_payloads import DEFAULT_WUID, make_loc_history_payload

HISTORY_UNIQUE_ID = f"kid_one_{ATTR_WATCH}_{SENSOR_LOCATION_HISTORY}_{DEFAULT_WUID}_user"


def _register_history_entity(hass: HomeAssistant, entry: MockConfigEntry, *, disabled: bool) -> None:
    """Register the location-history sensor so the coordinator's enabled-consumer gate sees it."""
    er.async_get(hass).async_get_or_create(
        Platform.SENSOR,
        "xplora_watch",
        HISTORY_UNIQUE_ID,
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
    )


def _patch_loc_history(coordinator: XploraDataUpdateCoordinator, payload: dict[str, Any] | None = None) -> AsyncMock:
    """Replace the controller's `getWatchLocHistory` with a counting AsyncMock returning `payload`."""
    mock = AsyncMock(return_value=payload if payload is not None else make_loc_history_payload())
    coordinator.controller.getWatchLocHistory = mock  # type: ignore[method-assign]
    return mock


def _today(coordinator: XploraDataUpdateCoordinator) -> str:
    return coordinator._today_key(coordinator._history_tzinfo())


# ---- gating -------------------------------------------------------------------------------------


async def test_history_not_fetched_when_no_entity(coordinator: XploraDataUpdateCoordinator) -> None:
    """With no history sensor registered (the default), the `LocHistory` request is never issued."""
    mock = _patch_loc_history(coordinator)
    await coordinator.async_update_xplora_data()
    mock.assert_not_called()
    assert coordinator._loc_history == {}


async def test_history_not_fetched_when_disabled(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    """A registered-but-disabled history sensor still suppresses the fetch (ban-defense default)."""
    _register_history_entity(hass, coordinator._entry, disabled=True)
    mock = _patch_loc_history(coordinator)
    await coordinator.async_update_xplora_data()
    mock.assert_not_called()


async def test_only_today_fetched_on_force_refresh(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    """A force refresh fetches only TODAY (one request, date=None), stored under today's bucket."""
    _register_history_entity(hass, coordinator._entry, disabled=False)
    mock = _patch_loc_history(coordinator)
    data = await coordinator.async_refresh_functions([DEFAULT_WUID])

    mock.assert_called_once()
    assert mock.await_args.kwargs["date"] is None  # today keeps the proven date=None call
    buckets = coordinator._loc_history[DEFAULT_WUID]
    assert list(buckets.keys()) == [_today(coordinator)]
    assert len(buckets[_today(coordinator)]) == 2  # two points, tm normalized to ms
    assert all(p[ATTR_HISTORY_TM] > 1_000_000_000_000 for p in buckets[_today(coordinator)])
    assert data[DEFAULT_WUID][ATTR_LOCATION_HISTORY]["total"] == 2


async def test_history_force_refresh_fetches_when_enabled(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    """`async_refresh_functions` (force) fetches today for an enabled sensor even with interval OFF."""
    _register_history_entity(hass, coordinator._entry, disabled=False)
    mock = _patch_loc_history(coordinator)
    await coordinator.async_refresh_functions([DEFAULT_WUID])
    mock.assert_called()


async def test_history_not_fetched_on_see(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    """`xplora_watch.see` (a plain update, no force) must NOT load history, even when enabled.

    History is deliberately off the regular `see` / periodic-poll path -- it is fetched only on an
    explicit force refresh (the card triggers one when the history view is opened) or via the
    `fetch_history` service. A normal location update never drags the `LocHistory` request along.
    """
    _register_history_entity(hass, coordinator._entry, disabled=False)
    mock = _patch_loc_history(coordinator)
    await coordinator.async_update_xplora_data()  # like a `see`
    await coordinator.async_update_xplora_data()  # and again
    mock.assert_not_called()


# ---- caching rule (today fresh, past cached) ----------------------------------------------------


async def test_today_is_always_fetched_fresh(coordinator: XploraDataUpdateCoordinator) -> None:
    """Today is never served from cache -- each request hits the network."""
    mock = _patch_loc_history(coordinator)
    today = _today(coordinator)
    await coordinator.async_fetch_history_day(DEFAULT_WUID, today)
    await coordinator.async_fetch_history_day(DEFAULT_WUID, today)
    assert mock.call_count == 2


async def test_past_day_is_cached_after_first_fetch(coordinator: XploraDataUpdateCoordinator) -> None:
    """A past day hits the network once, then is served from the Store (no second request)."""
    mock = _patch_loc_history(coordinator)
    yesterday = (datetime.now(coordinator._history_tzinfo()) - timedelta(days=1)).strftime("%Y-%m-%d")
    first = await coordinator.async_fetch_history_day(DEFAULT_WUID, yesterday)
    assert mock.call_count == 1
    assert mock.await_args.kwargs["date"] is not None  # a past day passes an explicit epoch date
    second = await coordinator.async_fetch_history_day(DEFAULT_WUID, yesterday)
    assert mock.call_count == 1  # cache hit -> no extra network call
    assert first == second


async def test_empty_past_day_is_cached(coordinator: XploraDataUpdateCoordinator) -> None:
    """An empty past day is cached so it isn't re-requested."""
    mock = _patch_loc_history(coordinator, {"locHistory": {"list": []}})
    yesterday = (datetime.now(coordinator._history_tzinfo()) - timedelta(days=1)).strftime("%Y-%m-%d")
    assert await coordinator.async_fetch_history_day(DEFAULT_WUID, yesterday) == []
    assert await coordinator.async_fetch_history_day(DEFAULT_WUID, yesterday) == []
    assert mock.call_count == 1


# ---- normalization / parsing --------------------------------------------------------------------


def test_to_epoch_ms_handles_seconds_ms_and_garbage() -> None:
    """Seconds are scaled to ms, ms pass through, and unusable values become None."""
    assert XploraDataUpdateCoordinator._to_epoch_ms(1700000000) == 1700000000000
    assert XploraDataUpdateCoordinator._to_epoch_ms(1700000000000) == 1700000000000
    assert XploraDataUpdateCoordinator._to_epoch_ms(0) is None
    assert XploraDataUpdateCoordinator._to_epoch_ms("nope") is None
    assert XploraDataUpdateCoordinator._to_epoch_ms(None) is None


def test_parse_loc_history_drops_malformed_entries() -> None:
    """Only entries with a usable tm and coordinates survive parsing."""
    raw = {
        "locHistory": {
            "list": [
                {"tm": 1700000000, "lat": "1.0", "lng": "2.0", "addr": "A"},
                {"tm": 1700000001, "lat": None, "lng": "2.0"},  # no lat -> dropped
                {"lat": "1.0", "lng": "2.0"},  # no tm -> dropped
                "not-a-dict",  # dropped
            ]
        }
    }
    points = XploraDataUpdateCoordinator._parse_loc_history(raw)
    assert len(points) == 1
    assert points[0]["lat"] == 1.0 and points[0]["addr"] == "A"
    assert XploraDataUpdateCoordinator._parse_loc_history({}) == []


# ---- prune / bounded slice ----------------------------------------------------------------------


async def test_store_day_prunes_buckets_beyond_retention(coordinator: XploraDataUpdateCoordinator) -> None:
    """Day buckets older than `history_retention_days` are dropped on store."""
    coordinator._resolved = dataclasses.replace(coordinator._resolved, history_retention_days=2)
    tzinfo = coordinator._history_tzinfo()
    days = coordinator._loc_history.setdefault(DEFAULT_WUID, {})
    old = (datetime.now(tzinfo) - timedelta(days=10)).strftime("%Y-%m-%d")
    recent = (datetime.now(tzinfo) - timedelta(days=1)).strftime("%Y-%m-%d")
    days[old] = [{"tm": 1, "lat": 1.0, "lng": 2.0}]
    days[recent] = [{"tm": 2, "lat": 1.0, "lng": 2.0}]

    await coordinator._store_day(DEFAULT_WUID, _today(coordinator), [], tzinfo)

    kept = coordinator._loc_history[DEFAULT_WUID]
    assert old not in kept
    assert recent in kept


def test_bounded_history_caps_window_and_count(coordinator: XploraDataUpdateCoordinator) -> None:
    """The sensor slice is capped to the recent window and the max-point count; total stays full."""
    now_ms = int(datetime.now().timestamp() * 1000)
    old_bucket = [{"tm": now_ms - 48 * 60 * 60 * 1000, "lat": 1.0, "lng": 2.0}]  # outside 24h window
    recent_bucket = [{"tm": now_ms - i * 1000, "lat": 1.0, "lng": 2.0} for i in range(LOC_HISTORY_ATTR_MAX_POINTS + 10, 0, -1)]
    coordinator._loc_history[DEFAULT_WUID] = {"old": old_bucket, "recent": recent_bucket}

    bounded, total = coordinator._bounded_history(DEFAULT_WUID)
    assert total == len(old_bucket) + len(recent_bucket)
    assert len(bounded) <= LOC_HISTORY_ATTR_MAX_POINTS
    assert all(p["tm"] >= now_ms - 24 * 60 * 60 * 1000 for p in bounded)


# ---- selector helpers ---------------------------------------------------------------------------


def test_cached_history_days_returns_sorted_keys(coordinator: XploraDataUpdateCoordinator) -> None:
    """`cached_history_days` returns the watch's day-bucket keys, ascending."""
    coordinator._loc_history[DEFAULT_WUID] = {"2026-06-27": [], "2026-06-25": [], "2026-06-26": []}
    assert coordinator.cached_history_days(DEFAULT_WUID) == ["2026-06-25", "2026-06-26", "2026-06-27"]
    assert coordinator.cached_history_days("unknown") == []


def test_history_yesterday_key_is_one_day_before_today(coordinator: XploraDataUpdateCoordinator) -> None:
    """Yesterday's key is exactly one day before today's, both in the watch timezone."""
    tzinfo = coordinator._history_tzinfo()
    expected = (datetime.now(tzinfo) - timedelta(days=1)).strftime("%Y-%m-%d")
    assert coordinator.history_yesterday_key() == expected


# ---- persistence --------------------------------------------------------------------------------


async def test_history_persists_across_restart(
    hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, hass_storage: dict[str, Any]
) -> None:
    """A stored day persists to `.storage`; a fresh coordinator for the same entry restores it."""
    tzinfo = coordinator._history_tzinfo()
    today = _today(coordinator)
    await coordinator._store_day(DEFAULT_WUID, today, [{"tm": 1700000000000, "lat": 1.0, "lng": 2.0}], tzinfo)

    coord2 = XploraDataUpdateCoordinator(hass, coordinator._entry)
    await coord2.init(session=aiohttp_client.async_get_clientsession(hass))
    assert coord2._loc_history.get(DEFAULT_WUID, {}).get(today), "day bucket should be restored from storage"


async def test_restores_legacy_flat_list_shape(
    hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, hass_storage: dict[str, Any]
) -> None:
    """A legacy flat-list blob is migrated into per-day buckets on restore."""
    await coordinator._history_store.async_save({DEFAULT_WUID: [{"tm": 1700000000000, "lat": 1.0, "lng": 2.0}]})

    coord2 = XploraDataUpdateCoordinator(hass, coordinator._entry)
    await coord2.init(session=aiohttp_client.async_get_clientsession(hass))
    buckets = coord2._loc_history.get(DEFAULT_WUID, {})
    assert buckets, "legacy flat list should be regrouped into a day bucket"
    assert sum(len(b) for b in buckets.values()) == 1
