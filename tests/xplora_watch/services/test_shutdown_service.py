"""Tests for XploraShutdownService.async_shutdown."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.const import DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.services import XploraShutdownService
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _register_coordinator(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> str:
    entry_id = coordinator._entry.entry_id
    hass.data.setdefault(DOMAIN, {})[entry_id] = coordinator
    return entry_id


async def test_shutdown_admin_watch_succeeds(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    entry_id = _register_coordinator(hass, coordinator)
    shutdown_service = XploraShutdownService(hass, entry_id)

    with caplog.at_level(logging.DEBUG):
        await shutdown_service.async_shutdown([DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "Shutdown failed" not in caplog.text
    assert "Shutdown result: True" in caplog.text


async def test_shutdown_non_primary_guardian_still_succeeds(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    graphql_operations,
    caplog,
) -> None:
    # A non-primary (guardianType != "FIRST") guardian can still shut a watch down: the admin gate
    # was dropped, so controller.shutdown() just fires the mutation and the server authorizes it
    # (ref:XW-007). The
    # Contacts payload no longer affects the outcome (shutdown does not call it).
    graphql_operations["Contacts"] = {
        "data": {
            "contacts": {
                "contacts": [
                    {
                        "contactUser": {"id": "user-id-001", "xcoin": 0},
                        "guardianType": "SECOND",
                        "create": 1700000000,
                        "update": 1700000000,
                        "name": "Parent Name",
                        "countryPhoneNumber": "49",
                        "phoneNumber": "1700000001",
                    }
                ]
            }
        }
    }
    entry_id = _register_coordinator(hass, coordinator)
    shutdown_service = XploraShutdownService(hass, entry_id)

    with caplog.at_level(logging.DEBUG):
        await shutdown_service.async_shutdown([DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "Shutdown failed" not in caplog.text
    assert "Shutdown result: True" in caplog.text


async def test_shutdown_rejected_by_backend_logs_warning(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    graphql_operations,
    caplog,
) -> None:
    # The backend refuses the command (False) -- e.g. the watch is off/offline. The service must say
    # so, not silently debug-log it as if it worked.
    graphql_operations["ShutDown"] = {"data": {"ShutDown": False}}
    entry_id = _register_coordinator(hass, coordinator)
    shutdown_service = XploraShutdownService(hass, entry_id)

    with caplog.at_level(logging.WARNING):
        await shutdown_service.async_shutdown([DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "Shutdown was not accepted" in caplog.text


async def test_non_list_targets_logs_warning_and_skips(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    entry_id = _register_coordinator(hass, coordinator)
    shutdown_service = XploraShutdownService(hass, entry_id)

    with caplog.at_level(logging.WARNING):
        await shutdown_service.async_shutdown(None, kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "No watch ID or type" in caplog.text
