"""Tests for the ``read_message`` service, via device targeting.

The attachment-fetch helpers (`_fetch_chat_*`) are unit-tested directly on the service instance;
the dispatch + state-merge behavior is driven end-to-end through `hass.services.async_call`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.xplora_watch.const import ATTR_SERVICE_READ_MSG, DOMAIN, SENSOR_MESSAGE
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.services import XploraMessageSensorUpdateService
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_USER_ID, DEFAULT_WUID

from ..conftest import setup_service_target


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
    devices = await setup_service_target(hass, coordinator_with_data)

    with (
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_voice", new=AsyncMock()) as mock_voice,
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_short_video", new=AsyncMock()) as mock_video,
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_image", new=AsyncMock()) as mock_image,
    ):
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_READ_MSG, {"device_id": [devices[DEFAULT_WUID]]}, blocking=True)

    # The media-fetch helpers now take the coordinator as a parameter (ADR 0004: no per-instance
    # `self.coordinator` state, so concurrent read_message calls for different accounts can't cross).
    mock_voice.assert_awaited_once_with(coordinator_with_data, DEFAULT_WUID, "msg-0")
    mock_video.assert_awaited_once_with(coordinator_with_data, DEFAULT_WUID, "msg-1")
    mock_image.assert_awaited_once_with(coordinator_with_data, DEFAULT_WUID, "msg-2")


async def test_existing_watch_entry_in_old_state_gets_updated_with_new_messages(
    hass: HomeAssistant, coordinator_with_data: XploraDataUpdateCoordinator, graphql_operations
) -> None:
    _set_chats_fixture(graphql_operations, ["VOICE"])
    devices = await setup_service_target(hass, coordinator_with_data)

    # coordinator_with_data already populated coordinator.data[DEFAULT_WUID] via a full refresh.
    assert DEFAULT_WUID in coordinator_with_data.data

    with (
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_voice", new=AsyncMock()),
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_short_video", new=AsyncMock()),
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_image", new=AsyncMock()),
    ):
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_READ_MSG, {"device_id": [devices[DEFAULT_WUID]]}, blocking=True)

    updated_messages = coordinator_with_data.data[DEFAULT_WUID][SENSOR_MESSAGE]
    assert updated_messages["list"][0]["msgId"] == "msg-0"


async def test_watch_not_already_in_old_state_still_gets_an_entry_via_message_data(
    hass: HomeAssistant, coordinator_with_data: XploraDataUpdateCoordinator, graphql_operations
) -> None:
    """coordinator.message_data() unconditionally does self.data = watch_entry (see coordinator.py),

    so a previously-unseen watch DOES end up in coordinator.data after read_message --
    the `if new_data_msg:` guard inside services.py only governs whether *that* function's own
    old_state.update() call additionally merges in SENSOR_MESSAGE on top of an existing entry;
    it cannot prevent the entry from being created in the first place.
    """
    other_watch = "watch-id-002"
    _set_chats_fixture(graphql_operations, ["VOICE"])
    # The account links a second watch (so its device resolves).
    coordinator_with_data.controller.getWatchUserIDs = lambda: [DEFAULT_WUID, other_watch]  # type: ignore[method-assign]
    devices = await setup_service_target(hass, coordinator_with_data, wuids=(other_watch,))

    assert other_watch not in coordinator_with_data.data

    with (
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_voice", new=AsyncMock()),
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_short_video", new=AsyncMock()),
        patch.object(XploraMessageSensorUpdateService, "_fetch_chat_image", new=AsyncMock()),
    ):
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_READ_MSG, {"device_id": [devices[other_watch]]}, blocking=True)

    assert other_watch in coordinator_with_data.data
    assert coordinator_with_data.data[other_watch][SENSOR_MESSAGE]["list"][0]["msgId"] == "msg-0"


async def test_fetch_chat_image_skips_remote_when_cached(hass: HomeAssistant, coordinator_with_data: XploraDataUpdateCoordinator) -> None:
    """A cached attachment must NOT trigger a fresh (rate-limited) remote download."""
    read_service = XploraMessageSensorUpdateService(hass, coordinator_with_data._entry.entry_id)

    with (
        patch("custom_components.xplora_watch.services.chat_media_cached", return_value=True),
        patch.object(coordinator_with_data.controller, "get_chat_image", new=AsyncMock()) as mock_get,
    ):
        await read_service._fetch_chat_image(coordinator_with_data, DEFAULT_WUID, "msg-cached")

    mock_get.assert_not_awaited()


async def test_fetch_chat_image_downloads_when_not_cached(hass: HomeAssistant, coordinator_with_data: XploraDataUpdateCoordinator) -> None:
    """A cache miss still fetches from the watch and writes the file."""
    read_service = XploraMessageSensorUpdateService(hass, coordinator_with_data._entry.entry_id)

    with (
        patch("custom_components.xplora_watch.services.chat_media_cached", return_value=False),
        patch.object(coordinator_with_data.controller, "get_chat_image", new=AsyncMock(return_value="base64==")) as mock_get,
        patch("custom_components.xplora_watch.services.encoded_base64_string_to_file") as mock_write,
    ):
        await read_service._fetch_chat_image(coordinator_with_data, DEFAULT_WUID, "msg-new")

    mock_get.assert_awaited_once_with(DEFAULT_WUID, "msg-new")
    mock_write.assert_called_once()


async def test_fetch_chat_short_video_skips_only_when_both_files_cached(
    hass: HomeAssistant, coordinator_with_data: XploraDataUpdateCoordinator
) -> None:
    """The video skip requires BOTH the video and its thumbnail to be cached; a missing thumb
    still re-fetches."""
    read_service = XploraMessageSensorUpdateService(hass, coordinator_with_data._entry.entry_id)

    # Video cached, thumbnail missing -> must NOT skip.
    def _only_video_cached(_hass, _name, file_type, file_dir) -> bool:
        return file_dir == "video" and file_type == "mp4"

    with (
        patch("custom_components.xplora_watch.services.chat_media_cached", side_effect=_only_video_cached),
        patch.object(coordinator_with_data.controller, "get_short_video", new=AsyncMock(return_value=None)) as mock_video,
        patch.object(coordinator_with_data.controller, "get_short_video_cover", new=AsyncMock(return_value=None)) as mock_cover,
    ):
        await read_service._fetch_chat_short_video(coordinator_with_data, DEFAULT_WUID, "msg-partial")

    mock_video.assert_awaited_once()
    mock_cover.assert_awaited_once()


async def test_no_device_target_raises(hass: HomeAssistant, coordinator_with_data: XploraDataUpdateCoordinator) -> None:
    await setup_service_target(hass, coordinator_with_data)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_READ_MSG, {"device_id": []}, blocking=True)
