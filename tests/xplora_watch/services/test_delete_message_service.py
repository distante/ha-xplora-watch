"""Tests for XploraDeleteMessageFromAppService.async_delete_message_from_app."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.const import DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.services import XploraDeleteMessageFromAppService
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _register_coordinator(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> str:
    entry_id = coordinator._entry.entry_id
    hass.data.setdefault(DOMAIN, {})[entry_id] = coordinator
    return entry_id


async def test_delete_message_happy_path(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    entry_id = _register_coordinator(hass, coordinator)
    delete_service = XploraDeleteMessageFromAppService(hass, entry_id)

    with caplog.at_level(logging.ERROR):
        await delete_service.async_delete_message_from_app("msg-1", [DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "Message cannot deleted" not in caplog.text


async def test_empty_message_id_after_strip_logs_warning_and_skips_delete(
    hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog
) -> None:
    entry_id = _register_coordinator(hass, coordinator)
    delete_service = XploraDeleteMessageFromAppService(hass, entry_id)

    with caplog.at_level(logging.WARNING):
        await delete_service.async_delete_message_from_app("   ", [DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "You must provide an ID" in caplog.text


async def test_delete_failure_logs_error_but_does_not_raise(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    graphql_operations,
    caplog,
) -> None:
    graphql_operations["DeleteChatMessage"] = {"data": {"deleteMsg": False}}
    entry_id = _register_coordinator(hass, coordinator)
    delete_service = XploraDeleteMessageFromAppService(hass, entry_id)

    with caplog.at_level(logging.ERROR):
        await delete_service.async_delete_message_from_app("msg-1", [DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "Message cannot deleted" in caplog.text
