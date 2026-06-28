"""Logout (`ExpireToken`) regression tests.

Same mocked-transport approach as `test_rate_limit_fixes.py`: a per-test callback counts
operations by `operationName` so logout behavior is asserted against real request traffic
through the vendored client, not mocks of the client itself.

Covers: the controller's server-side logout invalidates the current token and disconnects;
an `E000004` on `ExpireToken` is tolerated (token already dead, no `AuthError`); a poll after
logout re-logs in exactly once; and the `async_remove_entry` hook expires the token on
deletion while never raising on transport failure.
"""

from __future__ import annotations

import re
from typing import Any

import aiohttp
from aioresponses import CallbackResult, aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch import async_remove_entry
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.const import ENDPOINT

from ..conftest import _make_graphql_callback
from ..fixtures.graphql_payloads import DEFAULT_OPERATION_PAYLOADS, DEFAULT_WUID
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


async def test_logout_sends_expire_token_and_disconnects(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    counts = {"expire": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        if body.get("operationName") == "ExpireToken":
            counts["expire"] += 1
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        coord = await _init_coordinator(hass, mock_config_entry_phone)
        assert coord.controller._isConnected() is True

        acknowledged = await coord.controller.logout()

    assert counts["expire"] == 1
    assert acknowledged is True
    assert coord.controller._isConnected() is False


async def test_logout_tolerates_e000004_without_autherror(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    counts = {"expire": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        if body.get("operationName") == "ExpireToken":
            counts["expire"] += 1
            # Token already dead -> server returns the auth-expired code. Must not raise.
            return CallbackResult(status=200, payload={"errors": [{"code": "E000004"}], "data": {"expireToken": None}})
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        coord = await _init_coordinator(hass, mock_config_entry_phone)

        acknowledged = await coord.controller.logout()

    assert counts["expire"] == 1
    assert acknowledged is False  # no usable confirmation, but no exception either
    assert coord.controller._isConnected() is False


async def test_poll_after_logout_triggers_exactly_one_relogin(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
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

        await coord.controller.logout()
        # Logout dropped the local token, so the next poll must re-authenticate -- once.
        result = await coord.async_update_xplora_data()

    assert counts["login"] == 2
    assert DEFAULT_WUID in result


async def test_async_remove_entry_logs_in_then_expires_token(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    counts = {"login": 0, "expire": 0}
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        op = body.get("operationName")
        if op == "signInWithEmailOrPhone":
            counts["login"] += 1
        elif op == "ExpireToken":
            counts["expire"] += 1
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        # No coordinator in hass.data (mirrors post-unload state at removal time).
        await async_remove_entry(hass, mock_config_entry_phone)

    assert counts["login"] == 1
    assert counts["expire"] == 1


async def test_async_remove_entry_never_raises_on_transport_failure(hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry) -> None:
    with aioresponses() as mocked:
        mocked.post(ENDPOINT, exception=aiohttp.ServerDisconnectedError(), repeat=True)
        # Even though login itself fails, removal must complete without raising.
        await async_remove_entry(hass, mock_config_entry_phone)
