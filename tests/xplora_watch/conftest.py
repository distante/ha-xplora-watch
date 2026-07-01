"""Shared fixtures for xplora_watch tests.

These tests exercise the REAL vendored ``pyxplora_api`` client code rather than mocking
the ``PyXploraApi`` class itself. Only the actual HTTP transport underneath it is
intercepted, using ``aioresponses``. All GraphQL operations hit the same endpoint URL and
are distinguished only by the ``operationName`` field in the request body, so a single
callback-based route does the dispatching (see ``mock_graphql`` below).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Generator
from typing import Any

import pytest
from aioresponses import CallbackResult, aioresponses
from homeassistant.const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_COUNTRY_CODE,
    CONF_EMAIL,
    CONF_LANGUAGE,
    CONF_PASSWORD,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
)
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
    HOME,
    MAPS,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.const import ENDPOINT
from custom_components.xplora_watch.services import async_setup_services

from .fixtures.graphql_payloads import DEFAULT_OPERATION_PAYLOADS, DEFAULT_WUID
from .fixtures.rest_payloads import (
    ENTITY_PICTURE_BODY,
    OPENCAGEDATA_REVERSE_GEOCODE,
    OPENSTREETMAP_REVERSE_GEOCODE,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow hass to load custom_components/xplora_watch in these tests."""


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collapse the vendored client's real `asyncio.sleep` delays to instant yields.

    `pyxplora_api`'s retry loops and `loadWatchLocation`'s post-`askWatchLocate` pause are
    real wall-clock delays meant to protect the live Xplora API from being hammered (the
    whole reason this integration was forked). They add no value in tests against a mocked
    transport and were making the suite take minutes; this keeps the cooperative yield-point
    (so `asyncio.gather` interleaving still behaves the same) without the wait.
    """
    real_sleep = asyncio.sleep

    async def _instant_sleep(delay: float, result: Any = None) -> Any:
        return await real_sleep(0, result)

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)


@pytest.fixture
def graphql_operations() -> dict[str, dict[str, Any]]:
    """Mutable per-test map of operationName -> {'data': payload}.

    Mutate this fixture's dict before triggering the call whose response you want
    to change (e.g. ``graphql_operations["Alarms"] = {"data": {"alarms": []}}``).
    """
    return {name: {"data": payload} for name, payload in DEFAULT_OPERATION_PAYLOADS.items()}


def _make_graphql_callback(operations: dict[str, dict[str, Any]]):
    def _callback(url: Any, **kwargs: Any) -> CallbackResult:
        body = kwargs.get("json") or {}
        operation_name = body.get("operationName")
        if operation_name is None:
            query = body.get("query", "")
            match = re.search(r"(?:query|mutation)\s+(\w+)", query)
            operation_name = match.group(1) if match else None
        payload = operations.get(operation_name)
        if payload is None:
            return CallbackResult(
                status=200,
                payload={"errors": [{"message": f"No fixture registered for operationName={operation_name!r}"}]},
            )
        return CallbackResult(status=200, payload=payload)

    return _callback


@pytest.fixture
def mock_graphql(graphql_operations: dict[str, dict[str, Any]]) -> Generator[aioresponses, None, None]:
    """Intercept every POST to the Xplora GraphQL endpoint and route it by operationName.

    One registration is enough since every GraphQL operation hits the identical URL.
    """
    with aioresponses() as mocked:
        mocked.post(ENDPOINT, callback=_make_graphql_callback(graphql_operations), repeat=True)
        yield mocked


@pytest.fixture
def mock_geocoding_openstreetmap(mock_graphql: aioresponses) -> aioresponses:
    """Extend the same aioresponses instance with the openstreetmap.org reverse-geocode route."""
    mock_graphql.get(
        re.compile(r"https://nominatim\.openstreetmap\.org/.*"),
        payload=OPENSTREETMAP_REVERSE_GEOCODE,
        repeat=True,
    )
    return mock_graphql


@pytest.fixture
def mock_geocoding_opencage(mock_graphql: aioresponses) -> aioresponses:
    """Extend the same aioresponses instance with the opencagedata.com reverse-geocode route."""
    mock_graphql.get(
        re.compile(r"https://api\.opencagedata\.com/.*"),
        payload=OPENCAGEDATA_REVERSE_GEOCODE,
        repeat=True,
    )
    return mock_graphql


@pytest.fixture
def mock_entity_picture(hass: HomeAssistant, mock_graphql: aioresponses) -> aioresponses:
    """device_tracker.py's GET against the watch's entity_picture URL, cached into www/image."""
    os.makedirs(hass.config.path("www/image"), exist_ok=True)
    mock_graphql.get(
        re.compile(r"https://api\.myxplora\.com/file.*"),
        status=200,
        body=ENTITY_PICTURE_BODY,
        repeat=True,
    )
    return mock_graphql


DEFAULT_OPTIONS: dict[str, Any] = {
    # CONF_TYPES is intentionally absent: entity visibility is now per-entity via
    # `entity_registry_enabled_default`, not an options-flow type selection. Only CONF_WATCHES
    # gates entity creation.
    CONF_WATCHES: [DEFAULT_WUID],
    CONF_MAPS: MAPS[0],
    CONF_SCAN_INTERVAL: 0,
    CONF_MESSAGE: 10,
    CONF_REMOVE_MESSAGE: False,
    CONF_HOME_SAFEZONE: "off",
}


@pytest.fixture
def mock_config_entry_phone(hass: HomeAssistant) -> MockConfigEntry:
    """A phone-signin config entry with sensible default options."""
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
        options=DEFAULT_OPTIONS,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_config_entry_email(hass: HomeAssistant) -> MockConfigEntry:
    """An email-signin config entry with sensible default options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Xplora®",
        unique_id="parent@example.com",
        data={
            CONF_EMAIL: "parent@example.com",
            CONF_PASSWORD: "secret",
            CONF_USERLANG: "en-GB",
            CONF_TIMEZONE: "Europe/Berlin",
            CONF_LANGUAGE: "en",
        },
        options=DEFAULT_OPTIONS,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_home_zone(hass: HomeAssistant) -> None:
    """Set up a ``zone.home`` state for helper/safezone/options-flow tests."""
    hass.states.async_set(
        HOME,
        "zoning",
        {ATTR_LATITUDE: 52.5200, ATTR_LONGITUDE: 13.4050, CONF_RADIUS: 100},
    )


