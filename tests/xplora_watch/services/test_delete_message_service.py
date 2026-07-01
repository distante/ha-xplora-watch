"""Tests for the ``delete_message_from_app`` service, via device targeting."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.const import ATTR_SERVICE_DELETE_MSG, DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID

from ..conftest import setup_service_target


async def _delete(hass: HomeAssistant, device_id: str, message_id: str) -> None:
    await hass.services.async_call(DOMAIN, ATTR_SERVICE_DELETE_MSG, {"device_id": [device_id], "message_id": message_id}, blocking=True)


async def test_delete_message_happy_path(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    devices = await setup_service_target(hass, coordinator)

    with caplog.at_level(logging.ERROR):
        await _delete(hass, devices[DEFAULT_WUID], "msg-1")

    assert "Message cannot deleted" not in caplog.text


async def test_empty_message_id_after_strip_logs_warning_and_skips_delete(
    hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog
) -> None:
    devices = await setup_service_target(hass, coordinator)

    with caplog.at_level(logging.WARNING):
        await _delete(hass, devices[DEFAULT_WUID], "   ")

    assert "You must provide an ID" in caplog.text


async def test_delete_failure_logs_error_but_does_not_raise(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    graphql_operations,
    caplog,
) -> None:
    graphql_operations["DeleteChatMessage"] = {"data": {"deleteMsg": False}}
    devices = await setup_service_target(hass, coordinator)

    with caplog.at_level(logging.ERROR):
        await _delete(hass, devices[DEFAULT_WUID], "msg-1")

    assert "Message cannot deleted" in caplog.text
