"""Tests for whether the vendored client's retry loops actually retry.

Prompted by a review question: speeding up the suite (conftest.py's `_no_real_sleep`)
removed the wall-clock cost of `pyxplora_api`'s retry/backoff delays, but nothing was
asserting that those retries -- or their `maxRetries`/`retryDelay` knobs -- behave
correctly in the first place. They used to behave *inconsistently*:

- `_login` (pyxplora_api_async.py) genuinely retries on a transient HTTP failure and
  recovers once it clears.
- The per-field data-fetch wrappers (`getWatchAlarm`, `getSilentTime`,
  `getWatchSafeZones`) did not: `runGqlQuery_a` -> `ha_execute_async` swallows
  `ClientResponseError`/`ContentTypeError` into `{}` *before* anything can raise the
  `Error` subclass their `except Error` retry loops were waiting for, and their own
  `if not raw_x: return <default>` guard returned on that empty dict immediately. One
  failed HTTP call became one silent permanent failure, not three retries.

Fixed: the three wrappers now treat an empty `{}` envelope (request failed) as a retry
trigger, while a present-but-empty payload (`{"alarms": []}`, genuinely no data) still
returns immediately without retrying. After retries are exhausted on a real failure they
return a `FetchError` (see `pyxplora_api_async.FetchError`) instead of silently degrading
to an indistinguishable empty list.

A second, separate concern: the Xplora API is very rate-limit-sensitive (see CLAUDE.md's
migration context -- this whole fork exists because of IP bans from a previous integration
hammering it). Retrying an HTTP 429 (Too Many Requests) the same way as a transient 500
would make that worse. `graphql_client.py` now raises `RateLimitError` (deliberately NOT a
subclass of the library's `Error` base) on a 429, so it bypasses every `except Error` retry
loop in this file -- and `_login`'s -- and aborts the call after exactly one attempt instead
of retrying.

These tests build their own standalone `aioresponses` context per test instead of using
the shared `mock_graphql`/`graphql_operations` fixtures, because the failure injected here
must be the *only* registered matcher for the GraphQL endpoint -- aioresponses uses
whichever `repeat=True` matcher was registered first when more than one targets the same
URL, so layering a second matcher on top of `mock_graphql`'s catch-all would never fire.
"""

from __future__ import annotations

from typing import Any

import pytest
from aioresponses import CallbackResult, aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.const import ENDPOINT
from custom_components.xplora_watch.pyxplora_api.exception_classes import RateLimitError
from custom_components.xplora_watch.pyxplora_api.pyxplora import PyXplora
from custom_components.xplora_watch.pyxplora_api.pyxplora_api_async import FetchError

from ..conftest import _make_graphql_callback
from ..fixtures.graphql_payloads import DEFAULT_OPERATION_PAYLOADS, DEFAULT_WUID


def _failing_then_default_callback(failing_operation: str, fail_times: int, call_count: dict[str, int]):
    """Fail `failing_operation` with an HTTP 500 the first `fail_times` calls, then fall
    back to the normal happy-path fixtures for every operation (including the failing one
    once its `fail_times` budget is spent).
    """
    operations = {name: {"data": payload} for name, payload in DEFAULT_OPERATION_PAYLOADS.items()}
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        if body.get("operationName") == failing_operation:
            call_count["n"] += 1
            if call_count["n"] <= fail_times:
                return CallbackResult(status=500, reason="Internal Server Error", payload={"errors": [{"message": "boom"}]})
        return default_callback(url, **kwargs)

    return _callback


def _rate_limited_callback(failing_operation: str, call_count: dict[str, int]):
    """Respond to every call of `failing_operation` with an HTTP 429, falling back to the
    normal happy-path fixtures for every other operation.
    """
    operations = {name: {"data": payload} for name, payload in DEFAULT_OPERATION_PAYLOADS.items()}
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        if body.get("operationName") == failing_operation:
            call_count["n"] += 1
            return CallbackResult(status=429, reason="Too Many Requests", payload={"errors": [{"message": "rate limited"}]})
        return default_callback(url, **kwargs)

    return _callback


def test_retry_constants_unchanged() -> None:
    """Pin the retry knobs the two tests below assume, so an accidental change shows up
    here instead of as a confusing failure in a timing-sensitive test.
    """
    assert PyXplora.maxRetries == 3
    assert PyXplora.retryDelay == 2


async def test_login_retries_on_transient_server_error_then_succeeds(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """A 500 response is swallowed to `{}` by `ha_execute_async`, which makes
    `signInWithEmailOrPhone` missing from the response, which makes `login_a` raise
    `LoginError`. `_login`'s retry loop explicitly catches `LoginError` and retries, so
    two transient failures followed by a real success still results in a working login.
    """
    call_count = {"n": 0}
    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_failing_then_default_callback("signInWithEmailOrPhone", 2, call_count), repeat=True)

        coordinator = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
        session = aiohttp_client.async_get_clientsession(hass)
        await coordinator.init(session=session)

    assert call_count["n"] == 3
    assert coordinator.username


