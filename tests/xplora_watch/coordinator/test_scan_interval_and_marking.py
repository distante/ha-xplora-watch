"""Tests for the scan-interval presets/normalization and the auto-mark-read toggle plumbing."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.const import (
    CONF_COUNTRY_CODE,
    CONF_LANGUAGE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import (
    CONF_PHONENUMBER,
    CONF_TIMEZONE,
    CONF_USERLANG,
    DOMAIN,
    SCAN_INTERVAL_OFF,
    normalize_scan_interval,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.model import ChatsNew, Data, SimpleChat
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, SCAN_INTERVAL_OFF),
        (0, SCAN_INTERVAL_OFF),
        ("0", SCAN_INTERVAL_OFF),
        ("", SCAN_INTERVAL_OFF),
        ("abc", SCAN_INTERVAL_OFF),
        (-5, SCAN_INTERVAL_OFF),
        (1800, 1800),
        (3600, 3600),
        (7200, 7200),
        ("1800", 1800),
        # Legacy / unsafe free-form values snap to the nearest non-off preset (never silently
        # disabled, never faster than 30 min).
        (180, 1800),
        (5, 1800),
        (2699, 1800),
        (99999, 7200),
    ],
)
def test_normalize_scan_interval(raw: Any, expected: int) -> None:
    assert normalize_scan_interval(raw) == expected


def _entry_with_scan(hass: HomeAssistant, value: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Xplora®",
        unique_id="+491700000001",
        data={
            CONF_COUNTRY_CODE: "+49",
            CONF_PHONENUMBER: "+491700000001",
            CONF_PASSWORD: "secret",
            CONF_USERLANG: "en-GB",
            CONF_TIMEZONE: "Europe/Berlin",
            CONF_LANGUAGE: "en",
        },
        options={CONF_SCAN_INTERVAL: value},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, None),  # OFF -> no recurring polling
        (None, None),
        (1800, timedelta(seconds=1800)),
        (3600, timedelta(seconds=3600)),
        (7200, timedelta(seconds=7200)),
        ("1800", timedelta(seconds=1800)),
        (180, timedelta(seconds=1800)),  # legacy value normalized up to the 30-min preset
    ],
)
async def test_update_interval_from_option(hass: HomeAssistant, value: Any, expected: timedelta | None) -> None:
    coord = XploraDataUpdateCoordinator(hass, _entry_with_scan(hass, value))
    assert coord.update_interval == expected


async def test_data_loop_passes_auto_mark_read_through(coordinator: XploraDataUpdateCoordinator, monkeypatch: pytest.MonkeyPatch) -> None:
    """data_loop forwards its auto_mark_read flag to the controller's getWatchChatsRaw."""
    captured: dict[str, Any] = {}

    async def _fake_chats(wuid: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"list": []}

    monkeypatch.setattr(coordinator.controller, "getWatchChatsRaw", _fake_chats)
    await coordinator.data_loop([DEFAULT_WUID], message_limit=10, remove_message=False, auto_mark_read=True)
    assert captured["mark_as_read"] is True


async def test_data_loop_defaults_to_not_marking_read(coordinator: XploraDataUpdateCoordinator, monkeypatch: pytest.MonkeyPatch) -> None:
    """With auto_mark_read unset, the read-receipt write is off by default."""
    captured: dict[str, Any] = {}

    async def _fake_chats(wuid: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"list": []}

    monkeypatch.setattr(coordinator.controller, "getWatchChatsRaw", _fake_chats)
    await coordinator.data_loop([DEFAULT_WUID], message_limit=10, remove_message=False)
    assert captured["mark_as_read"] is False


def _two_chats() -> ChatsNew:
    """One server-side unread message (readFlag 0) and one already-read (readFlag 1)."""
    return ChatsNew(
        [
            SimpleChat(id="row-unread", msgId="msg-unread", readFlag=0, data=Data()),
            SimpleChat(id="row-read", msgId="msg-read", readFlag=1, data=Data()),
        ]
    )


async def test_getwatchchatsraw_marks_only_unread_when_enabled(
    coordinator: XploraDataUpdateCoordinator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With mark_as_read on, a read receipt is sent only for messages still unread server-side.

    This is the actual ban-traffic fix: the old code re-marked the whole fetched window every
    poll. `readFlag` truthiness must gate the `set_read_chat_msg` write so re-fetching already-read
    history sends no mutations.
    """
    read_calls: list[str] = []

    async def _fake_chats_a(*_args: Any, **_kwargs: Any) -> ChatsNew:
        return _two_chats()

    async def _record_read(wuid: str, msg_id: str, chat_id: str) -> None:
        read_calls.append(msg_id)

    monkeypatch.setattr(coordinator.controller._gql_handler, "chats_a", _fake_chats_a)
    monkeypatch.setattr(coordinator.controller, "set_read_chat_msg", _record_read)

    # with_emoji_id off isolates the read-receipt path from the unrelated emoji translation.
    await coordinator.controller.getWatchChatsRaw(DEFAULT_WUID, mark_as_read=True, with_emoji_id=False)

    assert read_calls == ["msg-unread"]


async def test_getwatchchatsraw_marks_nothing_when_disabled(
    coordinator: XploraDataUpdateCoordinator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With mark_as_read off (the default), no read receipt is sent even for unread messages."""
    read_calls: list[str] = []

    async def _fake_chats_a(*_args: Any, **_kwargs: Any) -> ChatsNew:
        return _two_chats()

    async def _record_read(wuid: str, msg_id: str, chat_id: str) -> None:
        read_calls.append(msg_id)

    monkeypatch.setattr(coordinator.controller._gql_handler, "chats_a", _fake_chats_a)
    monkeypatch.setattr(coordinator.controller, "set_read_chat_msg", _record_read)

    await coordinator.controller.getWatchChatsRaw(DEFAULT_WUID, mark_as_read=False, with_emoji_id=False)

    assert read_calls == []
