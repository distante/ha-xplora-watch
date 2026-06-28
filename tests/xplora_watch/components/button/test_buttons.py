"""Tests for button.py: direct per-watch reboot / shutdown / update action buttons.

Each button is bound to one watch and, when pressed, runs the same action as the matching service
(reboot / shutdown / see) for that watch -- no target/user selection needed. reboot/shutdown are
admin-only and only created for admin watches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.button import (
    BUTTON_TYPES,
    XploraButton,
    async_setup_entry,
)
from custom_components.xplora_watch.const import (
    BUTTON_REBOOT,
    BUTTON_REFRESH_FUNCTIONS,
    BUTTON_SHUTDOWN,
    BUTTON_UPDATE,
    DOMAIN,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.exception_classes import AuthError, RateLimitError
from custom_components.xplora_watch.pyxplora_api.exception_classes import ConnectionError as XploraConnectionError
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _description(key: str):
    return next(d for d in BUTTON_TYPES if d.key == key)


def _make_button(hass: HomeAssistant, config_entry: ConfigEntry, coordinator: XploraDataUpdateCoordinator, key: str) -> XploraButton:
    ward = coordinator.controller.watchs[0]["ward"]
    button = XploraButton(config_entry, coordinator, ward, DEFAULT_WUID, _description(key))
    button.hass = hass
    return button


def _capture() -> tuple[list, object]:
    captured: list = []

    def capture_entities(new_entities, update_before_add=False) -> None:  # noqa: ARG001
        captured.extend(new_entities)

    return captured, capture_entities


async def test_setup_creates_all_buttons_regardless_of_admin(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """All three buttons are created for every watch -- there is no client-side admin gate on
    reboot/shutdown anymore (the server authorizes the action; a non-primary guardian can too)."""
    hass.data.setdefault(DOMAIN, {})[mock_config_entry_phone.entry_id] = coordinator_with_data
    captured, capture_entities = _capture()

    await async_setup_entry(hass, mock_config_entry_phone, capture_entities)

    keys = {e.entity_description.key for e in captured}
    assert {BUTTON_UPDATE, BUTTON_REFRESH_FUNCTIONS, BUTTON_REBOOT, BUTTON_SHUTDOWN} <= keys


async def test_buttons_disabled_by_default(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """Action buttons follow the integration convention: registered but disabled-by-default."""
    hass.data.setdefault(DOMAIN, {})[mock_config_entry_phone.entry_id] = coordinator_with_data
    captured, capture_entities = _capture()

    await async_setup_entry(hass, mock_config_entry_phone, capture_entities)

    assert captured
    assert all(not e.entity_registry_enabled_default for e in captured)


async def test_press_update_refreshes_only_this_watch(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """Pressing `update` calls the coordinator refresh for this watch only (same as `see`)."""
    button = _make_button(hass, mock_config_entry_phone, coordinator_with_data, BUTTON_UPDATE)
    coordinator_with_data.async_update_xplora_data = AsyncMock()

    await button.async_press()

    coordinator_with_data.async_update_xplora_data.assert_awaited_once_with([DEFAULT_WUID])


async def test_press_refresh_functions_refreshes_only_this_watch(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """Pressing `refresh_functions` re-fetches alarms/silent times for this watch only (same as the
    `refresh_functions` service), distinct from `update`/`see` which refreshes location/battery."""
    button = _make_button(hass, mock_config_entry_phone, coordinator_with_data, BUTTON_REFRESH_FUNCTIONS)
    coordinator_with_data.async_refresh_functions = AsyncMock()

    await button.async_press()

    coordinator_with_data.async_refresh_functions.assert_awaited_once_with([DEFAULT_WUID])


async def test_press_refresh_functions_failure_raises_homeassistant_error(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """A failed functions refresh surfaces as a HomeAssistantError (and is recorded for the UI)."""
    button = _make_button(hass, mock_config_entry_phone, coordinator_with_data, BUTTON_REFRESH_FUNCTIONS)
    coordinator_with_data.async_refresh_functions = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(HomeAssistantError):
        await button.async_press()


async def test_press_reboot_calls_controller_for_this_watch(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    button = _make_button(hass, mock_config_entry_phone, coordinator_with_data, BUTTON_REBOOT)
    coordinator_with_data.controller.reboot = AsyncMock(return_value=True)

    await button.async_press()

    coordinator_with_data.controller.reboot.assert_awaited_once_with(DEFAULT_WUID)


async def test_press_shutdown_calls_controller_for_this_watch(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    button = _make_button(hass, mock_config_entry_phone, coordinator_with_data, BUTTON_SHUTDOWN)
    coordinator_with_data.controller.shutdown = AsyncMock(return_value=True)

    await button.async_press()

    coordinator_with_data.controller.shutdown.assert_awaited_once_with(DEFAULT_WUID)


async def test_press_shutdown_auth_error_raises_homeassistant_error(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """An expired session (AuthError) surfaces as a clean HomeAssistantError."""
    button = _make_button(hass, mock_config_entry_phone, coordinator_with_data, BUTTON_SHUTDOWN)
    coordinator_with_data.controller.shutdown = AsyncMock(side_effect=AuthError())

    with pytest.raises(HomeAssistantError):
        await button.async_press()


@pytest.mark.parametrize("error", [RateLimitError("429"), XploraConnectionError("boom")])
async def test_press_shutdown_transient_error_raises_homeassistant_error(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
    error: Exception,
) -> None:
    """A 429 / connection drop surfaces as a clean HomeAssistantError, not a raw traceback."""
    button = _make_button(hass, mock_config_entry_phone, coordinator_with_data, BUTTON_SHUTDOWN)
    coordinator_with_data.controller.shutdown = AsyncMock(side_effect=error)

    with pytest.raises(HomeAssistantError):
        await button.async_press()


async def test_press_reboot_rejected_raises_homeassistant_error(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """A False result (backend refused -- e.g. the watch is off) surfaces as a HomeAssistantError so
    the UI reports the real outcome instead of a phantom success."""
    button = _make_button(hass, mock_config_entry_phone, coordinator_with_data, BUTTON_REBOOT)
    coordinator_with_data.controller.reboot = AsyncMock(return_value=False)

    with pytest.raises(HomeAssistantError):
        await button.async_press()


async def test_press_shutdown_rejected_raises_homeassistant_error(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    """A False shutdown result (watch off/offline) surfaces as a HomeAssistantError, not a success."""
    button = _make_button(hass, mock_config_entry_phone, coordinator_with_data, BUTTON_SHUTDOWN)
    coordinator_with_data.controller.shutdown = AsyncMock(return_value=False)

    with pytest.raises(HomeAssistantError):
        await button.async_press()
