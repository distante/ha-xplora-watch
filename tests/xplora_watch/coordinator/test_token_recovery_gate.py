"""Single-flight, centralized token-recovery gate (`_with_recovery` / `_recover_token`).

Critical ban-defense behavior (ref:XW-008): concurrent
controller calls that hit an expired token must coalesce onto exactly ONE `RefreshToken`
round-trip and share its outcome -- never each firing their own refresh (the rate-limit storm
this integration exists to avoid).

These are unit tests of the gate itself: the controller is a mock so the refresh/re-login call
counts and the concurrency are fully deterministic (an `asyncio.Event` barrier holds the in-flight
recovery open until all concurrent callers have piled onto it). The end-to-end recovery against a
real mocked transport lives in `test_rate_limit_fixes.py`.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import CONF_WATCHES
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.exception_classes import AuthError, LoginError, RateLimitError
from custom_components.xplora_watch.pyxplora_api.exception_classes import ConnectionError as XploraConnectionError
from custom_components.xplora_watch.pyxplora_api.pyxplora_api_async import TokenRefreshOutcome


def _make_coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> XploraDataUpdateCoordinator:
    """A coordinator with a mocked controller, no network/storage touched."""
    coord = XploraDataUpdateCoordinator(hass, entry)
    coord.controller = MagicMock()
    coord.controller.refresh = AsyncMock(return_value=TokenRefreshOutcome.REFRESHED)
    coord.controller.init = AsyncMock()
    coord._persist_session = AsyncMock()  # type: ignore[method-assign]  # avoid `.storage` I/O
    return coord


def _auth_once_factory() -> "callable":
    """Build a fresh coro factory that raises `AuthError` on its first call, then returns 'ok'.

    Mirrors a real controller call: the first attempt fails on the expired token, the retry (after
    recovery) succeeds.
    """
    state = {"n": 0}

    async def _call() -> str:
        state["n"] += 1
        if state["n"] == 1:
            raise AuthError("E000004")
        return "ok"

    return _call


async def test_concurrent_auth_errors_coalesce_to_one_refresh(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """Three parallel calls all hitting an expired token -> exactly ONE refresh, all succeed."""
    coord = _make_coordinator(hass, mock_config_entry_phone)

    release = asyncio.Event()
    refresh_calls = 0

    async def _refresh() -> TokenRefreshOutcome:
        nonlocal refresh_calls
        refresh_calls += 1
        await release.wait()  # hold the recovery open so followers must coalesce onto it
        return TokenRefreshOutcome.REFRESHED

    coord.controller.refresh = AsyncMock(side_effect=_refresh)

    tasks = [asyncio.create_task(coord._with_recovery(_auth_once_factory())) for _ in range(3)]
    await asyncio.sleep(0.05)  # let all three raise AuthError and enter recovery
    release.set()
    results = await asyncio.gather(*tasks)

    assert results == ["ok", "ok", "ok"]
    assert refresh_calls == 1  # single-flight: not 3
    assert coord._token_generation == 1


async def test_stale_generation_skips_second_refresh(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """A stale `AuthError` arriving AFTER a completed recovery must not trigger a second refresh."""
    coord = _make_coordinator(hass, mock_config_entry_phone)
    refresh = AsyncMock(return_value=TokenRefreshOutcome.REFRESHED)
    coord.controller.refresh = refresh

    await coord._recover_token(seen_generation=0)
    assert coord._token_generation == 1
    assert refresh.await_count == 1

    # Caller captured generation 0 before its request, but the token already rotated -> no-op.
    await coord._recover_token(seen_generation=0)
    assert refresh.await_count == 1  # unchanged
    assert coord._token_generation == 1


async def test_concurrent_transient_refresh_failure_is_shared(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """A transient refresh failure (429) is shared by all coalesced callers: one refresh, no
    re-login, generation unchanged, every caller sees the same error.
    """
    coord = _make_coordinator(hass, mock_config_entry_phone)

    release = asyncio.Event()
    refresh_calls = 0

    async def _refresh() -> TokenRefreshOutcome:
        nonlocal refresh_calls
        refresh_calls += 1
        await release.wait()
        raise RateLimitError("429")

    coord.controller.refresh = AsyncMock(side_effect=_refresh)

    async def _always_auth() -> str:
        raise AuthError("E000004")

    tasks = [asyncio.create_task(coord._with_recovery(_always_auth)) for _ in range(3)]
    await asyncio.sleep(0.05)
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert all(isinstance(r, RateLimitError) for r in results)
    assert refresh_calls == 1  # single-flight even on failure
    assert coord.controller.init.await_count == 0  # never escalated to a re-login
    assert coord._token_generation == 0  # not bumped on failure


async def test_auth_refused_escalates_to_one_relogin(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """AUTH_REFUSED refresh -> exactly one `init(forceLogin=True)` re-login, then the retry runs."""
    coord = _make_coordinator(hass, mock_config_entry_phone)
    coord.controller.refresh = AsyncMock(return_value=TokenRefreshOutcome.AUTH_REFUSED)

    result = await coord._with_recovery(_auth_once_factory())

    assert result == "ok"
    assert coord.controller.refresh.await_count == 1
    assert coord.controller.init.await_count == 1  # the one bounded re-login
    assert coord._token_generation == 1


async def test_failed_refresh_outcome_does_not_relogin(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """A FAILED (empty/unknown) refresh outcome is transient -> no re-login, recovery surfaces as
    AuthError (caller treats it as an auth failure, not a silent success).
    """
    coord = _make_coordinator(hass, mock_config_entry_phone)
    coord.controller.refresh = AsyncMock(return_value=TokenRefreshOutcome.FAILED)

    async def _always_auth() -> str:
        raise AuthError("E000004")

    try:
        await coord._with_recovery(_always_auth)
    except AuthError:
        pass
    else:  # pragma: no cover - the assertion below makes a missing raise a clear failure
        raise AssertionError("expected AuthError to propagate on a FAILED refresh outcome")

    assert coord.controller.init.await_count == 0  # NO re-login
    assert coord._token_generation == 0  # not bumped


async def test_failed_relogin_evicts_stale_controller(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """AUTH_REFUSED escalates to a re-login; if that re-login fails non-transiently (LoginError),
    the stale controller MUST be evicted so `set_controller()` rebuilds it fresh next poll.

    Without the eviction, the un-authenticatable controller lingers (set_controller skips rebuild
    while it is non-None) and every subsequent poll loops on UpdateFailed until the entry reloads.
    """
    coord = _make_coordinator(hass, mock_config_entry_phone)
    coord.controller.refresh = AsyncMock(return_value=TokenRefreshOutcome.AUTH_REFUSED)
    coord.controller.init = AsyncMock(side_effect=LoginError("bad credentials"))

    try:
        await coord._recover_token(seen_generation=0)
    except LoginError:
        pass
    else:  # pragma: no cover - the assertion below makes a missing raise a clear failure
        raise AssertionError("expected LoginError to propagate from the failed re-login")

    assert getattr(coord, "controller", None) is None  # evicted -> next poll rebuilds it
    assert coord._token_generation == 0  # not bumped on failure


async def test_transient_relogin_failure_keeps_controller(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """A transient (429 / connection) failure during the re-login keeps the controller -- nothing is
    wrong with it, the next poll should retry cheaply -- and does not bump the generation.
    """
    coord = _make_coordinator(hass, mock_config_entry_phone)
    coord.controller.refresh = AsyncMock(return_value=TokenRefreshOutcome.AUTH_REFUSED)
    coord.controller.init = AsyncMock(side_effect=XploraConnectionError("boom"))

    try:
        await coord._recover_token(seen_generation=0)
    except XploraConnectionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected XploraConnectionError to propagate")

    assert getattr(coord, "controller", None) is not None  # kept for a cheap retry
    assert coord._token_generation == 0


async def test_configured_wuids_falls_back_to_empty_when_controller_evicted(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry
) -> None:
    """`_configured_wuids` must not raise `AttributeError` when the controller was evicted and no
    `CONF_WATCHES` option is saved -- it resolves to an empty list so the 01:00 auto-fetch no-ops.
    """
    options = {k: v for k, v in mock_config_entry_phone.options.items() if k != CONF_WATCHES}
    hass.config_entries.async_update_entry(mock_config_entry_phone, options=options)
    coord = _make_coordinator(hass, mock_config_entry_phone)
    del coord.controller

    assert coord._configured_wuids == []  # no AttributeError


async def test_auto_fetch_yesterday_survives_evicted_controller(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """The 01:00 auto-fetch fires independently of the poll; with the controller evicted and no
    `CONF_WATCHES` option it must clean-no-op, not crash the timer callback with `AttributeError`.
    """
    options = {k: v for k, v in mock_config_entry_phone.options.items() if k != CONF_WATCHES}
    hass.config_entries.async_update_entry(mock_config_entry_phone, options=options)
    coord = _make_coordinator(hass, mock_config_entry_phone)
    del coord.controller
    coord.async_update_listeners = MagicMock()  # type: ignore[method-assign]

    await coord._async_auto_fetch_yesterday(_now=None)  # must not raise


async def test_history_fetch_recovers_through_the_gate(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """`async_fetch_history_day` routes through the gate: a single expired token is recovered at
    the source and the fetch retried, so the caller gets points (not an AuthError).
    """
    coord = _make_coordinator(hass, mock_config_entry_phone)
    calls = {"n": 0}

    async def _loc_history(*_args: object, **_kwargs: object) -> dict[str, object]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise AuthError("E000004")
        return {"locHistory": {"list": []}}

    coord.controller.getWatchLocHistory = AsyncMock(side_effect=_loc_history)

    points = await coord.async_fetch_history_day(mock_config_entry_phone.entry_id, "2026-06-27", force=True)

    assert points == []  # empty day, but a clean result -- recovery happened, no raise
    assert calls["n"] == 2  # original attempt + one retry after recovery
    assert coord.controller.refresh.await_count == 1
    assert coord._token_generation == 1
