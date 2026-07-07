"""Tests for `XploraDataUpdateCoordinator.get_location` fix-time handling (ADR 0007).

The shown position's age is the *watch's* fix time (`tm`, epoch seconds), not our poll time.
`get_location` turns the raw `device["tm"]` epoch into an ISO-8601 UTC string that entities/cards
render; an unknown fix time must stay unknown (never fabricated to `now()`), and pyxplora's
`31532399` missing-`tm` placeholder must be treated as unknown.
"""

from __future__ import annotations

import pytest

from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator


@pytest.mark.parametrize(
    ("epoch", "expected"),
    [
        (1700000000, "2023-11-14T22:13:20+00:00"),
        ("1700000000", "2023-11-14T22:13:20+00:00"),  # numeric strings are accepted (coerced)
        (None, None),
        (0, None),
        ("0", None),  # a stringified zero must NOT become 1970-01-01
        (31532399, None),  # pyxplora's missing-tm sentinel
        ("31532399", None),  # ...even stringified (the vendor's tm fields aren't reliably typed)
        (-1, None),  # a negative epoch is unknown, not a 1969 fix
        ("nonsense", None),
        # An out-of-range epoch must drop to None, never crash: this vendor's `tm` unit isn't
        # contractually seconds (cf. `_to_epoch_ms`), so a 13-digit ms value reaches fromtimestamp
        # and raises ValueError ("year out of range") -- which an uncaught except would let
        # propagate past the poll's terminal handler and fail the whole account's refresh (ADR 0007).
        (1700000000000, None),
        (float("inf"), None),  # int(inf) raises OverflowError -- also "unknown", not a crash
    ],
)
def test_fix_time_iso_normalizes_epoch(epoch, expected) -> None:
    assert XploraDataUpdateCoordinator._fix_time_iso(epoch) == expected


async def test_get_location_sets_iso_utc_from_raw_epoch(coordinator: XploraDataUpdateCoordinator) -> None:
    coordinator.device = {"tm": 1700000000, "lat": 52.52, "lng": 13.405}

    coordinator.get_location()

    # 1700000000 epoch seconds == 2023-11-14T22:13:20Z, emitted as an offset-aware ISO string.
    assert coordinator.last_track_time == "2023-11-14T22:13:20+00:00"


async def test_get_location_leaves_fix_time_unknown_when_tm_missing(coordinator: XploraDataUpdateCoordinator) -> None:
    """No `tm` -> None. The old code fabricated `datetime.now()`, which made a stale pin read
    'just now' — the exact lie ADR 0007 removes."""
    coordinator.device = {"lat": 52.52, "lng": 13.405}

    coordinator.get_location()

    assert coordinator.last_track_time is None


async def test_get_location_treats_missing_tm_sentinel_as_unknown(coordinator: XploraDataUpdateCoordinator) -> None:
    """`31532399` is pyxplora's placeholder for an absent fix time; it must not surface as a 1971 date."""
    coordinator.device = {"tm": 31532399, "lat": 52.52, "lng": 13.405}

    coordinator.get_location()

    assert coordinator.last_track_time is None
