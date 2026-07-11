"""Headline regression tests for the rate-limit/ban root-cause fixes.

See ref:XW-003. These exercise the real vendored
`pyxplora_api` client against a mocked transport (same approach as
`test_retry_behavior.py`): a custom `aioresponses` callback per test counts/branches on
`operationName` so each fix can be asserted against actual request traffic, not mocks of
the client itself.
"""

from __future__ import annotations

import re
from time import time
from typing import Any

import aiohttp
import pytest
from aioresponses import CallbackResult, aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.const import (
    DEFAULT_USER_AGENT,
    ENDPOINT,
    GqlOperation,
)
from custom_components.xplora_watch.pyxplora_api.exception_classes import AuthError, LoginError, RateLimitError
from custom_components.xplora_watch.pyxplora_api.pyxplora_api_async import PyXploraApi

from ..conftest import _make_graphql_callback
from ..fixtures.graphql_payloads import DEFAULT_OPERATION_PAYLOADS, DEFAULT_USER_ID, DEFAULT_WUID
from ..fixtures.rest_payloads import OPENSTREETMAP_REVERSE_GEOCODE


def _operations() -> dict[str, dict[str, Any]]:
    return {name: {"data": payload} for name, payload in DEFAULT_OPERATION_PAYLOADS.items()}


def _mock_geocoding(mocked: aioresponses) -> None:
    mocked.get(
        re.compile(r"https://nominatim\.openstreetmap\.org/.*"),
        payload=OPENSTREETMAP_REVERSE_GEOCODE,
        repeat=True,
    )


async def _init_coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> XploraDataUpdateCoordinator:
    coord = XploraDataUpdateCoordinator(hass, entry)
    session = aiohttp_client.async_get_clientsession(hass)
    await coord.init(session=session)
    return coord


# --- ISSUE-1: one long-lived PyXploraApi; no per-poll re-login -----------------------------


