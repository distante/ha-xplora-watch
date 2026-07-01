"""Tests for the ``see`` service (manual live-status refresh), via device targeting."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.xplora_watch.const import ATTR_SERVICE_SEE, DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID

from ..conftest import setup_service_target


async def test_targeted_device_refreshes_its_watch(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    """A single device target refreshes exactly that watch (resolved from the device)."""
    devices = await setup_service_target(hass, coordinator)

    with patch.object(coordinator, "async_update_xplora_data", new=AsyncMock()) as mock_update:
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_SEE, {"device_id": [devices[DEFAULT_WUID]]}, blocking=True)

    mock_update.assert_awaited_once_with([DEFAULT_WUID])


async def test_no_device_target_raises_and_skips_update(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    devices = await setup_service_target(hass, coordinator)  # noqa: F841  (registers the services)

    with patch.object(coordinator, "async_update_xplora_data", new=AsyncMock()) as mock_update:
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(DOMAIN, ATTR_SERVICE_SEE, {"device_id": []}, blocking=True)

    mock_update.assert_not_awaited()
