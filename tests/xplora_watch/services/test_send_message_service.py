"""Tests for the ``send_message`` service, via device targeting."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.const import ATTR_SERVICE_SEND_MSG, DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.exception_classes import ConnectionError as XploraConnectionError
from custom_components.xplora_watch.pyxplora_api.exception_classes import RateLimitError
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID

from ..conftest import setup_service_target


async def _send(hass: HomeAssistant, device_id: str, message: str) -> None:
    await hass.services.async_call(DOMAIN, ATTR_SERVICE_SEND_MSG, {"device_id": [device_id], "message": message}, blocking=True)


async def test_send_message_happy_path(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    devices = await setup_service_target(hass, coordinator)

    with caplog.at_level(logging.ERROR):
        await _send(hass, devices[DEFAULT_WUID], "Hello watch")

    assert "Message cannot send" not in caplog.text


async def test_empty_message_after_strip_logs_warning_and_skips_send(
    hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog
) -> None:
    devices = await setup_service_target(hass, coordinator)

    with caplog.at_level(logging.WARNING):
        await _send(hass, devices[DEFAULT_WUID], "   ")

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
    devices = await setup_service_target(hass, coordinator)

    with caplog.at_level(logging.WARNING):
        await _send(hass, devices[DEFAULT_WUID], "Hello watch")

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
    devices = await setup_service_target(hass, coordinator)

    with caplog.at_level(logging.ERROR):
        await _send(hass, devices[DEFAULT_WUID], "Hello watch")

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
    devices = await setup_service_target(hass, coordinator)
    coordinator.controller.sendText = AsyncMock(side_effect=error)  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING):
        await _send(hass, devices[DEFAULT_WUID], "Hello watch")

    assert expected_substring in caplog.text
    assert "Message cannot send" not in caplog.text  # not misreported as a plain send failure
