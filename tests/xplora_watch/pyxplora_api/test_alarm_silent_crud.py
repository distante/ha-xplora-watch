"""Unit tests for the alarm / silent-time CRUD wrappers added to PyXploraApi.

These exercise the controller methods directly with a mocked GraphQL handler (no network), so we
can assert the right handler method is called with the right arguments and that the boolean result
is derived from the handler's response envelope.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.xplora_watch.pyxplora_api.pyxplora_api_async import PyXploraApi
from custom_components.xplora_watch.pyxplora_api.status import NormalStatus


def _make_api() -> PyXploraApi:
    """A bare PyXploraApi with its GraphQL handler replaced by an AsyncMock (no network)."""
    api = PyXploraApi()
    api._gql_handler = AsyncMock()
    api.retryDelay = 0  # no backoff sleeps in tests
    return api


async def test_add_alarm_time_forwards_args_and_mirrors_start() -> None:
    api = _make_api()
    api._gql_handler.addAlarmTime_a = AsyncMock(return_value={"addAlarm": {"id": "a1"}})

    ok = await api.addAlarmTime("wuid-1", 480, "0111110", "Wake")

    assert ok is True
    # `start` mirrors `occurMin` (480) for a point-in-time alarm; `end` defaults to None.
    api._gql_handler.addAlarmTime_a.assert_awaited_once_with("wuid-1", 480, 480, "0111110", "Wake", None)


async def test_modify_alarm_time_passes_status_value() -> None:
    api = _make_api()
    api._gql_handler.modifyAlarmTime_a = AsyncMock(return_value={"modifyAlarm": True})

    ok = await api.modifyAlarmTime("a1", occur_min=540, week_repeat="1000001", name="X", status=NormalStatus.DISABLE)

    assert ok is True
    # occur_min and start are kept in sync; status is forwarded as its string value.
    api._gql_handler.modifyAlarmTime_a.assert_awaited_once_with("a1", 540, 540, "1000001", "X", "DISABLE")


async def test_remove_alarm_time() -> None:
    api = _make_api()
    api._gql_handler.removeAlarmTime_a = AsyncMock(return_value={"removeAlarm": True})

    assert await api.removeAlarmTime("a1") is True
    api._gql_handler.removeAlarmTime_a.assert_awaited_once_with("a1")


async def test_add_silent_time_forwards_args() -> None:
    api = _make_api()
    api._gql_handler.addSilentTime_a = AsyncMock(return_value={"addSilentTime": {"id": "s1"}})

    ok = await api.addSilentTime("wuid-1", 1320, 420, "0111110")

    assert ok is True
    api._gql_handler.addSilentTime_a.assert_awaited_once_with("wuid-1", 1320, 420, "0111110", "")


async def test_modify_silent_time() -> None:
    api = _make_api()
    api._gql_handler.modifySilentTime_a = AsyncMock(return_value={"modifySilentTime": True})

    assert await api.modifySilentTime("s1", start=1320, end=420, week_repeat="1111111") is True
    api._gql_handler.modifySilentTime_a.assert_awaited_once_with("s1", 1320, 420, "1111111")


async def test_remove_silent_time() -> None:
    api = _make_api()
    api._gql_handler.removeSilentTime_a = AsyncMock(return_value={"removeSilentTime": True})

    assert await api.removeSilentTime("s1") is True
    api._gql_handler.removeSilentTime_a.assert_awaited_once_with("s1")


@pytest.mark.parametrize("response", [{}, {"removeAlarm": False}, {"removeAlarm": 0}])
async def test_remove_alarm_returns_false_when_server_does_not_confirm(response: dict) -> None:
    api = _make_api()
    api.maxRetries = 0  # keep the retry loop short for the falsy path
    api._gql_handler.removeAlarmTime_a = AsyncMock(return_value=response)

    assert await api.removeAlarmTime("a1") is False
