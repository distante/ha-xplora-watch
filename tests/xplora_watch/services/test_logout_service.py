"""Tests for XploraLogoutService.async_logout (manual server-side logout service)."""

from __future__ import annotations

import logging
from typing import Any

from aioresponses import CallbackResult, aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.const import ENDPOINT
from custom_components.xplora_watch.services import XploraLogoutService

from ..conftest import _make_graphql_callback
from ..fixtures.graphql_payloads import DEFAULT_OPERATION_PAYLOADS


def _register_coordinator(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> str:
    entry_id = coordinator._entry.entry_id
    hass.data.setdefault(DOMAIN, {})[entry_id] = coordinator
    return entry_id


def _operations() -> dict[str, dict[str, Any]]:
    return {name: {"data": payload} for name, payload in DEFAULT_OPERATION_PAYLOADS.items()}


async def test_logout_success_logs_result_and_disconnects(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    assert coordinator.controller._isConnected() is True
    entry_id = _register_coordinator(hass, coordinator)
    logout_service = XploraLogoutService(hass, entry_id)

    with caplog.at_level(logging.DEBUG):
        await logout_service.async_logout(kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "Logout result" in caplog.text
    assert "could not reach" not in caplog.text
    # Local session is cleared so the next coordinator poll re-logs in.
    assert coordinator.controller._isConnected() is False


async def test_logout_tolerates_e000004_as_success(
    hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, graphql_operations, caplog
) -> None:
    # ExpireToken returning E000004 means the token is already dead -- the desired end state.
    # It must NOT be raised as AuthError (it routes through the raw query, not the authorized
    # one) and must NOT be reported as a transient failure.
    graphql_operations["ExpireToken"] = {"errors": [{"code": "E000004"}], "data": {"expireToken": None}}
    entry_id = _register_coordinator(hass, coordinator)
    logout_service = XploraLogoutService(hass, entry_id)

    with caplog.at_level(logging.DEBUG):
        await logout_service.async_logout(kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "could not reach" not in caplog.text
    assert coordinator.controller._isConnected() is False


async def test_logout_transient_error_logs_warning_and_clears_session(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, caplog
) -> None:
    """A 429 on ExpireToken must be caught, logged as a clean warning (not a traceback), and
    the local session cleared anyway so the next poll recovers."""
    operations = _operations()
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        if body.get("operationName") == "ExpireToken":
            return CallbackResult(status=429, reason="Too Many Requests", payload={"errors": [{"message": "rate limited"}]})
        return default_callback(url, **kwargs)

    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_callback, repeat=True)
        coord = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
        session = aiohttp_client.async_get_clientsession(hass)
        await coord.init(session=session)
        entry_id = _register_coordinator(hass, coord)
        logout_service = XploraLogoutService(hass, entry_id)

        with caplog.at_level(logging.WARNING):
            await logout_service.async_logout(kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "could not reach the Xplora server" in caplog.text
    # `logout()`'s finally block clears local state even when the server call raised.
    assert coord.controller._isConnected() is False
