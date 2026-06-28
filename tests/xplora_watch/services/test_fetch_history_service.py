"""Tests for XploraFetchHistoryService.async_fetch_history."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.const import DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.services import XploraFetchHistoryService
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _register_coordinator(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> str:
    entry_id = coordinator._entry.entry_id
    hass.data.setdefault(DOMAIN, {})[entry_id] = coordinator
    return entry_id


async def test_defaults_to_yesterday_and_forces_fetch(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    """With no date, the service fetches yesterday (forced) for the explicit target(s)."""
    entry_id = _register_coordinator(hass, coordinator)
    service = XploraFetchHistoryService(hass, entry_id)
    yesterday = coordinator.history_yesterday_key()

    with (
        patch.object(coordinator, "async_fetch_history_day", new=AsyncMock(return_value=[])) as mock_fetch,
        patch.object(coordinator, "async_update_listeners") as mock_notify,
    ):
        await service.async_fetch_history(targets=[DEFAULT_WUID], date=None, kwargs={"user": [f"{entry_id} (testuser)"]})

    mock_fetch.assert_awaited_once_with(DEFAULT_WUID, yesterday, force=True)
    mock_notify.assert_called_once()


async def test_explicit_date_and_all_expands_watches(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    """An explicit date is honored and `all` expands to every watch id."""
    entry_id = _register_coordinator(hass, coordinator)
    service = XploraFetchHistoryService(hass, entry_id)

    with (
        patch.object(coordinator, "async_fetch_history_day", new=AsyncMock(return_value=[])) as mock_fetch,
        patch.object(coordinator, "async_update_listeners"),
    ):
        await service.async_fetch_history(targets=["all"], date="2026-06-25", kwargs={"user": [f"{entry_id} (testuser)"]})

    for wuid in coordinator.controller.getWatchUserIDs():
        mock_fetch.assert_any_await(wuid, "2026-06-25", force=True)


async def test_non_list_targets_logs_warning_and_skips(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    entry_id = _register_coordinator(hass, coordinator)
    service = XploraFetchHistoryService(hass, entry_id)

    with (
        patch.object(coordinator, "async_fetch_history_day", new=AsyncMock()) as mock_fetch,
        caplog.at_level(logging.WARNING),
    ):
        await service.async_fetch_history(targets=None, date=None, kwargs={"user": [f"{entry_id} (testuser)"]})

    mock_fetch.assert_not_awaited()
    assert "No watch id or type" in caplog.text
