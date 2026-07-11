"""Query-document assertions for the WATCH_Q GraphQL operations. Two concerns:

- ISSUE-13: the slimmed Chats/Alarms/SlientTimes query text no longer over-fetches.
- Wire-format parity (ref:XW-012, XW-017, XW-018): operation names, fragment names, variable
  set/order and field selection stay identical to the reference client for traffic fidelity.

Pure string checks on the query documents themselves -- no network needed.
"""

from __future__ import annotations

import re

import pytest

from custom_components.xplora_watch.pyxplora_api.gql_queries import WATCH_Q


def test_chats_query_drops_full_user_fragments_on_sender_receiver() -> None:
    query = WATCH_Q["chatsQ"]
    assert "SimpleUserFragment" not in query
    assert "ContactsFragment" not in query
    assert "ContactorFragment" not in query
    assert "sender {\n    __typename\n    id\n  }" in query
    assert "receiver {\n    __typename\n    id\n  }" in query


def test_alarms_query_drops_top_level_user_field() -> None:
    query = WATCH_Q["alarmsQ"]
    assert "fragment WatchAlarmFragment on WatchAlarm" in query
    alarm_fragment = query.split("fragment WatchAlarmFragment on WatchAlarm {")[1].split("\n}")[0]
    assert "user {" not in alarm_fragment
    # The nested Watch.user still needs WatchFragment/UserFragment -- only the alarm's own
    # top-level `user` field was dropped.
    assert "watch {" in alarm_fragment
    assert "fragment WatchFragment on Watch" in query


def test_silent_times_query_drops_watch_and_user_fields() -> None:
    query = WATCH_Q["silentTimesQ"]
    assert "watch {" not in query
    assert "user {" not in query
    # Nothing else references WatchFragment/UserFragment for this op anymore.
    assert "fragment WatchFragment" not in query
    assert "fragment UserFragment" not in query


def test_device_list_query_is_not_slimmed() -> None:
    """ISSUE-13 explicitly does not apply to deviceList -- it over-fetches the full XpUser
    fragment in the live app too, so the library matches it verbatim.
    """
    query = WATCH_Q["deviceListQ"]
    assert "fragment XpUser on User" in query
    assert "fragment XpDevice on WatchListItem" in query


# --- Wire-format parity: each query's operation name, fragment names, variable set, field
#     selection AND fragment-definition order are what go on the wire, so they are pinned here to
#     stay identical to the reference client for traffic fidelity (ref:XW-012). deviceList is the
#     already-matching model these were aligned to.


def _fragment_defs(doc: str) -> list[str]:
    return re.findall(r"fragment (\w+) on ", doc)


def _fragment_spreads(doc: str) -> set[str]:
    return set(re.findall(r"\.\.\.(\w+)", doc))


@pytest.mark.parametrize("key", sorted(WATCH_Q))
def test_every_watch_query_has_resolvable_fragments(key: str) -> None:
    """Structural invariant across all WATCH_Q docs: every `...Frag` spread resolves to a
    defined fragment, no fragment is defined-but-never-spread, and braces balance. Guards the
    hand-authored fragment swaps below against a dangling reference or an orphaned leftover.
    """
    doc = WATCH_Q[key]
    defined, spread = set(_fragment_defs(doc)), _fragment_spreads(doc)
    assert spread - defined == set(), f"{key}: spreads with no definition: {sorted(spread - defined)}"
    assert defined - spread == set(), f"{key}: fragments defined but never spread: {sorted(defined - spread)}"
    assert doc.count("{") == doc.count("}"), f"{key}: unbalanced braces"


def test_watch_last_locate_uses_xplocation_fragment() -> None:
    """The location fragment is named `XpLocation` (as in deviceList), not the library-invented
    `WatchLastLocateFragment`. Op name stays `WatchLastLocate`.
    """
    query = WATCH_Q["locateQ"]
    assert "query WatchLastLocate(" in query
    assert "...XpLocation" in query
    assert "fragment XpLocation on Location" in query
    assert "WatchLastLocateFragment" not in query


def test_ask_watch_locate_operation_name_is_lowercase() -> None:
    """The `askWatchLocate` operation name is lowercase-initial on the wire, not `AskWatchLocate`
    (ref:XW-012)."""
    query = WATCH_Q["askLocateQ"]
    assert "query askWatchLocate(" in query
    assert "query AskWatchLocate(" not in query


