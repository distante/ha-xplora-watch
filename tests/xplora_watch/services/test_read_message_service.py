"""Tests for XploraMessageSensorUpdateService.async_read_message."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.const import DOMAIN, SENSOR_MESSAGE
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.services import XploraMessageSensorUpdateService
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_USER_ID, DEFAULT_WUID


def _register_coordinator(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> str:
    entry_id = coordinator._entry.entry_id
    hass.data.setdefault(DOMAIN, {})[entry_id] = coordinator
    return entry_id


def _make_raw_chat(msg_id: str, chat_type: str) -> dict:
    """Build a raw chat dict matching SimpleChat's expected JSON shape (see model.py)."""
    return {
        "id": f"id-{msg_id}",
        "msgId": msg_id,
        "readFlag": 0,
        "sender": {
            "id": DEFAULT_WUID,
            "userId": DEFAULT_WUID,
            "name": "Kid One",
            "phoneNumber": "+491700000001",
        },
        "receiver": {
            "id": DEFAULT_USER_ID,
            "userId": DEFAULT_USER_ID,
            "name": "Parent Name",
            "phoneNumber": "+491700000002",
        },
        "data": {
            "text": f"payload for {chat_type}",
            "sender_name": "Kid One",
            "delete_flag": 0,
            # emoticon_id must be a real Emoji enum member suffix (Emoji[f"M{emoticon_id}"]); "1001" -> Emoji.M1001.
            "emoticon_id": "1001",
        },
        "create": 1700000000,
        "type": chat_type,
    }


def _set_chats_fixture(graphql_operations: dict, chat_types: list[str]) -> None:
    chats = [_make_raw_chat(f"msg-{i}", chat_type) for i, chat_type in enumerate(chat_types)]
    graphql_operations["Chats"] = {"data": {"chatsNew": {"offset": 0, "limit": 10, "list": chats}}}
    # getWatchChatsRaw()'s with_emoji_id post-processing calls set_read_chat_msg() per chat,
    # which is a *separate* GraphQL operation not in the shared DEFAULT_OPERATION_PAYLOADS.
    graphql_operations["setReadChatMsg"] = {"data": {"setReadChatMsg": {}}}


