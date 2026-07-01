"""Tests for the ``fetch_history`` service, via device targeting."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.xplora_watch.const import ATTR_SERVICE_FETCH_HISTORY, DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID

from ..conftest import setup_service_target


async def test_defaults_to_yesterday_and_forces_fetch(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    """With no date, the service fetches yesterday (forced) for the targeted watch."""
    devices = await setup_service_target(hass, coordinator)
    yesterday = coordinator.history_yesterday_key()

    with (
        patch.object(coordinator, "async_fetch_history_day", new=AsyncMock(return_value=[])) as mock_fetch,
        patch.object(coordinator, "async_update_listeners") as mock_notify,
    ):
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_FETCH_HISTORY, {"device_id": [devices[DEFAULT_WUID]]}, blocking=True)

    mock_fetch.assert_awaited_once_with(DEFAULT_WUID, yesterday, force=True)
    mock_notify.assert_called_once()


async def test_explicit_date_is_honored(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    """An explicit date is honored for the targeted watch."""
    devices = await setup_service_target(hass, coordinator)

    with (
        patch.object(coordinator, "async_fetch_history_day", new=AsyncMock(return_value=[])) as mock_fetch,
        patch.object(coordinator, "async_update_listeners"),
    ):
        await hass.services.async_call(
            DOMAIN, ATTR_SERVICE_FETCH_HISTORY, {"device_id": [devices[DEFAULT_WUID]], "date": "2026-06-25"}, blocking=True
        )

    mock_fetch.assert_awaited_once_with(DEFAULT_WUID, "2026-06-25", force=True)


async def test_no_device_target_raises_and_skips(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    await setup_service_target(hass, coordinator)

    with (
        patch.object(coordinator, "async_fetch_history_day", new=AsyncMock()) as mock_fetch,
        pytest.raises(ServiceValidationError),
    ):
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_FETCH_HISTORY, {"device_id": []}, blocking=True)

    mock_fetch.assert_not_awaited()
