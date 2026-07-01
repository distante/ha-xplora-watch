"""Tests for the ``send_message`` service, via device targeting."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

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
    # Token expires (E000004) during the send -> SendChatText raises AuthError. The service logs a
    # clean per-type warning (no raw traceback), and -- since nothing succeeded (best-effort fan-out,
    # ADR 0004) -- surfaces an error toast rather than reporting success.
    graphql_operations["SendChatText"] = {"errors": [{"code": "E000004"}], "data": {"sendChatText": None}}
    devices = await setup_service_target(hass, coordinator)

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceValidationError) as err:
        await _send(hass, devices[DEFAULT_WUID], "Hello watch")

    assert err.value.translation_key == "nothing_actioned"
    assert "session token expired" in caplog.text
    assert "Message cannot send" not in caplog.text  # not misreported as a send failure


async def test_send_text_failure_logs_error_and_raises_watch_offline(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    graphql_operations,
    caplog,
) -> None:
    # sendText_a only returns False when the "sendChatText" key is present but None
    # (any non-None value, including False, is treated as success) -- see gql_handler_async.py.
    # A refused send (the only target) achieves nothing, so it surfaces the homogeneous
    # `watch_offline` error toast in addition to the per-watch error log (ADR 0004).
    graphql_operations["SendChatText"] = {"data": {"sendChatText": None}}
    devices = await setup_service_target(hass, coordinator)

    with caplog.at_level(logging.ERROR), pytest.raises(ServiceValidationError) as err:
        await _send(hass, devices[DEFAULT_WUID], "Hello watch")

    assert err.value.translation_key == "watch_offline"
    assert "Message cannot send" in caplog.text


@pytest.mark.parametrize(
    ("error", "expected_substring"),
    [
        (RateLimitError("429"), "rate limit"),
        (XploraConnectionError("boom"), "could not reach the Xplora server"),
    ],
)
async def test_send_message_transient_error_logs_clean_and_raises(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    caplog,
    error: Exception,
    expected_substring: str,
) -> None:
    # A 429 / connection drop during the send surfaces a clean per-type warning (no raw traceback);
    # since nothing succeeded, it also raises an error toast rather than reporting success (ADR 0004).
    # (`RateLimitError`/`XploraConnectionError` bypass the recovery gate -- only an expired token
    # triggers a refresh -- so they reach the handler raw.)
    devices = await setup_service_target(hass, coordinator)
    coordinator.controller.sendText = AsyncMock(side_effect=error)  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceValidationError) as err:
        await _send(hass, devices[DEFAULT_WUID], "Hello watch")

    assert err.value.translation_key == "nothing_actioned"
    assert expected_substring in caplog.text
    assert "Message cannot send" not in caplog.text  # not misreported as a plain send failure
