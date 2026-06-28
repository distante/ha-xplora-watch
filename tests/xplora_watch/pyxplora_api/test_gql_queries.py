"""ISSUE-13: assert the slimmed Chats/Alarms/SlientTimes query text no longer over-fetches.

Pure string checks on the query documents themselves -- no network needed.
"""

from __future__ import annotations

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