async def test_get_watch_alarm_retries_and_returns_fetch_error_on_persistent_server_error(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry
) -> None:
    """Same kind of transient HTTP failure as the login test above, but on the `Alarms`
    operation: `getWatchAlarm`'s retry loop now treats the swallowed-to-`{}` envelope as a
    failure worth retrying, so it runs the full `maxRetries + 2` (5) attempts before giving
    up and returning a `FetchError` instead of silently degrading to `[]`.
    """
    call_count = {"n": 0}
    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_failing_then_default_callback("Alarms", 100, call_count), repeat=True)

        coordinator = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
        session = aiohttp_client.async_get_clientsession(hass)
        await coordinator.init(session=session)

        result = await coordinator.controller.getWatchAlarm(DEFAULT_WUID)

    assert isinstance(result, FetchError)
    assert call_count["n"] == 5


async def test_get_watch_alarm_retries_on_transient_server_error_then_succeeds(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry
) -> None:
    """Mirrors test_login_retries_on_transient_server_error_then_succeeds above: two transient
    failures on the `Alarms` operation followed by a real success still results in the parsed
    alarm list, because the retry loop now keeps retrying on the swallowed-to-`{}` envelope.
    """
    call_count = {"n": 0}
    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_failing_then_default_callback("Alarms", 2, call_count), repeat=True)

        coordinator = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
        session = aiohttp_client.async_get_clientsession(hass)
        await coordinator.init(session=session)

        result = await coordinator.controller.getWatchAlarm(DEFAULT_WUID)

    assert call_count["n"] == 3
    assert isinstance(result, list)
    assert [alarm["id"] for alarm in result] == ["alarm-1"]
    assert result[0]["status"] == "ENABLE"


async def test_get_silent_time_retries_and_returns_fetch_error_on_persistent_server_error(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry
) -> None:
    """Same fix as getWatchAlarm above, applied to getSilentTime's `SlientTimes` operation."""
    call_count = {"n": 0}
    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_failing_then_default_callback("SlientTimes", 100, call_count), repeat=True)

        coordinator = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
        session = aiohttp_client.async_get_clientsession(hass)
        await coordinator.init(session=session)

        result = await coordinator.controller.getSilentTime(DEFAULT_WUID)

    assert isinstance(result, FetchError)
    assert call_count["n"] == 5


async def test_get_watch_safe_zones_retries_and_returns_fetch_error_on_persistent_server_error(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry
) -> None:
    """Same fix as getWatchAlarm above, applied to getWatchSafeZones's `SafeZones` operation."""
    call_count = {"n": 0}
    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_failing_then_default_callback("SafeZones", 100, call_count), repeat=True)

        coordinator = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
        session = aiohttp_client.async_get_clientsession(hass)
        await coordinator.init(session=session)

        result = await coordinator.controller.getWatchSafeZones(DEFAULT_WUID)

    assert isinstance(result, FetchError)
    assert call_count["n"] == 5


async def test_get_watch_alarm_does_not_retry_on_rate_limit(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """A 429 response is never retried: `graphql_client.py` raises `RateLimitError` (not the
    library's `Error` base), so it bypasses `getWatchAlarm`'s `except Error` retry loop
    entirely and aborts after exactly one attempt -- retrying a rate-limit response against
    this rate-sensitive API would make a ban worse, not better.
    """
    call_count = {"n": 0}
    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_rate_limited_callback("Alarms", call_count), repeat=True)

        coordinator = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
        session = aiohttp_client.async_get_clientsession(hass)
        await coordinator.init(session=session)

        with pytest.raises(RateLimitError):
            await coordinator.controller.getWatchAlarm(DEFAULT_WUID)

    assert call_count["n"] == 1


async def test_login_does_not_retry_on_rate_limit(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """Same rate-limit-abort behavior as above, but hitting the login call itself: `_login`'s
    retry loop catches `LoginError`/`Error`, neither of which `RateLimitError` subclasses, so
    a 429 during login also aborts after one attempt instead of retrying.
    """
    call_count = {"n": 0}
    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_rate_limited_callback("signInWithEmailOrPhone", call_count), repeat=True)

        coordinator = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
        session = aiohttp_client.async_get_clientsession(hass)

        with pytest.raises(RateLimitError):
            await coordinator.init(session=session)

    assert call_count["n"] == 1


async def test_async_update_xplora_data_raises_update_failed_on_rate_limit(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry
) -> None:
    """A full coordinator refresh cycle hitting a 429 (here on the `Alarms` operation inside
    `controller.setDevices()`'s per-watch fan-out) is converted to `UpdateFailed` at the
    coordinator boundary rather than letting `RateLimitError` propagate raw. Without this,
    HA's `DataUpdateCoordinator` would log it via its generic "Unexpected error fetching ...
    data" exception handler on every poll, and a first refresh would be a hard setup failure
    instead of `ConfigEntryNotReady`.
    """
    call_count = {"n": 0}
    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_rate_limited_callback("Alarms", call_count), repeat=True)

        coordinator = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)

        with pytest.raises(UpdateFailed):
            await coordinator.async_update_xplora_data()

    assert call_count["n"] == 1
