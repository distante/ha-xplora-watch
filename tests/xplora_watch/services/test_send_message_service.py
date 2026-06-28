"""Tests for XploraMessageService.async_send_message."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.const import DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.exception_classes import ConnectionError as XploraConnectionError
from custom_components.xplora_watch.pyxplora_api.exception_classes import RateLimitError
from custom_components.xplora_watch.services import XploraMessageService
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _register_coordinator(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> str:
    entry_id = coordinator._entry.entry_id
    hass.data.setdefault(DOMAIN, {})[entry_id] = coordinator
    return entry_id


async def test_send_message_happy_path(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    entry_id = _register_coordinator(hass, coordinator)
    notify_service = XploraMessageService(hass, entry_id)

    with caplog.at_level(logging.ERROR):
        await notify_service.async_send_message("Hello watch", [DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "Message cannot send" not in caplog.text


async def test_empty_message_after_strip_logs_warning_and_skips_send(
    hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog
) -> None:
    entry_id = _register_coordinator(hass, coordinator)
    notify_service = XploraMessageService(hass, entry_id)

    with caplog.at_level(logging.WARNING):
        await notify_service.async_send_message("   ", [DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "Message is empty" in caplog.text


async def test_send_message_auth_error_logs_clean_and_does_not_raise(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    graphql_operations,
    caplog,
) -> None:
    # Token expires (E000004) during the send -> SendChatText raises AuthError. The service
    # must log a clean warning and return rather than propagate a raw traceback or re-login.
    graphql_operations["SendChatText"] = {"errors": [{"code": "E000004"}], "data": {"sendChatText": None}}
    entry_id = _register_coordinator(hass, coordinator)
    notify_service = XploraMessageService(hass, entry_id)

    with caplog.at_level(logging.WARNING):
        await notify_service.async_send_message("Hello watch", [DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "session token expired" in caplog.text
    assert "Message cannot send" not in caplog.text  # not misreported as a send failure


async def test_send_text_failure_logs_error_but_does_not_raise(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    graphql_operations,
    caplog,
) -> None:
    # sendText_a only returns False when the "sendChatText" key is present but None
    # (any non-None value, including False, is treated as success) -- see gql_handler_async.py.
    graphql_operations["SendChatText"] = {"data": {"sendChatText": None}}
    entry_id = _register_coordinator(hass, coordinator)
    notify_service = XploraMessageService(hass, entry_id)

    with caplog.at_level(logging.ERROR):
        await notify_service.async_send_message("Hello watch", [DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "Message cannot send" in caplog.text


@pytest.mark.parametrize(
    ("error", "expected_substring"),
    [
        (RateLimitError("429"), "rate limit"),
        (XploraConnectionError("boom"), "could not reach the Xplora server"),
    ],
)
async def test_send_message_transient_error_logs_clean_and_does_not_raise(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    caplog,
    error: Exception,
    expected_substring: str,
) -> None:
    # A 429 / connection drop during the send must surface as a clean per-type warning, not a raw
    # "Error executing service" traceback. (`RateLimitError`/`XploraConnectionError` bypass the
    # recovery gate -- only an expired token triggers a refresh -- so they reach the handler raw.)
    entry_id = _register_coordinator(hass, coordinator)
    coordinator.controller.sendText = AsyncMock(side_effect=error)  # type: ignore[method-assign]
    notify_service = XploraMessageService(hass, entry_id)

    with caplog.at_level(logging.WARNING):
        await notify_service.async_send_message("Hello watch", [DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert expected_substring in caplog.text
    assert "Message cannot send" not in caplog.text  # not misreported as a plain send failure