@pytest.fixture
async def coordinator(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    mock_graphql: aioresponses,
    mock_geocoding_openstreetmap: aioresponses,
) -> XploraDataUpdateCoordinator:
    """A real XploraDataUpdateCoordinator with a real, initialized PyXploraApi controller."""
    coord = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
    session = aiohttp_client.async_get_clientsession(hass)
    await coord.init(session=session)
    return coord


@pytest.fixture
async def coordinator_with_data(coordinator: XploraDataUpdateCoordinator) -> XploraDataUpdateCoordinator:
    """``coordinator`` after a full data refresh, for entity/platform/service tests."""
    await coordinator.async_update_xplora_data()
    return coordinator


async def setup_service_target(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    wuids: tuple[str, ...] = (DEFAULT_WUID,),
) -> dict[str, str]:
    """Register the coordinator + the integration's services and create one HA device per watch.

    Mirrors production: each account's copy of a watch is its own device, identified by
    ``(DOMAIN, "{entry.unique_id}_{wuid}")`` and owned by the account's config entry. Returns a
    ``{wuid: device_id}`` map; tests then drive the real service via
    ``hass.services.async_call(DOMAIN, service, {"device_id": [device_id], ...}, blocking=True)``.
    """
    from homeassistant.helpers import device_registry as dr

    entry_id = coordinator._entry.entry_id
    hass.data.setdefault(DOMAIN, {})[entry_id] = coordinator
    await async_setup_services(hass, entry_id)
    registry = dr.async_get(hass)
    return {
        wuid: registry.async_get_or_create(
            config_entry_id=entry_id,
            identifiers={(DOMAIN, f"{coordinator._entry.unique_id}_{wuid}")},
        ).id
        for wuid in wuids
    }
