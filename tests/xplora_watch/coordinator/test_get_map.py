"""Tests for XploraDataUpdateCoordinator.get_map() / openstreetmap() / opencagedata() / mapbox()."""

from __future__ import annotations

import re

from homeassistant.const import CONF_COUNTRY_CODE, CONF_LANGUAGE, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import (
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
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID
from tests.xplora_watch.fixtures.rest_payloads import (
    MAPBOX_REVERSE_GEOCODE,
    OPENCAGEDATA_REVERSE_GEOCODE,
    OPENSTREETMAP_REVERSE_GEOCODE,
)


async def test_get_map_uses_openstreetmap_by_default(coordinator: XploraDataUpdateCoordinator) -> None:
    """With the default MAPS[0] option and lat/lng set, openstreetmap() is used."""
    coordinator.lat = 52.5200
    coordinator.lng = 13.4050

    await coordinator.get_map(DEFAULT_WUID)

    assert coordinator.location_name == OPENSTREETMAP_REVERSE_GEOCODE["display_name"]
    assert coordinator.licence == OPENSTREETMAP_REVERSE_GEOCODE["licence"]


async def test_get_map_caches_unchanged_fix(coordinator: XploraDataUpdateCoordinator, monkeypatch) -> None:
    """An unchanged fix reuses the cached address (no second geocode); a real move re-geocodes."""
    calls = {"n": 0}

    async def fake_osm() -> None:
        calls["n"] += 1
        coordinator.location_name = "Cached Place"
        coordinator.licence = "lic"

    # Patch the provider so we can count network hits without depending on the HTTP mock.
    monkeypatch.setattr(coordinator, "openstreetmap", fake_osm)

    coordinator.lat = 52.5200
    coordinator.lng = 13.4050
    await coordinator.get_map(DEFAULT_WUID)  # cache miss -> geocode
    # Clear the live value to prove the second call restores it from the cache, not the network.
    coordinator.location_name = None
    await coordinator.get_map(DEFAULT_WUID)  # same fix -> cache hit, NO geocode
    assert calls["n"] == 1
    assert coordinator.location_name == "Cached Place"

    # A genuine position change must re-geocode (exact-match cache, never a stale address).
    coordinator.lat = 48.8566
    coordinator.lng = 2.3522
    await coordinator.get_map(DEFAULT_WUID)
    assert calls["n"] == 2


async def test_get_map_cache_is_per_watch(coordinator: XploraDataUpdateCoordinator, monkeypatch) -> None:
    """The geocode cache is keyed per watch, so the same fix for a different wuid still geocodes."""
    calls = {"n": 0}

    async def fake_osm() -> None:
        calls["n"] += 1
        coordinator.location_name = "Place"

    monkeypatch.setattr(coordinator, "openstreetmap", fake_osm)

    coordinator.lat = 52.5200
    coordinator.lng = 13.4050
    await coordinator.get_map(DEFAULT_WUID)
    await coordinator.get_map("a-different-watch-uid")
    assert calls["n"] == 2


async def test_get_map_does_not_cache_failed_lookup(coordinator: XploraDataUpdateCoordinator, monkeypatch) -> None:
    """A lookup that resolves no address is not cached, so the next poll retries it (not pinned)."""
    calls = {"n": 0}

    async def fake_osm() -> None:
        # Simulate an empty/failed provider response: leave location_name as get_map reset it (None).
        calls["n"] += 1

    monkeypatch.setattr(coordinator, "openstreetmap", fake_osm)

    coordinator.lat = 52.5200
    coordinator.lng = 13.4050
    await coordinator.get_map(DEFAULT_WUID)  # miss -> geocode, but no address resolved -> uncached
    await coordinator.get_map(DEFAULT_WUID)  # same fix, prior lookup failed -> retry, not a cache hit
    assert calls["n"] == 2
    assert coordinator.location_name is None


async def test_get_map_noop_when_lat_lng_none(coordinator: XploraDataUpdateCoordinator) -> None:
    """get_map() does nothing when lat/lng are None, regardless of the maps option."""
    coordinator.lat = None
    coordinator.lng = None

    await coordinator.get_map(DEFAULT_WUID)

    assert coordinator.location_name is None
    assert coordinator.licence is None


async def test_get_map_uses_opencagedata_when_configured(hass: HomeAssistant, mock_geocoding_opencage) -> None:
    """With CONF_MAPS set to MAPS[1] and lat/lng set, opencagedata() is used."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Xplora®",
        unique_id="+491700000098",
        data={
            CONF_COUNTRY_CODE: "+49",
            CONF_PHONENUMBER: "+491700000098",
            CONF_PASSWORD: "secret",
            CONF_USERLANG: "en-GB",
            CONF_TIMEZONE: "Europe/Berlin",
            CONF_LANGUAGE: "en",
        },
        options={
            CONF_WATCHES: [DEFAULT_WUID],
            CONF_MAPS: MAPS[1],
            CONF_SCAN_INTERVAL: 0,
            CONF_MESSAGE: 10,
            CONF_REMOVE_MESSAGE: False,
        },
    )
    entry.add_to_hass(hass)

    coord = XploraDataUpdateCoordinator(hass, entry)
    await coord.init(session=aiohttp_client.async_get_clientsession(hass))
    coord.lat = 52.5200
    coord.lng = 13.4050

    await coord.get_map(DEFAULT_WUID)

    assert coord.location_name == OPENCAGEDATA_REVERSE_GEOCODE["results"][0]["formatted"]
    assert coord.licence == OPENCAGEDATA_REVERSE_GEOCODE["licenses"][0]["url"]


async def test_openstreetmap_content_type_error_falls_back_to_mapbox(
    hass: HomeAssistant, mock_config_entry_phone: MockConfigEntry, mock_graphql
) -> None:
    """A non-JSON openstreetmap.org response raises ContentTypeError, triggering the mapbox() fallback.

    Note: this deliberately does NOT depend on the ``coordinator``/``mock_geocoding_openstreetmap``
    fixtures, since aioresponses matches routes in registration order (first match wins) and the
    shared fixture's valid-JSON openstreetmap route would otherwise shadow this test's broken one.
    """
    mock_graphql.get(
        re.compile(r"https://nominatim\.openstreetmap\.org/.*"),
        body="not json",
        content_type="text/plain",
        repeat=True,
    )
    mock_graphql.get(
        re.compile(r"https://api\.mapbox\.com/.*"),
        payload=MAPBOX_REVERSE_GEOCODE,
        repeat=True,
    )

    coord = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
    await coord.init(session=aiohttp_client.async_get_clientsession(hass))
    coord.lat = 52.5200
    coord.lng = 13.4050

    await coord.get_map(DEFAULT_WUID)

    assert coord.location_name == MAPBOX_REVERSE_GEOCODE["features"][0]["place_name"]
    assert coord.licence == MAPBOX_REVERSE_GEOCODE["attribution"]
