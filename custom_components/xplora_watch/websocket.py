"""Websocket API for Xplora® Watch Version 2.

Lets the custom Lovelace card load a single day's location track on demand: the card sends the
picked day (YYYY-MM-DD); the coordinator returns today fresh or a cached past day (network only on
first view). The retained history lives only in the coordinator's per-day `Store` (so it never
bloats the recorder); the card reads it via ``hass.callWS({type: "xplora_watch/location_history",
…})``.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, WS_TYPE_LOCATION_HISTORY
from .coordinator import XploraDataUpdateCoordinator
from .pyxplora_api.exception_classes import AuthError, RateLimitError
from .pyxplora_api.exception_classes import ConnectionError as XploraConnectionError


@callback
def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register the integration's websocket commands (idempotent; keyed by command type)."""
    websocket_api.async_register_command(hass, websocket_location_history)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_TYPE_LOCATION_HISTORY,
        vol.Required("entry_id"): str,
        vol.Required("wuid"): str,
        # The calendar day to view (YYYY-MM-DD, in the watch timezone). Today is always fetched
        # fresh; a past day is served from the cache once stored (network only on first view).
        vol.Required("day"): str,
    }
)
@websocket_api.async_response
async def websocket_location_history(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Return one day's location-history points for a watch (cached past days, fresh today).

    Resolves the coordinator from ``entry_id`` and returns ``{wuid, day, points}`` (points ascending
    by ``tm``). Delegates the cache-vs-network decision to ``async_fetch_history_day``. An unknown /
    not-loaded entry is reported as an error rather than an empty result so the card can tell apart.
    """
    coordinator = hass.data.get(DOMAIN, {}).get(msg["entry_id"])
    if not isinstance(coordinator, XploraDataUpdateCoordinator):
        connection.send_error(msg["id"], websocket_api.const.ERR_NOT_FOUND, "unknown or unloaded entry_id")
        return
    # `async_fetch_history_day` re-raises auth/rate-limit/connection failures (only generic fetch
    # errors are swallowed there). Catch them so the card gets a clean error to render instead of a
    # raw traceback / generic "unknown_error" -- the same graceful treatment the `fetch_history`
    # service gives the identical call. A fresh fix arrives once the integration re-authenticates.
    try:
        points = await coordinator.async_fetch_history_day(msg["wuid"], msg["day"])
    except AuthError:
        connection.send_error(msg["id"], websocket_api.const.ERR_UNAUTHORIZED, "Xplora session expired; retry after re-authentication")
        return
    except (RateLimitError, XploraConnectionError) as err:
        # A 429 is NOT a timeout; keying it as ERR_TIMEOUT could invite a consumer to
        # retry-on-timeout and worsen a ban. ERR_HOME_ASSISTANT_ERROR is the least-wrong code.
        connection.send_error(msg["id"], websocket_api.const.ERR_HOME_ASSISTANT_ERROR, f"Xplora history fetch failed: {err}")
        return
    connection.send_result(msg["id"], {"wuid": msg["wuid"], "day": msg["day"], "points": points})
