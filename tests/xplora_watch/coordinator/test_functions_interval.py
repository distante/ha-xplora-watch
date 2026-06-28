"""Tests for the separate "functions" (alarm/silent/safezone) poll interval.

These fetches cannot be folded into the account-wide `deviceList` call (the backend needs a
per-watch `uid` query for each), but they rarely change -- so they have their own interval that
defaults to OFF and is refreshed on demand. Each test drives the real vendored `pyxplora_api`
client against a mocked transport and counts the `Alarms` / `SafeZones` / `SlientTimes`
operations to assert the gate behavior. Mirrors the approach in `test_rate_limit_fixes.py`.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from aioresponses import CallbackResult, aioresponses
from homeassistant.const import (
    CONF_COUNTRY_CODE,
    CONF_LANGUAGE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import (
    ATTR_WATCH,
    BINARY_SENSOR_SAFEZONE,
    CONF_HOME_SAFEZONE,
    CONF_MAPS,
    CONF_MESSAGE,
    CONF_PHONENUMBER,
    CONF_REMOVE_MESSAGE,
    CONF_SCAN_INTERVAL_FUNCTIONS,
    CONF_TIMEZONE,
    CONF_USERLANG,
    CONF_WATCHES,
    DOMAIN,
    MAPS,
    SCAN_INTERVAL_FUNCTIONS_WITH_POLL,
    SCAN_INTERVAL_OFF,
    SENSOR_ALARMS,
    SENSOR_MESSAGE,
    SENSOR_SILENTS,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.const import ENDPOINT, FUNCTIONS_OPERATIONS, GqlOperation

from ..conftest import _make_graphql_callback
from ..fixtures.graphql_payloads import DEFAULT_OPERATION_PAYLOADS, DEFAULT_WUID
from ..fixtures.rest_payloads import OPENSTREETMAP_REVERSE_GEOCODE

# The three per-watch "functions" operations whose fetch is gated by the functions-poll interval.
FUNCTION_OPS = FUNCTIONS_OPERATIONS
SIX_HOURS = 6 * 60 * 60


def _operations() -> dict[str, dict[str, Any]]:
    return {name: {"data": payload} for name, payload in DEFAULT_OPERATION_PAYLOADS.items()}


def _entry(hass: HomeAssistant, functions_interval: int) -> MockConfigEntry:
    """A phone config entry with the functions poll interval set."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Xplora®",
        unique_id="+491700000001",
        data={
            CONF_COUNTRY_CODE: "+49",
            CONF_PHONENUMBER: "+491700000001",
            CONF_PASSWORD: "secret",
            CONF_USERLANG: "en-GB",
            CONF_TIMEZONE: "Europe/Berlin",
            CONF_LANGUAGE: "en",
        },
        options={
            CONF_WATCHES: [DEFAULT_WUID],
            CONF_MAPS: MAPS[0],
            CONF_SCAN_INTERVAL: 0,
            CONF_SCAN_INTERVAL_FUNCTIONS: functions_interval,
            CONF_MESSAGE: 10,
            CONF_REMOVE_MESSAGE: False,
            CONF_HOME_SAFEZONE: "off",
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _counting_coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, counts: dict[str, int]
) -> tuple[XploraDataUpdateCoordinator, aioresponses]:
    """Build a coordinator whose transport tallies each functions operation into `counts`."""
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        op = (kwargs.get("json") or {}).get("operationName")
        if op in counts:
            counts[op] += 1
        return default_callback(url, **kwargs)

    mocked = aioresponses()
    mocked.start()
    mocked.post(ENDPOINT, callback=_callback, repeat=True)
    mocked.get(re.compile(r"https://nominatim\.openstreetmap\.org/.*"), payload=OPENSTREETMAP_REVERSE_GEOCODE, repeat=True)
    coord = XploraDataUpdateCoordinator(hass, entry)
    await coord.init(session=aiohttp_client.async_get_clientsession(hass))
    return coord, mocked


async def test_functions_off_fetches_once_then_carries_forward(hass: HomeAssistant) -> None:
    """OFF (default): the first poll seeds the functions data, later polls reuse it (0 extra ops)."""
    counts = {op: 0 for op in FUNCTION_OPS}
    coord, mocked = await _counting_coordinator(hass, _entry(hass, SCAN_INTERVAL_OFF), counts)
    try:
        for _ in range(3):
            data = await coord.async_update_xplora_data()
    finally:
        mocked.stop()

    # Seeded exactly once on the first poll; polls 2 and 3 carry the values forward.
    assert all(counts[op] == 1 for op in FUNCTION_OPS), counts
    # Carry-forward keeps the alarm list populated across the later polls.
    assert data[DEFAULT_WUID]["alarm"], "carried-forward alarm data should remain present"


