"""Tests for the `xplora_watch/location_history` websocket command.

Deliberately does NOT use the `coordinator` fixture: that fixture activates `aioresponses`, which
intercepts ALL HTTP -- including the local websocket connection `hass_ws_client` needs. The command
serves a cached PAST day via `async_fetch_history_day` from the in-memory Store without any network
call, so a coordinator built directly (no `init()`, no controller) is sufficient here. The
network-fetch path (today / uncached days) is covered by the coordinator tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import DOMAIN, WS_TYPE_LOCATION_HISTORY
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.exception_classes import (
    AuthError,
    RateLimitError,
)
from custom_components.xplora_watch.websocket import async_register_websocket_commands

from ..fixtures.graphql_payloads import DEFAULT_WUID

# A cached PAST day (not today) -> `async_fetch_history_day` returns it from the Store without a
# network call, so these tests need no controller.
PAST_DAY = "2020-01-01"
_POINTS = [
    {"tm": 1000, "lat": 1.0, "lng": 2.0},
    {"tm": 2000, "lat": 1.0, "lng": 2.0},
    {"tm": 3000, "lat": 1.0, "lng": 2.0},
]


async def _setup_ws(hass: HomeAssistant, entry: MockConfigEntry, day_buckets: dict[str, list[dict[str, Any]]] | None) -> str:
    """Register the command + expose a (network-free) coordinator with the given cached day buckets."""
    assert await async_setup_component(hass, "websocket_api", {})
    coordinator = XploraDataUpdateCoordinator(hass, entry)
    if day_buckets is not None:
        coordinator._loc_history[DEFAULT_WUID] = day_buckets
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    async_register_websocket_commands(hass)
    return entry.entry_id


async def test_ws_returns_cached_day(hass: HomeAssistant, hass_ws_client: Any, mock_config_entry_phone: MockConfigEntry) -> None:
    """A cached past day is returned from the Store (no network)."""
    entry_id = await _setup_ws(hass, mock_config_entry_phone, {PAST_DAY: list(_POINTS)})
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": WS_TYPE_LOCATION_HISTORY, "entry_id": entry_id, "wuid": DEFAULT_WUID, "day": PAST_DAY})
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"]["wuid"] == DEFAULT_WUID
    assert msg["result"]["day"] == PAST_DAY
    assert [p["tm"] for p in msg["result"]["points"]] == [1000, 2000, 3000]


async def test_ws_unknown_entry_errors(hass: HomeAssistant, hass_ws_client: Any, mock_config_entry_phone: MockConfigEntry) -> None:
    """An unknown/not-loaded entry id is reported as an error, not an empty result."""
    await _setup_ws(hass, mock_config_entry_phone, {PAST_DAY: list(_POINTS)})
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": WS_TYPE_LOCATION_HISTORY, "entry_id": "does-not-exist", "wuid": DEFAULT_WUID, "day": PAST_DAY})
    msg = await client.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "not_found"


async def test_ws_auth_error_reported_cleanly(hass: HomeAssistant, hass_ws_client: Any, mock_config_entry_phone: MockConfigEntry) -> None:
    """An expired token surfaces as a clean `unauthorized` error, not a raw traceback."""
    entry_id = await _setup_ws(hass, mock_config_entry_phone, {PAST_DAY: list(_POINTS)})
    coordinator: XploraDataUpdateCoordinator = hass.data[DOMAIN][entry_id]
    coordinator.async_fetch_history_day = AsyncMock(side_effect=AuthError("E000004"))  # type: ignore[method-assign]
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": WS_TYPE_LOCATION_HISTORY, "entry_id": entry_id, "wuid": DEFAULT_WUID, "day": PAST_DAY})
    msg = await client.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "unauthorized"


async def test_ws_rate_limit_error_reported_cleanly(
    hass: HomeAssistant, hass_ws_client: Any, mock_config_entry_phone: MockConfigEntry
) -> None:
    """A transient rate-limit/connection failure surfaces as a clean `home_assistant_error`.

    A 429 is NOT a timeout: keying it as `timeout` could invite a consumer to retry-on-timeout and
    worsen a ban, so `home_assistant_error` is the least-wrong available code.
    """
    entry_id = await _setup_ws(hass, mock_config_entry_phone, {PAST_DAY: list(_POINTS)})
    coordinator: XploraDataUpdateCoordinator = hass.data[DOMAIN][entry_id]
    coordinator.async_fetch_history_day = AsyncMock(side_effect=RateLimitError("429"))  # type: ignore[method-assign]
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": WS_TYPE_LOCATION_HISTORY, "entry_id": entry_id, "wuid": DEFAULT_WUID, "day": PAST_DAY})
    msg = await client.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "home_assistant_error"
