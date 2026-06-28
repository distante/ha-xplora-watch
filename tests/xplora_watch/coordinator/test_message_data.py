"""Tests for XploraDataUpdateCoordinator.message_data()."""

from __future__ import annotations

from custom_components.xplora_watch.const import SENSOR_MESSAGE
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


async def test_message_data_returns_raw_chats(coordinator: XploraDataUpdateCoordinator) -> None:
    """message_data() returns the raw chats dict matching the Chats operationName fixture."""
    res_chats = await coordinator.message_data(DEFAULT_WUID, message_limit=10, remove_message=False)

    assert res_chats == {"list": []}


async def test_message_data_leaves_other_wuids_untouched(
    coordinator: XploraDataUpdateCoordinator,
) -> None:
    """message_data() replaces the target wuid's entry with {message: chats} but leaves other wuids untouched."""
    coordinator.data = {
        DEFAULT_WUID: {"battery": 80, "isOnline": True},
        "other-wuid": {"battery": 50},
    }

    await coordinator.message_data(DEFAULT_WUID, message_limit=10, remove_message=False)

    assert coordinator.data["other-wuid"] == {"battery": 50}
    assert coordinator.data[DEFAULT_WUID] == {SENSOR_MESSAGE: {"list": []}}