def test_alarms_query_watch_user_uses_xpuser_fragment() -> None:
    """The alarm's nested `Watch.user` resolves to the flat `XpUser`, not `UserFragment` with its
    `children`/`contacts` over-fetch. Fragments are defined in the reference-client order.
    """
    query = WATCH_Q["alarmsQ"]
    assert "fragment XpUser on User" in query
    # WatchFragment.user must point at XpUser now.
    watch_fragment = query.split("fragment WatchFragment on Watch {")[1].split("\n}")[0]
    assert "...XpUser" in watch_fragment
    # The nested-user over-fetch fragments are gone.
    assert "SimpleUserFragment" not in query
    assert "ContactsFragment" not in query
    assert "ContactorFragment" not in query
    assert "children {" not in query
    assert _fragment_defs(query) == [
        "WatchGroupFragment",
        "VendorFragment",
        "XpStepsInfo",
        "XpFileInfo",
        "XpFile",
        "XpGoPlayProfile",
        "XpPremiumFlags",
        "XpUser",
        "WatchFragment",
        "WatchAlarmFragment",
    ]


def test_safe_zones_query_slims_user_fragments() -> None:
    """SafeZone's nested `Watch.user` uses `XpUser`; the top-level `SafeZone.user` uses a lean
    `UserFragment` (no `file`/`children`); the deep SimpleUser/Contacts over-fetch is gone.
    Fragments are defined in the reference-client order.
    """
    query = WATCH_Q["safeZonesQ"]
    assert "fragment XpUser on User" in query
    watch_fragment = query.split("fragment WatchFragment on Watch {")[1].split("\n}")[0]
    assert "...XpUser" in watch_fragment
    lean_user = query.split("fragment UserFragment on User {")[1].split("\n}")[0]
    assert "file {" not in lean_user
    assert "children {" not in lean_user
    assert "SimpleUserFragment" not in query
    assert "ContactsFragment" not in query
    assert "ContactorFragment" not in query
    assert _fragment_defs(query) == [
        "WatchGroupFragment",
        "VendorFragment",
        "XpStepsInfo",
        "XpFileInfo",
        "XpFile",
        "XpGoPlayProfile",
        "XpPremiumFlags",
        "XpUser",
        "WatchFragment",
        "UserFragment",
        "SafeZoneGroupFragment",
        "XpSafeZone",
    ]


def test_safe_zones_query_uses_xpsafezone_fragment() -> None:
    """The top-level SafeZone fragment is named `XpSafeZone` (as in the reference client), not the
    library-invented `SafeZoneFragment`. Fragment names go on the wire, so this pins the spelling
    for traffic fidelity (ref:XW-017). Op name stays `SafeZones`.
    """
    query = WATCH_Q["safeZonesQ"]
    assert "query SafeZones(" in query
    assert "...XpSafeZone" in query
    assert "fragment XpSafeZone on SafeZone" in query
    assert "SafeZoneFragment" not in query


def test_chats_query_matches_reference_wire_shape() -> None:
    """The `Chats` request mirrors the reference client on the wire (ref:XW-018): the variable
    declaration order and the `chatsNew(...)` argument order are `uid, msgId, offset, limit, isNew`,
    the optional `$isNew: Boolean` variable is declared, and `remainingMsgs` is selected. Argument
    order and variable set go on the wire, so they are pinned for traffic fidelity.
    """
    query = WATCH_Q["chatsQ"]
    assert "query Chats($uid: String!, $msgId: String, $offset: Int, $limit: Int, $isNew: Boolean)" in query
    assert "chatsNew(uid: $uid, msgId: $msgId, offset: $offset, limit: $limit, isNew: $isNew)" in query
    assert "remainingMsgs" in query


def test_contacts_query_matches_reference_wire_shape() -> None:
    """The `Contacts` request mirrors the reference client on the wire (ref:XW-019): lowercase
    operation name `contacts`, the `$watchId` variable, and the `Xp*`/`XPContact`/`XPContactList`
    fragment set (no SimpleUser/Contacts/Contactor over-fetch). Operation name, variable set and
    fragment names go on the wire, so they are pinned for traffic fidelity.
    """
    query = WATCH_Q["contactsQ"]
    assert "query contacts($uid: String, $watchId: String)" in query
    assert "contacts(uid: $uid, watchId: $watchId)" in query
    assert "...XPContactList" in query
    assert "fragment XPContactList on XPContactList" in query
    assert "fragment XPContact on XPContact" in query
    assert "fragment XpUser on User" in query
    assert "query Contacts(" not in query
    for gone in (
        "SimpleUserFragment",
        "ContactsFragment",
        "ContactorFragment",
        "ContactListFragment",
        "ContactFragment",
        "FollowRequestFragment",
    ):
        assert gone not in query, f"stale fragment {gone!r} still present"
