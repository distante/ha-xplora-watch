"""Unit tests for `PyXploraApi.dump_session` / `restore_session`.

The Xplora token is valid ~35 days but held only in memory, so a restart otherwise re-logs in.
`dump_session` serializes the `signIn` blob and `restore_session` reconstructs the exact state a
login would set (so a restored, still-valid token lets `init(forceLogin=False)` skip the network).
These tests exercise the (network-free) state handling directly.
"""

from __future__ import annotations

from typing import Any

from custom_components.xplora_watch.pyxplora_api.pyxplora_api_async import PyXploraApi


def _logged_in_controller() -> PyXploraApi:
    """A controller carrying an in-memory session, as if `login_a` had just run."""
    controller = PyXploraApi(countrycode="+49", phoneNumber="+491700000001", password="secret", userLang="en-GB", timeZone="UTC")
    issue = {
        "id": "session-id-1",
        "token": "access-token-1",
        "refreshToken": "refresh-token-1",
        "expireDate": 9999999999,  # far future -> not expired
        "user": {"id": "user-id-1", "name": "Parent", "children": [{"ward": {"id": "wuid-1"}}]},
    }
    controller._issueToken = issue
    controller._refresh_token = issue["refreshToken"]
    controller.dtIssueToken = 1_700_000_000
    controller._gql_handler.issueToken = issue
    controller._gql_handler.accessToken = issue["token"]
    return controller


def test_dump_session_round_trips_into_restore() -> None:
    src = _logged_in_controller()
    blob = src.dump_session()

    assert blob["issue_token"]["token"] == "access-token-1"
    assert blob["dt_issue_token"] == 1_700_000_000

    dst = PyXploraApi(countrycode="+49", phoneNumber="+491700000001", password="secret", userLang="en-GB", timeZone="UTC")
    assert dst.restore_session(blob) is True

    # Restored state mirrors exactly what a login sets, so the connection gate passes.
    assert dst._isConnected() is True
    assert dst._hasTokenExpired() is False
    assert dst._refresh_token == "refresh-token-1"
    assert dst._gql_handler.accessToken == "access-token-1"
    assert dst._gql_handler.userId == "user-id-1"
    assert dst._gql_handler.sessionId == "session-id-1"


def test_dump_session_empty_when_not_connected() -> None:
    controller = PyXploraApi(countrycode="+49", phoneNumber="+491700000001", password="secret", userLang="en-GB", timeZone="UTC")
    # No token yet -> nothing worth persisting.
    assert controller.dump_session() == {}


def test_restore_session_rejects_corrupt_blobs() -> None:
    controller = PyXploraApi(countrycode="+49", phoneNumber="+491700000001", password="secret", userLang="en-GB", timeZone="UTC")

    bad_blobs: list[Any] = [
        None,
        {},
        "not-a-dict",
        {"issue_token": None},
        {"issue_token": {"token": "t"}},  # missing user
        {"issue_token": {"user": {"id": "x"}}},  # missing token
    ]
    for blob in bad_blobs:
        assert controller.restore_session(blob) is False, blob
        assert controller._isConnected() is False
