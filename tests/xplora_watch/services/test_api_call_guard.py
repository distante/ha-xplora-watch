"""Unit tests for the shared `XploraService._api_call_guard` context manager.

This is the single choke-point every service handler wraps its controller-call body in: it catches
the recoverable API errors (`AuthError` after the coordinator's bounded recovery is exhausted, plus
`RateLimitError` / `XploraConnectionError` which bypass the recovery gate), logs each once via
`_log_api_error`, and flags `guard.failed` so a per-watch loop can stop after the first failure
(ban defense) -- while leaving the break/return decision to the caller. The exception tuple lives in
exactly this one place, so these tests pin it precisely (an unrelated exception must NOT be caught).
"""

from __future__ import annotations

import logging

import pytest
from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.pyxplora_api.exception_classes import AuthError, RateLimitError
from custom_components.xplora_watch.pyxplora_api.exception_classes import ConnectionError as XploraConnectionError
from custom_components.xplora_watch.services import XploraService

_ENTRY_ID = "abcdef1234567890"


@pytest.mark.parametrize(
    ("error", "expected_substring"),
    [
        (RateLimitError("429"), "rate limit"),
        (XploraConnectionError("boom"), "could not reach the Xplora server"),
        (AuthError("E000004"), "session token expired"),
    ],
)
def test_guard_suppresses_and_flags_each_recoverable_error(hass: HomeAssistant, caplog, error: Exception, expected_substring: str) -> None:
    """Each recoverable API error is swallowed (does not escape the `with`), flips `guard.failed`,
    and is logged once with its per-type message via `_log_api_error`."""
    service = XploraService(hass, _ENTRY_ID)

    with caplog.at_level(logging.WARNING):
        with service._api_call_guard("Create alarm", _ENTRY_ID) as guard:
            raise error

    assert guard.failed is True  # caller can `break` / `return` on this
    assert "Create alarm" in caplog.text
    assert expected_substring in caplog.text


def test_guard_clean_pass_leaves_failed_false_and_logs_nothing(hass: HomeAssistant, caplog) -> None:
    """A body that completes without error leaves `failed` False and emits no warning."""
    service = XploraService(hass, _ENTRY_ID)

    with caplog.at_level(logging.WARNING):
        with service._api_call_guard("Create alarm", _ENTRY_ID) as guard:
            pass

    assert guard.failed is False
    assert caplog.text == ""


def test_guard_does_not_swallow_unrelated_exception(hass: HomeAssistant) -> None:
    """An exception outside the recoverable tuple (e.g. a programming bug) must propagate -- the
    guard narrows the catch to API errors only, it is not a blanket `except Exception`."""
    service = XploraService(hass, _ENTRY_ID)

    with pytest.raises(ValueError):
        with service._api_call_guard("Create alarm", _ENTRY_ID):
            raise ValueError("a real bug must not be hidden")
