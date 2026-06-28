"""Coordinator-level tests for persisting the Xplora session across restarts.

The token lives ~35 days but only in memory, so without persistence every Home Assistant restart
spends a fresh `signInWithEmailOrPhone`. The coordinator saves the session to `.storage` after a
login and restores it on the next build, so a restart within the token's life makes zero login
calls. These tests count the `signInWithEmailOrPhone` operation against a mocked transport (the
`hass_storage` fixture keeps the `Store` in-memory and shared by store key across coordinators).
"""

from __future__ import annotations

import re
from typing import Any

from aioresponses import CallbackResult, aioresponses
from homeassistant.const import CONF_COUNTRY_CODE, CONF_LANGUAGE, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import (
    CONF_HOME_SAFEZONE,
    CONF_MAPS,
    CONF_MESSAGE,
    CONF_PHONENUMBER,
    CONF_REMOVE_MESSAGE,
    CONF_TIMEZONE,
    CONF_USERLANG,
    CONF_WATCHES,
    DOMAIN,
    MAPS,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.const import ENDPOINT, GqlOperation

from ..conftest import _make_graphql_callback
from ..fixtures.graphql_payloads import DEFAULT_OPERATION_PAYLOADS, DEFAULT_WUID
from ..fixtures.rest_payloads import OPENSTREETMAP_REVERSE_GEOCODE


def _entry(hass: HomeAssistant) -> MockConfigEntry:
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
        options={
            CONF_WATCHES: [DEFAULT_WUID],
            CONF_MAPS: MAPS[0],
            CONF_SCAN_INTERVAL: 0,
            CONF_MESSAGE: 10,
            CONF_REMOVE_MESSAGE: False,
            CONF_HOME_SAFEZONE: "off",
        },
    )
    entry.add_to_hass(hass)
    return entry


def _mocked_transport(login_counter: dict[str, int]) -> aioresponses:
    operations = {name: {"data": payload} for name, payload in DEFAULT_OPERATION_PAYLOADS.items()}
    default_callback = _make_graphql_callback(operations)

    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        op = (kwargs.get("json") or {}).get("operationName")
        if op == GqlOperation.SIGN_IN:
            login_counter["count"] += 1
        return default_callback(url, **kwargs)

    mocked = aioresponses()
    mocked.start()
    mocked.post(ENDPOINT, callback=_callback, repeat=True)
    mocked.get(re.compile(r"https://nominatim\.openstreetmap\.org/.*"), payload=OPENSTREETMAP_REVERSE_GEOCODE, repeat=True)
    return mocked


async def test_login_persists_session(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    """A first-ever login writes the session blob to `.storage`."""
    entry = _entry(hass)
    login_counter = {"count": 0}
    mocked = _mocked_transport(login_counter)
    try:
        coord = XploraDataUpdateCoordinator(hass, entry)
        await coord.init(session=aiohttp_client.async_get_clientsession(hass))
    finally:
        mocked.stop()

    assert login_counter["count"] == 1  # exactly one login on a cold start
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}.session"]["data"]
    assert stored["issue_token"]["token"] == "access-token-1"


async def test_restore_skips_login(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    """A second coordinator for the same entry (a restart) restores the token and does NOT log in."""
    entry = _entry(hass)

    # First build: cold start -> logs in and persists.
    first_counter = {"count": 0}
    mocked = _mocked_transport(first_counter)
    try:
        await XploraDataUpdateCoordinator(hass, entry).init(session=aiohttp_client.async_get_clientsession(hass))
    finally:
        mocked.stop()
    assert first_counter["count"] == 1

    # Second build (simulated restart): the store already holds the blob -> zero logins.
    second_counter = {"count": 0}
    mocked = _mocked_transport(second_counter)
    try:
        coord2 = XploraDataUpdateCoordinator(hass, entry)
        await coord2.init(session=aiohttp_client.async_get_clientsession(hass))
    finally:
        mocked.stop()

    assert second_counter["count"] == 0  # restored token -> no signInWithEmailOrPhone
    # The restored controller still resolved the watch list (from the persisted token's children).
    assert DEFAULT_WUID in coord2.controller.getWatchUserIDs()


async def test_clear_persisted_session_removes_blob(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    """Clearing the session (entry removal) deletes the stored blob so a re-add can't resurrect it."""
    entry = _entry(hass)
    login_counter = {"count": 0}
    mocked = _mocked_transport(login_counter)
    try:
        coord = XploraDataUpdateCoordinator(hass, entry)
        await coord.init(session=aiohttp_client.async_get_clientsession(hass))
    finally:
        mocked.stop()
    assert f"{DOMAIN}.{entry.entry_id}.session" in hass_storage

    await coord.async_clear_persisted_session()

    assert f"{DOMAIN}.{entry.entry_id}.session" not in hass_storage