async def test_dispatches_voice_short_video_and_image_handlers_once_each(
    hass: HomeAssistant, coordinator_with_data: XploraDataUpdateCoordinator, graphql_operations
) -> None:
    _set_chats_fixture(graphql_operations, ["VOICE", "SHORT_VIDEO", "IMAGE"])
    entry_id = _register_coordinator(hass, coordinator_with_data)
    read_service = XploraMessageSensorUpdateService(hass, entry_id)

    with (
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_voice", new=AsyncMock()) as mock_voice,
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_short_video", new=AsyncMock()) as mock_video,
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_image", new=AsyncMock()) as mock_image,
    ):
        await read_service.async_read_message([DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    mock_voice.assert_awaited_once_with(DEFAULT_WUID, "msg-0")
    mock_video.assert_awaited_once_with(DEFAULT_WUID, "msg-1")
    mock_image.assert_awaited_once_with(DEFAULT_WUID, "msg-2")


async def test_existing_watch_entry_in_old_state_gets_updated_with_new_messages(
    hass: HomeAssistant, coordinator_with_data: XploraDataUpdateCoordinator, graphql_operations
) -> None:
    _set_chats_fixture(graphql_operations, ["VOICE"])
    entry_id = _register_coordinator(hass, coordinator_with_data)
    read_service = XploraMessageSensorUpdateService(hass, entry_id)

    # coordinator_with_data already populated coordinator.data[DEFAULT_WUID] via a full refresh.
    assert DEFAULT_WUID in coordinator_with_data.data

    with (
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_voice", new=AsyncMock()),
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_short_video", new=AsyncMock()),
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_image", new=AsyncMock()),
    ):
        await read_service.async_read_message([DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    updated_messages = coordinator_with_data.data[DEFAULT_WUID][SENSOR_MESSAGE]
    assert updated_messages["list"][0]["msgId"] == "msg-0"


async def test_watch_not_already_in_old_state_still_gets_an_entry_via_message_data(
    hass: HomeAssistant, coordinator_with_data: XploraDataUpdateCoordinator, graphql_operations
) -> None:
    """coordinator.message_data() unconditionally does self.data = watch_entry (see coordinator.py),

    so a previously-unseen watch DOES end up in coordinator.data after async_read_message --
    the `if new_data_msg:` guard inside services.py only governs whether *that* function's own
    old_state.update() call additionally merges in SENSOR_MESSAGE on top of an existing entry;
    it cannot prevent the entry from being created in the first place.
    """
    other_watch = "watch-id-002"
    _set_chats_fixture(graphql_operations, ["VOICE"])
    entry_id = _register_coordinator(hass, coordinator_with_data)
    read_service = XploraMessageSensorUpdateService(hass, entry_id)

    assert other_watch not in coordinator_with_data.data

    with (
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_voice", new=AsyncMock()),
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_short_video", new=AsyncMock()),
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_image", new=AsyncMock()),
    ):
        await read_service.async_read_message([other_watch], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert other_watch in coordinator_with_data.data
    assert coordinator_with_data.data[other_watch][SENSOR_MESSAGE]["list"][0]["msgId"] == "msg-0"


async def test_fetch_chat_image_skips_remote_when_cached(hass: HomeAssistant, coordinator_with_data: XploraDataUpdateCoordinator) -> None:
    """A cached attachment must NOT trigger a fresh (rate-limited) remote download."""
    entry_id = _register_coordinator(hass, coordinator_with_data)
    read_service = XploraMessageSensorUpdateService(hass, entry_id)
    read_service.coordinator = coordinator_with_data

    with (
        patch("custom_components.xplora_watch.services.chat_media_cached", return_value=True),
        patch.object(coordinator_with_data.controller, "get_chat_image", new=AsyncMock()) as mock_get,
    ):
        await read_service._fetch_chat_image(DEFAULT_WUID, "msg-cached")

    mock_get.assert_not_awaited()


async def test_fetch_chat_image_downloads_when_not_cached(hass: HomeAssistant, coordinator_with_data: XploraDataUpdateCoordinator) -> None:
    """A cache miss still fetches from the watch and writes the file."""
    entry_id = _register_coordinator(hass, coordinator_with_data)
    read_service = XploraMessageSensorUpdateService(hass, entry_id)
    read_service.coordinator = coordinator_with_data

    with (
        patch("custom_components.xplora_watch.services.chat_media_cached", return_value=False),
        patch.object(coordinator_with_data.controller, "get_chat_image", new=AsyncMock(return_value="base64==")) as mock_get,
        patch("custom_components.xplora_watch.services.encoded_base64_string_to_file") as mock_write,
    ):
        await read_service._fetch_chat_image(DEFAULT_WUID, "msg-new")

    mock_get.assert_awaited_once_with(DEFAULT_WUID, "msg-new")
    mock_write.assert_called_once()


async def test_fetch_chat_short_video_skips_only_when_both_files_cached(
    hass: HomeAssistant, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """The video skip requires BOTH the video and its thumbnail to be cached; a missing thumb
    still re-fetches."""
    entry_id = _register_coordinator(hass, coordinator_with_data)
    read_service = XploraMessageSensorUpdateService(hass, entry_id)
    read_service.coordinator = coordinator_with_data

    # Video cached, thumbnail missing -> must NOT skip.
    def _only_video_cached(_hass, _name, file_type, file_dir) -> bool:
        return file_dir == "video" and file_type == "mp4"

    with (
        patch("custom_components.xplora_watch.services.chat_media_cached", side_effect=_only_video_cached),
        patch.object(coordinator_with_data.controller, "get_short_video", new=AsyncMock(return_value=None)) as mock_video,
        patch.object(coordinator_with_data.controller, "get_short_video_cover", new=AsyncMock(return_value=None)) as mock_cover,
    ):
        await read_service._fetch_chat_short_video(DEFAULT_WUID, "msg-partial")

    mock_video.assert_awaited_once()
    mock_cover.assert_awaited_once()


async def test_non_list_targets_logs_warning_and_returns_early(
    hass: HomeAssistant, coordinator_with_data: XploraDataUpdateCoordinator, caplog
) -> None:
    entry_id = _register_coordinator(hass, coordinator_with_data)
    read_service = XploraMessageSensorUpdateService(hass, entry_id)

    with caplog.at_level(logging.WARNING):
        await read_service.async_read_message(None, kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "No watch id or type" in caplog.text
