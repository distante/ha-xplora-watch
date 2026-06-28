"""Tests for XploraRefreshFunctionsService.async_refresh_functions."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.const import DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.services import XploraRefreshFunctionsService
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _register_coordinator(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> str:
    entry_id = coordinator._entry.entry_id
    hass.data.setdefault(DOMAIN, {})[entry_id] = coordinator
    return entry_id


async def test_all_expands_to_full_watch_id_list(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    entry_id = _register_coordinator(hass, coordinator)
    service = XploraRefreshFunctionsService(hass, entry_id)

    with patch.object(coordinator, "async_refresh_functions", new=AsyncMock()) as mock_refresh:
        await service.async_refresh_functions(targets=["all"], kwargs={"user": [f"{entry_id} (testuser)"]})

    mock_refresh.assert_awaited_once_with(coordinator.controller.getWatchUserIDs())


async def test_explicit_target_list_passed_through_unchanged(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    entry_id = _register_coordinator(hass, coordinator)
    service = XploraRefreshFunctionsService(hass, entry_id)

    with patch.object(coordinator, "async_refresh_functions", new=AsyncMock()) as mock_refresh:
        await service.async_refresh_functions(targets=[DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    mock_refresh.assert_awaited_once_with([DEFAULT_WUID])


async def test_non_list_targets_logs_warning_and_skips(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    entry_id = _register_coordinator(hass, coordinator)
    service = XploraRefreshFunctionsService(hass, entry_id)

    with patch.object(coordinator, "async_refresh_functions", new=AsyncMock()) as mock_refresh, caplog.at_level(logging.WARNING):
        await service.async_refresh_functions(targets=None, kwargs={"user": [f"{entry_id} (testuser)"]})

    mock_refresh.assert_not_awaited()
    assert "No watch id or type" in caplog.text