async def test_functions_with_poll_fetches_every_poll(hass: HomeAssistant) -> None:
    """WITH_POLL sentinel: functions are fetched on every main poll."""
    counts = {op: 0 for op in FUNCTION_OPS}
    coord, mocked = await _counting_coordinator(hass, _entry(hass, SCAN_INTERVAL_FUNCTIONS_WITH_POLL), counts)
    try:
        for _ in range(3):
            await coord.async_update_xplora_data()
    finally:
        mocked.stop()

    assert all(counts[op] == 3 for op in FUNCTION_OPS), counts


async def test_functions_interval_skips_until_due(hass: HomeAssistant) -> None:
    """A positive interval fetches, then skips back-to-back polls, then fetches once past due."""
    counts = {op: 0 for op in FUNCTION_OPS}
    coord, mocked = await _counting_coordinator(hass, _entry(hass, SIX_HOURS), counts)
    try:
        await coord.async_update_xplora_data()  # fetch (first time)
        await coord.async_update_xplora_data()  # not due yet -> skip
        assert all(counts[op] == 1 for op in FUNCTION_OPS), counts

        # Simulate >6h elapsed since the last functions fetch.
        coord._last_functions_fetch = {wuid: datetime.now() - timedelta(hours=7) for wuid in coord._last_functions_fetch}
        await coord.async_update_xplora_data()  # now due -> fetch again
    finally:
        mocked.stop()

    assert all(counts[op] == 2 for op in FUNCTION_OPS), counts