async def test_five_polls_trigger_exactly_one_login(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """The headline regression: N polls against a long-lived controller must log in once."""
    counts = {"login": 0, "refresh": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        op = body.get("operationName")
        if op == "signInWithEmailOrPhone":
            counts["login"] += 1
        elif op == "RefreshToken":
            counts["refresh"] += 1
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        for _ in range(5):
            await coord.async_update_xplora_data()

    assert counts["login"] == 1
    assert counts["refresh"] == 0


# --- ISSUE-2: token expiry driven by the server's expireDate --------------------------------


async def test_expired_server_token_triggers_exactly_one_relogin(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    counts = {"login": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        if body.get("operationName") == "signInWithEmailOrPhone":
            counts["login"] += 1
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        assert counts["login"] == 1

        coord.controller._token_expire = int(time()) - 1000
        await coord.init(aiohttp_client.async_get_clientsession(hass))
        assert counts["login"] == 2


async def test_unexpired_server_token_does_not_relogin(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    counts = {"login": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        if body.get("operationName") == "signInWithEmailOrPhone":
            counts["login"] += 1
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        assert counts["login"] == 1

        coord.controller._token_expire = int(time()) + 1000
        await coord.init(aiohttp_client.async_get_clientsession(hass))
        assert counts["login"] == 1


# --- ISSUE-5: forced re-login is a real re-login, not a silent no-op ------------------------


async def test_force_login_relogs_in_even_when_already_connected(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    counts = {"login": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        if body.get("operationName") == "signInWithEmailOrPhone":
            counts["login"] += 1
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        assert counts["login"] == 1
        assert coord.controller._isConnected() is True

        await coord.controller.init(forceLogin=True)
        assert counts["login"] == 2


# --- ISSUE-4 + ISSUE-3: E000004 -> AuthError -> RefreshToken(user uid) recovery -------------


async def test_e000004_recovers_via_refresh_token_with_user_uid(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    counts = {"login": 0, "refresh": 0, "alarms": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        op = body.get("operationName")
        if op == "signInWithEmailOrPhone":
            counts["login"] += 1
        elif op == "RefreshToken":
            counts["refresh"] += 1
            assert (body.get("variables") or {}).get("uid") == DEFAULT_USER_ID
            return CallbackResult(
                status=200,
                payload={
                    "data": {
                        "refreshToken": {
                            "id": "session-id-2",
                            "token": "access-token-2",
                            "refreshToken": "refresh-token-2",
                            "clientId": "client-1",
                            "issueDate": int(time()),
                            "expireDate": int(time()) + 3600,
                            "valid": True,
                        }
                    }
                },
            )
        elif op == "Alarms":
            counts["alarms"] += 1
            if counts["alarms"] == 1:
                # Top-level `code` (ref:XW-004) -- not under `extensions`.
                return CallbackResult(status=200, payload={"errors": [{"code": "E000004", "message": "token expired"}]})
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        result = await coord.async_update_xplora_data()

    assert counts["login"] == 1
    assert counts["refresh"] == 1
    assert DEFAULT_WUID in result


async def test_e000004_with_extensions_code_also_recovers(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """Defensive fallback: an `extensions.code` (not top-level) must also be detected."""
    counts = {"refresh": 0, "alarms": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        op = body.get("operationName")
        if op == "RefreshToken":
            counts["refresh"] += 1
            return CallbackResult(
                status=200,
                payload={
                    "data": {
                        "refreshToken": {
                            "token": "access-token-2",
                            "refreshToken": "refresh-token-2",
                            "issueDate": int(time()),
                            "expireDate": int(time()) + 3600,
                        }
                    }
                },
            )
        elif op == "Alarms":
            counts["alarms"] += 1
            if counts["alarms"] == 1:
                return CallbackResult(status=200, payload={"errors": [{"extensions": {"code": "E000004"}, "message": "expired"}]})
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        result = await coord.async_update_xplora_data()

    assert counts["refresh"] == 1
    assert DEFAULT_WUID in result


async def test_e000004_auth_refused_refresh_falls_back_to_one_relogin(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry
) -> None:
    """When the server actively REFUSES the refresh token (a structured `errors` body), that
    is a confirmed auth failure -> exactly one full re-login fallback fires.
    """
    counts = {"login": 0, "refresh": 0, "alarms": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        op = body.get("operationName")
        if op == "signInWithEmailOrPhone":
            counts["login"] += 1
        elif op == "RefreshToken":
            counts["refresh"] += 1
            # Structured error body == server refused the refresh token (auth failure).
            return CallbackResult(status=200, payload={"errors": [{"code": "E000004"}], "data": {"refreshToken": None}})
        elif op == "Alarms":
            counts["alarms"] += 1
            if counts["alarms"] == 1:
                return CallbackResult(status=200, payload={"errors": [{"code": "E000004"}]})
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        assert counts["login"] == 1

        result = await coord.async_update_xplora_data()

    assert counts["refresh"] == 1
    assert counts["login"] == 2  # initial login + the one bounded re-login fallback
    assert DEFAULT_WUID in result


async def test_e000004_raises_update_failed_when_refresh_and_relogin_both_fail(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry
) -> None:
    """Auth-refused refresh -> one re-login -> the re-login also fails -> UpdateFailed (bounded)."""
    counts = {"login": 0, "refresh": 0, "alarms": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        op = body.get("operationName")
        if op == "signInWithEmailOrPhone":
            counts["login"] += 1
            if counts["login"] == 1:
                return default_callback(url, **kwargs)
            return CallbackResult(status=200, payload={"errors": [{"message": "credentials rejected"}]})
        elif op == "RefreshToken":
            counts["refresh"] += 1
            # Auth refusal -> warrants the one re-login fallback (which then also fails).
            return CallbackResult(status=200, payload={"errors": [{"code": "E000004"}], "data": {"refreshToken": None}})
        elif op == "Alarms":
            counts["alarms"] += 1
            if counts["alarms"] == 1:
                return CallbackResult(status=200, payload={"errors": [{"code": "E000004"}]})
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)

        with pytest.raises(UpdateFailed):
            await coord.async_update_xplora_data()

    assert counts["refresh"] == 1
    # A re-login WAS attempted (auth refusal warrants it) -- contrast the transient tests
    # below where login count must stay at 1. The exact count is left loose because the one
    # `init(forceLogin=True)` call retries internally via `_login`'s own loop.
    assert counts["login"] > 1


async def test_e000004_rate_limit_during_refresh_does_not_relogin(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """A 429 during the RefreshToken request is transient, not an auth failure -> UpdateFailed
    with NO re-login (re-authenticating inside a rate-limit window worsens a ban).
    """
    counts = {"login": 0, "refresh": 0, "alarms": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        op = body.get("operationName")
        if op == "signInWithEmailOrPhone":
            counts["login"] += 1
        elif op == "RefreshToken":
            counts["refresh"] += 1
            return CallbackResult(status=429, reason="Too Many Requests", payload={"errors": [{"message": "rate limited"}]})
        elif op == "Alarms":
            counts["alarms"] += 1
            if counts["alarms"] == 1:
                return CallbackResult(status=200, payload={"errors": [{"code": "E000004"}]})
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        assert counts["login"] == 1

        with pytest.raises(UpdateFailed):
            await coord.async_update_xplora_data()

    assert counts["refresh"] == 1
    assert counts["login"] == 1  # NO re-login on a 429


async def test_e000004_connection_error_during_refresh_does_not_relogin(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry
) -> None:
    """A connection error during RefreshToken is transient -> UpdateFailed, NO re-login."""
    counts = {"login": 0, "refresh": 0, "alarms": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        op = body.get("operationName")
        if op == "signInWithEmailOrPhone":
            counts["login"] += 1
        elif op == "RefreshToken":
            counts["refresh"] += 1
            raise aiohttp.ServerDisconnectedError()
        elif op == "Alarms":
            counts["alarms"] += 1
            if counts["alarms"] == 1:
                return CallbackResult(status=200, payload={"errors": [{"code": "E000004"}]})
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        assert counts["login"] == 1

        with pytest.raises(UpdateFailed):
            await coord.async_update_xplora_data()

    assert counts["refresh"] == 1
    assert counts["login"] == 1  # NO re-login on a connection error


async def test_e000004_empty_refresh_response_does_not_relogin(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """A refresh response with no token AND no structured errors (e.g. a swallowed 5xx) is
    transient/unknown, not a confirmed auth refusal -> UpdateFailed, NO re-login.
    """
    counts = {"login": 0, "refresh": 0, "alarms": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        op = body.get("operationName")
        if op == "signInWithEmailOrPhone":
            counts["login"] += 1
        elif op == "RefreshToken":
            counts["refresh"] += 1
            return CallbackResult(status=200, payload={"data": {"refreshToken": None}})
        elif op == "Alarms":
            counts["alarms"] += 1
            if counts["alarms"] == 1:
                return CallbackResult(status=200, payload={"errors": [{"code": "E000004"}]})
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        assert counts["login"] == 1

        with pytest.raises(UpdateFailed):
            await coord.async_update_xplora_data()

    assert counts["refresh"] == 1
    assert counts["login"] == 1  # NO re-login on an empty/unknown refresh outcome


# --- ISSUE-8: wire fingerprint ---------------------------------------------------------------


async def test_endpoint_and_user_agent_match_the_app(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    assert ENDPOINT == "https://api.prod.myxplora.com/api/"
    assert DEFAULT_USER_AGENT == "okhttp/5.3.2"

    captured: dict[str, Any] = {}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        headers = kwargs.get("headers") or {}
        body = kwargs.get("json") or {}
        if body.get("operationName") == "signInWithEmailOrPhone":
            captured["headers"] = headers
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        await _init_coordinator(hass, mock_config_entry_phone)

    assert captured["headers"]["user-agent"] == "okhttp/5.3.2"
    assert "Accept-Language" in captured["headers"]


# --- ISSUE-9: never sign with w360, even when the backend sends it -------------------------


async def test_authenticated_calls_sign_with_static_secret_even_with_w360(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry
) -> None:
    from custom_components.xplora_watch.pyxplora_api.const import API_SECRET

    captured: list[str] = []
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        headers = kwargs.get("headers") or {}
        body = kwargs.get("json") or {}
        op = body.get("operationName")
        if op not in (None, "signInWithEmailOrPhone"):
            auth = headers.get("H-BackDoor-Authorization")
            if auth:
                captured.append(auth)
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        # Drive a real fetch so authenticated (non-login) requests actually fire -- init() itself
        # only logs in now (is_admin is derived from deviceList, no separate Contacts call).
        await coord.async_update_xplora_data()

    assert captured  # the deviceList / fresh-fix authenticated calls happened
    for auth_header in captured:
        assert auth_header == f"Bearer access-token-1:{API_SECRET}"


# --- ISSUE-11: raw connection/timeout errors --------------------------------------------------


async def test_connection_error_during_poll_raises_update_failed(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    operations = _operations()
    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_make_graphql_callback(operations), repeat=True)
        coord = await _init_coordinator(hass, mock_config_entry_phone)

    with aioresponses() as mocked_fail:
        mocked_fail.post(ENDPOINT, exception=aiohttp.ServerDisconnectedError(), repeat=True)
        # `pytest.raises(UpdateFailed)` is the assertion: a raw aiohttp exception would NOT
        # be an UpdateFailed, so this proves the connection error was wrapped, not leaked.
        with pytest.raises(UpdateFailed):
            await coord.async_update_xplora_data()


async def test_transient_connection_error_during_login_is_retried(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    counts = {"login": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        if body.get("operationName") == "signInWithEmailOrPhone":
            counts["login"] += 1
            if counts["login"] == 1:
                raise aiohttp.ServerDisconnectedError()
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)

    assert counts["login"] == 2
    assert coord.username


async def test_persistent_connection_error_during_login_ends_in_login_error(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry
) -> None:
    with aioresponses() as mocked:
        mocked.post(ENDPOINT, exception=aiohttp.ServerDisconnectedError(), repeat=True)

        coord = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
        session = aiohttp_client.async_get_clientsession(hass)
        with pytest.raises(LoginError):
            await coord.init(session=session)
        # Non-transient (LoginError): the controller can't authenticate, so it is dropped to
        # force a clean rebuild on the next attempt (the `except Error` branch of init()).
        assert getattr(coord, "controller", None) is None


async def test_transient_rate_limit_during_init_keeps_controller(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """A 429 during init()'s login is transient -> the controller (and any cached token) is
    retained for the next poll, NOT discarded and rebuilt into a fresh forced login."""
    with aioresponses() as mocked:
        # Every login attempt 429s. RateLimitError isn't an `Error` subclass, so it bypasses
        # _login's retry loop and propagates straight out of controller.init().
        mocked.post(ENDPOINT, status=429, repeat=True)

        coord = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
        session = aiohttp_client.async_get_clientsession(hass)
        with pytest.raises(RateLimitError):
            await coord.init(session=session)
        # Retained: the transient branch re-raises without deleting the controller.
        assert getattr(coord, "controller", None) is not None


# --- ISSUE-12: deviceList collapses the per-poll status fan-out into one call ---------------


async def test_one_poll_issues_exactly_one_device_list_call(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """ISSUE-12 invariant still holds: per-poll status collapses to a single `deviceList` call,
    with none of the old `WatchState`/`UserSteps`/`UnReadChatMsgCount`/`TrackWatch` per-watch
    fan-out returning. The fresh-fix locate calls (`askWatchLocate` + `WatchLastLocate`) are the
    one deliberate addition on top (see `_refresh_watch_fix`): a refresh must tell the watch to
    report a new position, which `deviceList` alone never does.
    """
    counts = {"deviceList": 0, "old_fan_out": 0, "ask_locate": 0, "last_locate": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        op = body.get("operationName")
        if op == "deviceList":
            counts["deviceList"] += 1
        elif op in ("WatchState", "UserSteps", "UnReadChatMsgCount", "TrackWatch"):
            counts["old_fan_out"] += 1
        elif op == "askWatchLocate":
            counts["ask_locate"] += 1
        elif op == "WatchLastLocate":
            counts["last_locate"] += 1
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        # Setup enumerates the account's watch list with one `deviceList` call (the lean login
        # response no longer carries it); zero the counters so the assertions measure only the poll.
        for key in counts:
            counts[key] = 0
        await coord.async_update_xplora_data()

    # Status still consolidated into one deviceList; the retired per-watch fan-out stays gone.
    assert counts["deviceList"] == 1
    assert counts["old_fan_out"] == 0
    # Fresh-fix per watch: exactly one askWatchLocate + at least one WatchLastLocate read.
    assert counts["ask_locate"] == 1
    assert counts["last_locate"] >= 1


async def test_device_list_e000004_triggers_auth_recovery(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """deviceList is routed through runAuthorizedGqlQuery_a, so an E000004 on it must also
    trigger the AuthError/RefreshToken recovery, not slip past untyped.
    """
    counts = {"deviceList": 0, "refresh": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        op = body.get("operationName")
        if op == "deviceList":
            counts["deviceList"] += 1
            # #1 is the setup-time watch-list enumeration; fail the *poll's* status deviceList (#2)
            # so the E000004 lands on the recovery-wrapped fetch path (`_with_recovery`) -- the
            # setup-time enumeration is deliberately not wrapped (see `coordinator.init`).
            if counts["deviceList"] == 2:
                return CallbackResult(status=200, payload={"errors": [{"code": "E000004"}]})
        elif op == "RefreshToken":
            counts["refresh"] += 1
            return CallbackResult(
                status=200,
                payload={
                    "data": {
                        "refreshToken": {
                            "token": "access-token-2",
                            "refreshToken": "refresh-token-2",
                            "issueDate": int(time()),
                            "expireDate": int(time()) + 3600,
                        }
                    }
                },
            )
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        result = await coord.async_update_xplora_data()

    assert counts["refresh"] == 1
    assert DEFAULT_WUID in result


async def test_setup_recovers_when_enumeration_hits_auth_error(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`init()` now makes an authenticated call (the watch-list `deviceList`), so it can raise
    `AuthError` -- e.g. a restored token our expiry estimate still trusted, rejected on that fetch.
    That path is outside the per-poll `_with_recovery` gate, so `coordinator.init` must force one
    re-login and retry rather than let the `AuthError` fail setup untyped.
    """
    calls = {"n": 0}
    real_init = PyXploraApi.init

    async def flaky_init(self: PyXploraApi, forceLogin: bool = False, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            # First attempt (forceLogin=False): simulate the restored token being rejected on the
            # post-login enumeration fetch.
            raise AuthError()
        await real_init(self, forceLogin=forceLogin, **kwargs)

    monkeypatch.setattr(PyXploraApi, "init", flaky_init)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_make_graphql_callback(_operations()), repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)

    # Handler forced exactly one re-login retry, and setup completed with the watch enumerated.
    assert calls["n"] == 2
    assert coord.controller.getWatchUserIDs() == [DEFAULT_WUID]


# --- Redundant per-watch calls removed: model info now comes from deviceList ----------------


async def test_poll_makes_no_redundant_watches_or_swinfo_calls(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    """`imei`/`osVersion`/`model` are read from the account-wide `deviceList` item, so a poll
    must NOT issue the old per-watch `Watches` / `CheckWatchByQrCode` queries (see plan Part 1)."""
    counts = {GqlOperation.WATCHES: 0, GqlOperation.CHECK_WATCH_BY_QR_CODE: 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        op = body.get("operationName")
        if op in counts:
            counts[op] += 1
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        _mock_geocoding(mocked)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        data = await coord.async_update_xplora_data()

    assert counts[GqlOperation.WATCHES] == 0
    assert counts[GqlOperation.CHECK_WATCH_BY_QR_CODE] == 0
    # The model info still lands on the watch, sourced from the deviceList item.
    assert data[DEFAULT_WUID]["model"] == "GPS-Watch"
