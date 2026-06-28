"""Tests for the built-in daily history auto-fetch scheduler.

``setup_history_scheduler`` registers an ``async_track_time_change`` listener (at
``AUTO_FETCH_HISTORY_HOUR``) when ``CONF_AUTO_FETCH_HISTORY`` is True.  The callback
``_async_auto_fetch_yesterday`` fetches the previous day's track for each configured watch
(relying on ``async_fetch_history_day``'s own cache to skip already-archived days), refreshes
the entities, and handles auth/rate-limit/connection errors gracefully instead of letting them
propagate out of the timer callback.  ``async_teardown`` cancels the listener.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import (
    AUTO_FETCH_HISTORY_HOUR,
    CONF_WATCHES,
    DOMAIN,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.exception_classes import (
    AuthError,
    RateLimitError,
)

from ..fixtures.graphql_payloads import DEFAULT_WUID, make_loc_history_payload


def _enable_auto_fetch(coordinator: XploraDataUpdateCoordinator) -> None:
    """Flip the resolved ``auto_fetch_history`` flag on without rebuilding the coordinator."""
    coordinator._resolved = dataclasses.replace(coordinator._resolved, auto_fetch_history=True)


def _patch_loc_history(coordinator: XploraDataUpdateCoordinator) -> AsyncMock:
    """Replace the controller's ``getWatchLocHistory`` with a counting AsyncMock."""
    mock = AsyncMock(return_value=make_loc_history_payload())
    coordinator.controller.getWatchLocHistory = mock  # type: ignore[method-assign]
    return mock


# ---------------------------------------------------------------------------
# setup_history_scheduler
# ---------------------------------------------------------------------------


async def test_scheduler_not_registered_when_disabled(coordinator: XploraDataUpdateCoordinator) -> None:
    """``setup_history_scheduler`` is a no-op when the option is off (the default)."""
    coordinator.setup_history_scheduler()
    assert coordinator._cancel_history_scheduler is None


async def test_scheduler_registered_when_enabled(coordinator: XploraDataUpdateCoordinator) -> None:
    """``setup_history_scheduler`` registers a time-change listener at the configured hour."""
    _enable_auto_fetch(coordinator)
    with patch(
        "custom_components.xplora_watch.coordinator.async_track_time_change",
        return_value=MagicMock(),
    ) as mock_track:
        coordinator.setup_history_scheduler()

    mock_track.assert_called_once_with(
        coordinator.hass,
        coordinator._async_auto_fetch_yesterday,
        hour=AUTO_FETCH_HISTORY_HOUR,
        minute=0,
        second=0,
    )
    assert coordinator._cancel_history_scheduler is not None


async def test_setup_is_idempotent_cancels_previous(coordinator: XploraDataUpdateCoordinator) -> None:
    """A second ``setup_history_scheduler`` cancels the first listener before registering anew."""
    _enable_auto_fetch(coordinator)
    first_cancel = MagicMock()
    second_cancel = MagicMock()
    with patch(
        "custom_components.xplora_watch.coordinator.async_track_time_change",
        side_effect=[first_cancel, second_cancel],
    ):
        coordinator.setup_history_scheduler()
        coordinator.setup_history_scheduler()

    # The first subscription's cancel handle must have been invoked (no leaked timer).
    first_cancel.assert_called_once()
    assert coordinator._cancel_history_scheduler is second_cancel


# ---------------------------------------------------------------------------
# _async_auto_fetch_yesterday
# ---------------------------------------------------------------------------


async def test_auto_fetch_calls_fetch_when_not_cached(coordinator: XploraDataUpdateCoordinator) -> None:
    """The callback triggers a network fetch when yesterday's data is absent from cache."""
    yesterday = coordinator.history_yesterday_key()
    assert yesterday not in coordinator._loc_history.get(DEFAULT_WUID, {})

    mock = _patch_loc_history(coordinator)
    await coordinator._async_auto_fetch_yesterday(datetime.now())

    mock.assert_called_once()


async def test_auto_fetch_skips_network_when_already_cached(coordinator: XploraDataUpdateCoordinator) -> None:
    """A day already cached is served from the Store -- ``async_fetch_history_day`` hits no network."""
    yesterday = coordinator.history_yesterday_key()
    coordinator._loc_history.setdefault(DEFAULT_WUID, {})[yesterday] = []

    mock = _patch_loc_history(coordinator)
    await coordinator._async_auto_fetch_yesterday(datetime.now())

    mock.assert_not_called()


async def test_auto_fetch_refreshes_listeners(coordinator: XploraDataUpdateCoordinator) -> None:
    """The callback pushes the refreshed cache to entities via ``async_update_listeners``."""
    _patch_loc_history(coordinator)
    with patch.object(coordinator, "async_update_listeners") as mock_update:
        await coordinator._async_auto_fetch_yesterday(datetime.now())
    mock_update.assert_called_once()


async def test_auto_fetch_falls_back_to_watch_user_ids(coordinator: XploraDataUpdateCoordinator) -> None:
    """With CONF_WATCHES unset, the callback resolves watches via ``getWatchUserIDs`` (not a no-op)."""
    # Drop CONF_WATCHES from the entry options so the fallback branch is exercised.
    old_entry = coordinator._entry
    options = {k: v for k, v in old_entry.options.items() if k != CONF_WATCHES}
    coordinator._entry = MockConfigEntry(
        domain=DOMAIN,
        title=old_entry.title,
        unique_id=old_entry.unique_id,
        data=dict(old_entry.data),
        options=options,
    )
    coordinator._entry.add_to_hass(coordinator.hass)

    mock = _patch_loc_history(coordinator)
    await coordinator._async_auto_fetch_yesterday(datetime.now())

    # getWatchUserIDs() returns the logged-in account's watches (DEFAULT_WUID), so one fetch fires.
    mock.assert_called_once()


async def test_auto_fetch_swallows_auth_error(coordinator: XploraDataUpdateCoordinator) -> None:
    """An expired/refused token is logged, not propagated out of the timer callback."""
    coordinator.controller.getWatchLocHistory = AsyncMock(side_effect=AuthError("E000004"))  # type: ignore[method-assign]
    with patch.object(coordinator, "async_update_listeners") as mock_update:
        # Must NOT raise.
        await coordinator._async_auto_fetch_yesterday(datetime.now())
    # Listeners are still refreshed (the run completes cleanly).
    mock_update.assert_called_once()


async def test_auto_fetch_swallows_rate_limit_error(coordinator: XploraDataUpdateCoordinator) -> None:
    """A transient rate-limit failure is logged cleanly, not propagated."""
    coordinator.controller.getWatchLocHistory = AsyncMock(side_effect=RateLimitError("429"))  # type: ignore[method-assign]
    await coordinator._async_auto_fetch_yesterday(datetime.now())  # must NOT raise


# ---------------------------------------------------------------------------
# async_teardown
# ---------------------------------------------------------------------------


async def test_teardown_cancels_scheduler(coordinator: XploraDataUpdateCoordinator) -> None:
    """``async_teardown`` calls the unsubscribe callable and clears the attribute."""
    cancel_mock = MagicMock()
    coordinator._cancel_history_scheduler = cancel_mock

    coordinator.async_teardown()

    cancel_mock.assert_called_once()
    assert coordinator._cancel_history_scheduler is None


async def test_teardown_is_noop_when_no_scheduler(coordinator: XploraDataUpdateCoordinator) -> None:
    """``async_teardown`` when the scheduler was never registered does not raise."""
    assert coordinator._cancel_history_scheduler is None
    coordinator.async_teardown()  # must not raise
    assert coordinator._cancel_history_scheduler is None
