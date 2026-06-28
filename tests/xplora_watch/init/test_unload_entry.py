"""Tests for async_unload_entry in custom_components/xplora_watch/__init__.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.const import Platform
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch import PLATFORMS, async_unload_entry
from custom_components.xplora_watch.const import DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator


async def test_unload_entry_success_pops_coordinator_and_unloads_services(
    hass, mock_config_entry_phone: MockConfigEntry, coordinator
) -> None:
    """unload_ok=True pops the coordinator and calls async_unload_services."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry_phone.entry_id] = coordinator

    with (
        patch.object(hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)) as mock_unload,
        patch("custom_components.xplora_watch.async_unload_services") as mock_unload_services,
    ):
        result = await async_unload_entry(hass, mock_config_entry_phone)

    assert result is True
    assert mock_config_entry_phone.entry_id not in hass.data[DOMAIN]
    mock_unload_services.assert_called_once_with(hass)

    expected_platforms = [platform for platform in PLATFORMS if platform != Platform.NOTIFY]
    mock_unload.assert_awaited_once_with(mock_config_entry_phone, expected_platforms)


async def test_unload_entry_failure_keeps_coordinator_but_still_unloads_services(
    hass, mock_config_entry_phone: MockConfigEntry, coordinator
) -> None:
    """unload_ok=False keeps the coordinator stored, but async_unload_services is still called unconditionally."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry_phone.entry_id] = coordinator

    with (
        patch.object(hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=False)),
        patch("custom_components.xplora_watch.async_unload_services") as mock_unload_services,
    ):
        result = await async_unload_entry(hass, mock_config_entry_phone)

    assert result is False
    assert hass.data[DOMAIN][mock_config_entry_phone.entry_id] is coordinator
    mock_unload_services.assert_called_once_with(hass)


async def test_unload_entry_does_not_log_out(
    hass, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    """Regression guard for the core no-churn invariant.

    Unload runs on every reload/restart/options-change, so it must NOT issue a server-side
    logout -- doing so would expire the token and force a fresh login on the next setup, the
    exact re-login churn this integration was reworked to avoid. Server-side logout belongs
    only in `async_remove_entry` (deletion).
    """
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][mock_config_entry_phone.entry_id] = coordinator
    assert coordinator.controller._isConnected() is True

    logout_calls = {"count": 0}
    real_logout = coordinator.controller.logout

    async def _counting_logout(*args: Any, **kwargs: Any) -> bool:
        logout_calls["count"] += 1
        return await real_logout(*args, **kwargs)

    with (
        patch.object(hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)),
        patch("custom_components.xplora_watch.async_unload_services"),
        patch.object(coordinator.controller, "logout", new=_counting_logout),
    ):
        result = await async_unload_entry(hass, mock_config_entry_phone)

    assert result is True
    assert logout_calls["count"] == 0
    # Token is left intact so the next setup reuses it instead of re-authenticating.
    assert coordinator.controller._isConnected() is True
