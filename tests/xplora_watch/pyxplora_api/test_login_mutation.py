"""The login (`signInWithEmailOrPhone`) request matches the reference client's wire format (ref:XW-014).

Two axes:
- **wire shape** -- the mutation document uses the lean ``...XpToken`` fragment set (defined in the
  reference-client order) and declares and passes a ``$clientId`` variable, dropping the hand-inlined
  ``children``/``roles``/``contacts`` over-fetch (a pure string check on the document + the handler's
  login variables);
- **behaviour** -- because the login response no longer carries ``user.children``, the watch list is
  sourced from the account-wide ``deviceList`` query instead. Driven end-to-end through the mocked
  transport so a login payload with NO children still yields the account's watches.
"""

from __future__ import annotations

from typing import Any

from custom_components.xplora_watch.pyxplora_api.gql_mutations import SIGN_M
from custom_components.xplora_watch.pyxplora_api.handler_gql import HandlerGQL
from custom_components.xplora_watch.pyxplora_api.pyxplora_api_async import PyXploraApi

from ..fixtures.graphql_payloads import make_device_list_payload

LOGIN = SIGN_M["signInWithEmailOrPhoneM"]


def test_login_mutation_declares_and_passes_client_id() -> None:
    assert "$clientId: String" in LOGIN
    assert "clientId: $clientId" in LOGIN


def test_login_mutation_uses_lean_xptoken_fragments() -> None:
    import re

    assert "...XpToken" in LOGIN
    for fragment in ("XpToken", "XpUser", "XpApp", "XpStepsInfo", "XpGoPlayProfile", "XpPremiumFlags"):
        assert f"fragment {fragment} on " in LOGIN
    # Fields the old hand-inlined selection under-fetched.
    for field in ("emailConsent", "isSSO", "goPlayProfile", "premiumFlags", "stepsInfo"):
        assert field in LOGIN
    # Fragments are defined leaf-first with the composite `XpToken` last -- the query document is
    # literal request bytes, so this order is part of the wire fingerprint (ref:XW-014).
    assert re.findall(r"fragment (\w+) on ", LOGIN) == [
        "XpStepsInfo",
        "XpFileInfo",
        "XpFile",
        "XpGoPlayProfile",
        "XpPremiumFlags",
        "XpUser",
        "XpApp",
        "XpToken",
    ]


def test_login_mutation_drops_the_over_fetch() -> None:
    """The hand-inlined `children`/`roles`/`contacts`/address over-fetch is gone; the watch list is
    sourced from `deviceList` instead (ref:XW-014)."""
    for gone in ("children", "roles", "contacts", "SimpleUserFragment", "faxNumber", "loginFailCount", "changePasswordDate"):
        assert gone not in LOGIN, f"login still over-fetches {gone!r}"


def test_login_variables_include_client_id() -> None:
    handler = HandlerGQL("+49", "+491700000001", "secret", "en-GB", "UTC")
    assert "clientId" in handler.variables


def _lean_login_payload(user_id: str = "account-1") -> dict[str, Any]:
    """A login response in the lean shape: a `user` with NO `children` block."""
    return {
        "signInWithEmailOrPhone": {
            "id": "session-1",
            "token": "access-1",
            "refreshToken": "refresh-1",
            "expireDate": 9999999999,
            "user": {"id": user_id, "name": "Parent"},
        }
    }


async def test_watch_list_is_sourced_from_device_list_not_login_children(
    graphql_operations: dict[str, dict[str, Any]],
    mock_graphql: Any,
) -> None:
    """With a login payload carrying no `children`, the account's watch still resolves -- proving
    the watch list comes from `deviceList` (`user.id`), not the login response."""
    graphql_operations["signInWithEmailOrPhone"] = {"data": _lean_login_payload()}
    graphql_operations["deviceList"] = {"data": make_device_list_payload(wuid="ward-from-device-list")}

    controller = PyXploraApi(email="parent@example.com", password="secret", userLang="en-GB", timeZone="UTC")
    controller.maxRetries = -1
    await controller.init()

    assert controller.getWatchUserIDs() == ["ward-from-device-list"]
    # The account (guardian) user still comes from the lean login response.
    assert controller.getUserName() == "Parent"


async def test_child_phone_number_filter_applies_to_device_list_watches(
    graphql_operations: dict[str, dict[str, Any]],
    mock_graphql: Any,
) -> None:
    """The optional `childPhoneNumber` filter still narrows the (now deviceList-sourced) watch list."""
    graphql_operations["signInWithEmailOrPhone"] = {"data": _lean_login_payload()}
    graphql_operations["deviceList"] = {"data": make_device_list_payload(wuid="ward-1", ward_phone="+491700000001")}

    keep = PyXploraApi(email="p@e.com", password="s", userLang="en", timeZone="UTC", childPhoneNumber=["+491700000001"])
    keep.maxRetries = -1
    await keep.init()
    assert keep.getWatchUserIDs() == ["ward-1"]

    graphql_operations["deviceList"] = {"data": make_device_list_payload(wuid="ward-1", ward_phone="+490000000000")}
    drop = PyXploraApi(email="p@e.com", password="s", userLang="en", timeZone="UTC", childPhoneNumber=["+491700000001"])
    drop.maxRetries = -1
    await drop.init()
    assert drop.getWatchUserIDs() == []