async def test_functions_fetch_timestamps_persist_across_restart(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    """A restart (fresh coordinator, same entry) restores the timestamps and skips the seed fetch.

    Without persistence the in-memory `_last_functions_fetch` starts empty on every HA start, so the
    one-time seed re-fired each restart -- an Alarms/SafeZones/SlientTimes call per watch even with
    the functions interval OFF. The `hass_storage` fixture keeps the `.storage` blob alive across the
    two coordinator builds, simulating the restart.
    """
    counts = {op: 0 for op in FUNCTION_OPS}
    entry = _entry(hass, SCAN_INTERVAL_OFF)
    coord, mocked = await _counting_coordinator(hass, entry, counts)
    try:
        await coord.async_update_xplora_data()  # first start: seeds once + persists the timestamps
        assert all(counts[op] == 1 for op in FUNCTION_OPS), counts

        # Simulate an HA restart: a brand-new coordinator for the same entry must restore the
        # persisted timestamps from `.storage` (so `first_time` is False) and -- with the interval
        # OFF -- issue NO further functions calls.
        coord2 = XploraDataUpdateCoordinator(hass, entry)
        await coord2.init(session=aiohttp_client.async_get_clientsession(hass))
        assert coord2._last_functions_fetch, "functions-fetch timestamps should be restored from storage"
        await coord2.async_update_xplora_data()
    finally:
        mocked.stop()

    assert all(counts[op] == 1 for op in FUNCTION_OPS), counts


async def test_refresh_functions_forces_fetch_when_off(hass: HomeAssistant) -> None:
    """`async_refresh_functions` fetches the functions data even when the interval is OFF."""
    counts = {op: 0 for op in FUNCTION_OPS}
    coord, mocked = await _counting_coordinator(hass, _entry(hass, SCAN_INTERVAL_OFF), counts)
    try:
        await coord.async_update_xplora_data()  # seeds once
        assert all(counts[op] == 1 for op in FUNCTION_OPS), counts
        await coord.async_refresh_functions([DEFAULT_WUID])  # forced refresh
    finally:
        mocked.stop()

    assert all(counts[op] == 2 for op in FUNCTION_OPS), counts


def _register_entity(hass: HomeAssistant, entry: MockConfigEntry, domain: Platform, unique_id: str, *, disabled: bool) -> None:
    """Register an entity for `entry` so the coordinator's enabled-consumer gate can see it."""
    er.async_get(hass).async_get_or_create(
        domain,
        DOMAIN,
        unique_id,
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
    )


def _alarms_uid() -> str:
    return f"kid_{ATTR_WATCH}_{SENSOR_ALARMS}_{DEFAULT_WUID}_uid"


def _silents_uid() -> str:
    return f"kid_{ATTR_WATCH}_{SENSOR_SILENTS}_{DEFAULT_WUID}_uid"


def _safezone_uid() -> str:
    return f"kid_{ATTR_WATCH}_{BINARY_SENSOR_SAFEZONE}_{DEFAULT_WUID}_uid"


async def test_all_functions_consumers_disabled_skips_all_fetches(hass: HomeAssistant) -> None:
    """When every functions-consuming entity is disabled, the poll fetches no functions data --
    even on the first poll (nothing is displaying any of it)."""
    entry = _entry(hass, SCAN_INTERVAL_FUNCTIONS_WITH_POLL)  # interval would otherwise fetch every poll
    _register_entity(hass, entry, Platform.SENSOR, _alarms_uid(), disabled=True)
    _register_entity(hass, entry, Platform.SENSOR, _silents_uid(), disabled=True)
    _register_entity(hass, entry, Platform.DEVICE_TRACKER, _safezone_uid(), disabled=True)

    counts = {op: 0 for op in FUNCTION_OPS}
    coord, mocked = await _counting_coordinator(hass, entry, counts)
    try:
        await coord.async_update_xplora_data()
    finally:
        mocked.stop()

    assert all(counts[op] == 0 for op in FUNCTION_OPS), counts


async def test_enabling_only_alarms_fetches_only_alarms(hass: HomeAssistant) -> None:
    """The granular gate: enabling just the alarms sensor (others disabled) fetches ONLY `Alarms` --
    not `SafeZones`/`SlientTimes`. This is the user-reported case: one enabled consumer must not
    drag the whole functions group along."""
    entry = _entry(hass, SCAN_INTERVAL_OFF)
    _register_entity(hass, entry, Platform.SENSOR, _alarms_uid(), disabled=False)
    _register_entity(hass, entry, Platform.SENSOR, _silents_uid(), disabled=True)
    _register_entity(hass, entry, Platform.DEVICE_TRACKER, _safezone_uid(), disabled=True)

    counts = {op: 0 for op in FUNCTION_OPS}
    coord, mocked = await _counting_coordinator(hass, entry, counts)
    try:
        await coord.async_update_xplora_data()
    finally:
        mocked.stop()

    assert counts[GqlOperation.ALARMS] == 1, counts
    assert counts[GqlOperation.SAFE_ZONES] == 0, counts
    assert counts[GqlOperation.SILENT_TIMES] == 0, counts


async def test_disabling_only_alarms_suppresses_only_alarms(hass: HomeAssistant) -> None:
    """Mirror of the above: disabling just the alarms sensor (others enabled) suppresses ONLY the
    `Alarms` request; `SafeZones`/`SlientTimes` still fetch."""
    entry = _entry(hass, SCAN_INTERVAL_FUNCTIONS_WITH_POLL)
    _register_entity(hass, entry, Platform.SENSOR, _alarms_uid(), disabled=True)
    _register_entity(hass, entry, Platform.SENSOR, _silents_uid(), disabled=False)
    _register_entity(hass, entry, Platform.DEVICE_TRACKER, _safezone_uid(), disabled=False)

    counts = {op: 0 for op in FUNCTION_OPS}
    coord, mocked = await _counting_coordinator(hass, entry, counts)
    try:
        await coord.async_update_xplora_data()
    finally:
        mocked.stop()

    assert counts[GqlOperation.ALARMS] == 0, counts
    assert counts[GqlOperation.SAFE_ZONES] == 1, counts
    assert counts[GqlOperation.SILENT_TIMES] == 1, counts


async def test_unregistered_consumers_still_seed_once(hass: HomeAssistant) -> None:
    """Before the platforms create their entities (brand-new install), no consumer is registered,
    so the gate can't tell -- it must still seed all functions once rather than skip forever."""
    entry = _entry(hass, SCAN_INTERVAL_OFF)  # no entities registered at all

    counts = {op: 0 for op in FUNCTION_OPS}
    coord, mocked = await _counting_coordinator(hass, entry, counts)
    try:
        await coord.async_update_xplora_data()
    finally:
        mocked.stop()

    assert all(counts[op] == 1 for op in FUNCTION_OPS), counts


async def test_poll_never_fetches_chats(hass: HomeAssistant) -> None:
    """The `see` / periodic-poll path never issues the `Chats` request -- even with the Message
    sensor ENABLED. Chats are owned exclusively by the standalone `read_message` service now, so the
    location/status refresh stays decoupled from them."""
    entry = _entry(hass, SCAN_INTERVAL_OFF)
    _register_entity(hass, entry, Platform.SENSOR, f"kid_{ATTR_WATCH}_{SENSOR_MESSAGE}_{DEFAULT_WUID}_uid", disabled=False)

    chats = {GqlOperation.CHATS: 0}
    coord, mocked = await _counting_coordinator(hass, entry, chats)
    try:
        await coord.async_update_xplora_data()
    finally:
        mocked.stop()

    assert chats[GqlOperation.CHATS] == 0
