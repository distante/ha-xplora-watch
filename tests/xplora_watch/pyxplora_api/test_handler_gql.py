"""Unit tests for HandlerGQL instance-level state and request-header signing.

These exercise `HandlerGQL` directly (no network, no coordinator) since the behaviors under
test -- per-instance `.errors` isolation and the auth-header signing rule -- are pure
construction/method-call concerns that don't need the full mocked-transport harness.
"""

from __future__ import annotations

from custom_components.xplora_watch.pyxplora_api.const import API_SECRET
from custom_components.xplora_watch.pyxplora_api.handler_gql import HandlerGQL


def _make_handler() -> HandlerGQL:
    return HandlerGQL(
        countryPhoneNumber="+49",
        phoneNumber="+491700000001",
        password="secret",
        userLang="en-GB",
        timeZone="Europe/Berlin",
    )


def test_errors_list_is_not_shared_between_instances() -> None:
    """ISSUE-6: `errors` used to be a class-level mutable list shared across every
    `HandlerGQL` (and thus every config entry / reconnect). Appending to one instance's
    list must not leak into another's.
    """
    handler_a = _make_handler()
    handler_b = _make_handler()

    handler_a.errors.append({"function": "test", "errors": ["boom"]})

    assert handler_a.errors == [{"function": "test", "errors": ["boom"]}]
    assert handler_b.errors == []


def test_instance_level_auth_state_defaults_to_none() -> None:
    """accessToken/sessionId/userId/issueToken are set in __init__, not shared class state."""
    handler = _make_handler()

    assert handler.accessToken is None
    assert handler.sessionId is None
    assert handler.userId is None
    assert handler.issueToken is None


def test_bearer_auth_signs_with_static_secret_even_when_w360_present() -> None:
    """ISSUE-9: requests always sign with `Bearer <token>:<static M2>` (ref:XW-005) and
    never reference `w360` -- a non-null `w360` block must not change the signing secret
    (the old code would overwrite `_API_KEY`/`_API_SECRET` with it, corrupting the session).
    """
    handler = _make_handler()
    handler.accessToken = "access-token-1"
    handler.issueToken = {
        "token": "access-token-1",
        "w360": {"token": "w360-token", "secret": "w360-secret", "qid": "qid-1"},
    }

    headers = handler.getRequestHeaders("application/json; charset=UTF-8")

    assert headers["H-BackDoor-Authorization"] == f"Bearer access-token-1:{API_SECRET}"
    assert handler._API_SECRET == API_SECRET
    assert handler._API_KEY != "w360-token"


def test_request_headers_include_accept_language() -> None:
    """ISSUE-8: every request includes `Accept-Language`; default en-GB."""
    handler = _make_handler()

    headers = handler.getRequestHeaders("application/json; charset=UTF-8")

    assert headers["Accept-Language"] == "en-GB"
