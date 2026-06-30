"""Tests for XploraShutdownService.async_shutdown."""

from __future__ import annotations

import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

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


async def test_shutdown_contact_watch_is_blocked(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    caplog,
) -> None:
    # A Contact (role resolved and not the Guardian: guardianType != "FIRST") may NOT shut the watch
    # down. The integration restricts this control action to the watch's Guardian as a client policy
    # (ref:XW-009): the service raises ServiceValidationError and the shutdown mutation is never sent.
    # (Reverses the old ref:XW-007 "non-primary guardian still succeeds" behavior; the chat half of
    # ref:XW-007 still holds.)
    coordinator.is_admin = {DEFAULT_WUID: False}
    entry_id = _register_coordinator(hass, coordinator)
    shutdown_service = XploraShutdownService(hass, entry_id)

    with caplog.at_level(logging.DEBUG), pytest.raises(ServiceValidationError):
        await shutdown_service.async_shutdown([DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "Shutdown result" not in caplog.text  # gated before the mutation, so it never fired


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
