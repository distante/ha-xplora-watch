"""Tests for the ``refresh_functions`` service (on-demand alarm/silent/safezone refresh)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.xplora_watch.const import ATTR_SERVICE_REFRESH_FUNCTIONS, DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID

from ..conftest import setup_service_target


async def test_targeted_device_refreshes_its_watch(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    devices = await setup_service_target(hass, coordinator)

    with patch.object(coordinator, "async_refresh_functions", new=AsyncMock()) as mock_refresh:
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_REFRESH_FUNCTIONS, {"device_id": [devices[DEFAULT_WUID]]}, blocking=True)

    mock_refresh.assert_awaited_once_with([DEFAULT_WUID])


async def test_no_device_target_raises_and_skips(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    await setup_service_target(hass, coordinator)

    with patch.object(coordinator, "async_refresh_functions", new=AsyncMock()) as mock_refresh:
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(DOMAIN, ATTR_SERVICE_REFRESH_FUNCTIONS, {"device_id": []}, blocking=True)

    mock_refresh.assert_not_awaited()
