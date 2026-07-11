"""Unit tests for the alarm / silent-time CRUD wrappers on PyXploraApi.

These exercise the public controller methods with a mocked GraphQL handler (no network), so we
can assert the right handler method is called with the right arguments and that the wrapper
coerces the handler's return to the ``bool`` its callers expect.

Since ADR 0010 the handler ``*_a`` methods return the response's *single value* directly (a bool,
or the ``{ id, ... }`` object for a create, or ``None`` for an empty/failed response) -- there is
no response-field key for the wrapper to read. The positional read contract itself (single value
/ empty / impossible multi-key shape) is covered in ``test_watch_commands.py`` where it lives, on
``GQLHandler.run_command``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.xplora_watch.pyxplora_api.exception_classes import ConnectionError as XploraConnectionError
from custom_components.xplora_watch.pyxplora_api.exception_classes import XploraProtocolError
from custom_components.xplora_watch.pyxplora_api.pyxplora_api_async import PyXploraApi
from custom_components.xplora_watch.pyxplora_api.status import NormalStatus
from custom_components.xplora_watch.pyxplora_api.watch_commands import WatchCommand


def _make_api() -> PyXploraApi:
    """A bare PyXploraApi with its GraphQL handler replaced by an AsyncMock (no network)."""
    api = PyXploraApi()
    api._gql_handler = AsyncMock()
    api.retryDelay = 0  # no backoff sleeps in tests
    return api


async def test_add_alarm_time_forwards_args_and_mirrors_start() -> None:
    api = _make_api()
    # A create returns the new object; the wrapper reports success as a bool.
    api._gql_handler.addAlarmTime_a = AsyncMock(return_value={"id": "a1"})

    ok = await api.addAlarmTime("wuid-1", 480, "0111110", "Wake")

    assert ok is True
    # `start` mirrors `occurMin` (480) for a point-in-time alarm; `end` defaults to None.
    api._gql_handler.addAlarmTime_a.assert_awaited_once_with("wuid-1", 480, 480, "0111110", "Wake", None)


async def test_modify_alarm_time_passes_status_value() -> None:
    api = _make_api()
    api._gql_handler.modifyAlarmTime_a = AsyncMock(return_value=True)

    ok = await api.modifyAlarmTime("a1", occur_min=540, week_repeat="1000001", name="X", status=NormalStatus.DISABLE)

    assert ok is True
    # occur_min and start are kept in sync; status is forwarded as its string value.
    api._gql_handler.modifyAlarmTime_a.assert_awaited_once_with("a1", 540, 540, "1000001", "X", "DISABLE")


async def test_remove_alarm_time() -> None:
    api = _make_api()
    api._gql_handler.removeAlarmTime_a = AsyncMock(return_value=True)

    assert await api.removeAlarmTime("a1") is True
    api._gql_handler.removeAlarmTime_a.assert_awaited_once_with("a1")


async def test_add_silent_time_forwards_args() -> None:
    api = _make_api()
    api._gql_handler.addSilentTime_a = AsyncMock(return_value={"id": "s1"})

    ok = await api.addSilentTime("wuid-1", 1320, 420, "0111110")

    assert ok is True
    api._gql_handler.addSilentTime_a.assert_awaited_once_with("wuid-1", 1320, 420, "0111110", "")


async def test_modify_silent_time() -> None:
    api = _make_api()
    api._gql_handler.modifySilentTime_a = AsyncMock(return_value=True)

    assert await api.modifySilentTime("s1", start=1320, end=420, week_repeat="1111111") is True
    api._gql_handler.modifySilentTime_a.assert_awaited_once_with("s1", 1320, 420, "1111111")


async def test_remove_silent_time() -> None:
    api = _make_api()
    api._gql_handler.removeSilentTime_a = AsyncMock(return_value=True)

    assert await api.removeSilentTime("s1") is True
    api._gql_handler.removeSilentTime_a.assert_awaited_once_with("s1")


@pytest.mark.parametrize("value", [None, False, 0])
async def test_remove_alarm_returns_false_when_server_does_not_confirm(value: object) -> None:
    api = _make_api()
    api.maxRetries = 0  # keep the retry loop short for the falsy path
    api._gql_handler.removeAlarmTime_a = AsyncMock(return_value=value)

    assert await api.removeAlarmTime("a1") is False


@pytest.mark.parametrize("enabled", [True, False])
async def test_set_silent_enabled_reads_server_confirmation(enabled: bool) -> None:
    """Enable/disable both map to the ``setEnableSilentTime`` command, differing only by status.

    Regression guard for the transposed response key (``setEnableSlientTime``) that made every
    toggle read ``False`` and surface a bogus ``watch_offline`` even though the watch was reached
    and the change applied. Reading positionally (ADR 0010) removes that key entirely.
    """
    api = _make_api()
    api._gql_handler.setEnableSilentTime_a = AsyncMock(return_value=True)

    if enabled:
        assert await api.setEnableSilentTime("s1") is True
        api._gql_handler.setEnableSilentTime_a.assert_awaited_once_with("s1")
    else:
        assert await api.setDisableSilentTime("s1") is True
        api._gql_handler.setEnableSilentTime_a.assert_awaited_once_with("s1", NormalStatus.DISABLE.value)


@pytest.mark.parametrize("value", [None, False])
async def test_set_silent_enabled_returns_false_when_server_does_not_confirm(value: object) -> None:
    api = _make_api()
    api.maxRetries = 0  # keep the retry loop short for the falsy path
    api._gql_handler.setEnableSilentTime_a = AsyncMock(return_value=value)

    assert await api.setEnableSilentTime("s1") is False


async def test_protocol_error_propagates_through_wrapper_retry_loop() -> None:
    """A control-action wrapper must NOT swallow ``XploraProtocolError``.

    The wrapper's ``except Error`` retry loop catches recoverable failures, but the impossible
    multi-key response shape is a code bug / schema drift that must surface loudly (ADR 0010,
    gotcha #4) -- it must not be caught, retried, and returned as a falsy non-success that the
    service layer would then mislabel ``watch_offline``. This bites only because
    ``XploraProtocolError`` does not subclass ``Error``.
    """
    api = _make_api()
    api._gql_handler.setEnableSilentTime_a = AsyncMock(
        side_effect=XploraProtocolError(WatchCommand.SET_ENABLE_SILENT_TIME, {"a": 1, "b": 2})
    )

    with pytest.raises(XploraProtocolError):
        await api.setEnableSilentTime("s1")


async def test_set_alarm_enabled_fast_fails_on_server_refusal() -> None:
    """A server refusal is authoritative: the watch was reached and declined, so the wrapper returns
    False at once and does NOT retry. Retrying an action the server already declined is wasted traffic
    against a watch that is not going to change its answer (ban hygiene). The retry loop is reserved
    for connection/`Error` failures, which raise. Regression guard for the ADR 0010 refactor, which
    briefly dropped this wrapper's fast-fail so a refused alarm toggle retried maxRetries+2 times."""
    api = _make_api()
    api._gql_handler.setEnableAlarmTime_a = AsyncMock(return_value=False)

    assert await api.setEnableAlarmTime("a1") is False
    api._gql_handler.setEnableAlarmTime_a.assert_awaited_once()


async def test_set_alarm_enabled_retries_only_on_connection_error() -> None:
    """The retry loop is scoped to transport failures, not server answers: a connection error is
    retried, and once the server actually answers, that answer is returned. Confirms the authoritative-
    response / retry-on-connection split the fast-fail restore encodes."""
    api = _make_api()
    api._gql_handler.setEnableAlarmTime_a = AsyncMock(side_effect=[XploraConnectionError(), True])

    assert await api.setEnableAlarmTime("a1") is True
    assert api._gql_handler.setEnableAlarmTime_a.await_count == 2


# --- Teeth for the shared control-action policy helper `_run_control_action` (ADR 0013) ---
#
# The nine alarm/silent-time control-action wrappers all delegate to `_run_control_action`, so the
# fast-fail-on-any-answer / retry-only-on-raised-transport-error policy is proven once, here, against
# the helper directly with a fake action callable. The per-wrapper `assert_awaited_once_with(...)`
# tests above pin each wrapper's argument wiring and, by asserting a single await, that it delegates
# unchanged.


@pytest.mark.parametrize("returned", [False, None])
async def test_run_control_action_fast_fails_on_any_returned_value(returned: object) -> None:
    """Any value the action *returns* -- a ``False`` refusal or the ``None`` of an empty envelope -- is
    the server's authoritative answer: the helper coerces it to ``bool`` and returns at once, awaiting
    the action EXACTLY once (no retry). Retrying an action a reached watch already declined is wasted
    traffic against a device that will keep saying no (ban hygiene, ADR 0013). ``maxRetries`` is left at
    its default (3) here so a regression to retry-on-falsy would await up to 5 times and fail this."""
    api = _make_api()
    action = AsyncMock(return_value=returned)

    assert await api._run_control_action(action) is False
    action.assert_awaited_once_with()


async def test_run_control_action_retries_only_on_connection_error() -> None:
    """The retry loop is scoped to *raised* transport failures: a connection error re-enters the loop,
    and once the action actually returns a value, that value is the answer returned (ADR 0013)."""
    api = _make_api()
    action = AsyncMock(side_effect=[XploraConnectionError(), True])

    assert await api._run_control_action(action) is True
    assert action.await_count == 2


async def test_run_control_action_propagates_protocol_error() -> None:
    """``XploraProtocolError`` does not subclass ``Error``, so the helper's ``except Error`` cannot
    swallow it: a schema-drift/code bug surfaces loudly rather than being retried and mislabeled a
    server refusal (ADR 0010, gotcha #4)."""
    api = _make_api()
    action = AsyncMock(side_effect=XploraProtocolError(WatchCommand.SET_ENABLE_SILENT_TIME, {"a": 1, "b": 2}))

    with pytest.raises(XploraProtocolError):
        await api._run_control_action(action)
    action.assert_awaited_once_with()


async def test_run_control_action_returns_false_after_exhausting_retries() -> None:
    """When *every* attempt raises a transport error, the helper exhausts its budget and returns
    ``False`` -- it never re-raises the transport error. The budget is ``maxRetries + 2`` attempts, so
    this pins the terminal ``return False`` fall-through that the one-failure-then-success retry test
    never reaches (ADR 0013)."""
    api = _make_api()
    action = AsyncMock(side_effect=XploraConnectionError())

    assert await api._run_control_action(action) is False
    assert action.await_count == api.maxRetries + 2  # full attempt budget (5 at defaults)


async def test_run_control_action_does_not_back_off_after_the_final_attempt() -> None:
    """Backoff sleeps sit BETWEEN attempts only, never after the last failed one: ``maxRetries + 1``
    sleeps for ``maxRetries + 2`` attempts (ADR 0013 -- the ~8s-not-10s cadence that drops the original
    loop's trailing sleep). ``retryDelay`` is 0 in tests, so the sleep is otherwise unobservable;
    asserting its count is the only teeth for the ``attempt < maxRetries + 1`` guard -- a regression to
    ``<=`` would restore the trailing sleep and slip past every other test."""
    api = _make_api()
    action = AsyncMock(side_effect=XploraConnectionError())

    with patch(
        "custom_components.xplora_watch.pyxplora_api.pyxplora_api_async.asyncio.sleep",
        new_callable=AsyncMock,
    ) as mock_sleep:
        assert await api._run_control_action(action) is False

    assert mock_sleep.await_count == api.maxRetries + 1  # backoff between attempts only (4 at defaults)
