"""The five network-free demo accounts (see ``demo.py``).

The demo sentinels let the whole integration -- including the multi-account service fan-out
(ADR 0004) -- be exercised in a live Home Assistant with no Xplora servers: a primary Guardian, a
second Guardian of a different child, a Contact, a Guardian whose watch is offline, and a Guardian
whose forced re-fix raises (the browser e2e's Reload-failure persona). These assert each sentinel
resolves to the ``DemoPyXploraApi`` and seeds the intended watch identity + role + online state, so
a live area target spans all of them and the Guardian pre-filter / ``watch_offline`` /
partial-success surfacing can be seen by hand.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_EMAIL, CONF_LANGUAGE, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import (
    CONF_HOME_SAFEZONE,
    CONF_MAPS,
    CONF_MESSAGE,
    CONF_REMOVE_MESSAGE,
    CONF_TIMEZONE,
    CONF_USERLANG,
    CONF_WATCHES,
    DEMO_ACCOUNT_EMAIL,
    DEMO_CONTACT_ACCOUNT_EMAIL,
    DEMO_ERROR_ACCOUNT_EMAIL,
    DEMO_OFFLINE_ACCOUNT_EMAIL,
    DEMO_SECOND_PARENT_ACCOUNT_EMAIL,
    DOMAIN,
    MAPS,
)
from custom_components.xplora_watch.demo import (
    DEMO_CONTACT_WUID,
    DEMO_ERROR_WUID,
    DEMO_OFFLINE_WUID,
    DEMO_SECOND_PARENT_WUID,
    DEMO_WUID,
    DemoPyXploraApi,
    is_demo_account,
    make_controller,
)
from custom_components.xplora_watch.pyxplora_api.exception_classes import ConnectionError as XploraConnectionError

_ALL_SENTINELS = (
    DEMO_ACCOUNT_EMAIL,
    DEMO_SECOND_PARENT_ACCOUNT_EMAIL,
    DEMO_CONTACT_ACCOUNT_EMAIL,
    DEMO_OFFLINE_ACCOUNT_EMAIL,
    DEMO_ERROR_ACCOUNT_EMAIL,
)


async def _demo_controller(email: str) -> DemoPyXploraApi:
    controller = make_controller(email=email, password="x", userLang="en-GB", timeZone="Europe/Berlin")
    assert isinstance(controller, DemoPyXploraApi)  # sentinel -> network-free stand-in
    await controller.init()
    await controller.setDevices()
    return controller


def test_is_demo_account_recognizes_every_sentinel_and_nothing_else() -> None:
    for email in _ALL_SENTINELS:
        assert is_demo_account(email) is True
        assert is_demo_account(email.upper()) is True  # case-insensitive
    assert is_demo_account("real.parent@example.com") is False
    assert is_demo_account(None) is False


@pytest.mark.parametrize(
    ("email", "wuid", "guardian_type", "is_contact"),
    [
        (DEMO_ACCOUNT_EMAIL, DEMO_WUID, "FIRST", False),
        (DEMO_SECOND_PARENT_ACCOUNT_EMAIL, DEMO_SECOND_PARENT_WUID, "FIRST", False),
        (DEMO_CONTACT_ACCOUNT_EMAIL, DEMO_CONTACT_WUID, "SECOND", True),
        (DEMO_OFFLINE_ACCOUNT_EMAIL, DEMO_OFFLINE_WUID, "FIRST", False),
        (DEMO_ERROR_ACCOUNT_EMAIL, DEMO_ERROR_WUID, "FIRST", False),
    ],
)
async def test_each_demo_sentinel_seeds_its_own_watch_and_role(email: str, wuid: str, guardian_type: str, is_contact: bool) -> None:
    controller = await _demo_controller(email)

    # Each account owns exactly its own watch...
    assert controller.getWatchUserIDs() == [wuid]
    # ...and carries the role the coordinator derives `is_admin` from (`guardianType == "FIRST"`).
    assert controller.getDevice(wuid).get("guardianType") == guardian_type
    # A Contact account is NOT the watch's primary guardian, so control actions gate it out.
    assert (guardian_type != "FIRST") is is_contact


async def test_the_five_demo_accounts_are_distinct_watches() -> None:
    """Distinct wuids, so a single area target spans all of them and fans out per ADR 0004."""
    assert len({DEMO_WUID, DEMO_SECOND_PARENT_WUID, DEMO_CONTACT_WUID, DEMO_OFFLINE_WUID, DEMO_ERROR_WUID}) == 5


async def test_error_account_fails_a_forced_fix_only_after_the_first_cycle_succeeds() -> None:
    """The Error demo watch loads normally (first fix cycle succeeds), then a *forced* re-fix raises.

    The entry has to load for its dashboard view and map card to exist at all -- so the first fix
    cycle (the setup refresh) must succeed and seed a real position. `askWatchLocate` is the
    once-per-cycle entry point in `coordinator._refresh_watch_fix` (which then reads
    `loadWatchLocation` possibly several times), so the second cycle raises there, before any read.
    That is the map card's Reload button (the watch's Update button); the error propagates up so the
    frontend `callService` rejects -> the card's failed-press recovery runs. Polling is OFF by
    default, so nothing else fires a locate in between. Contrast the Offline watch, which returns
    `False` (a no-response, keeps the last fix) rather than raising.
    """
    controller = await _demo_controller(DEMO_ERROR_ACCOUNT_EMAIL)

    # First cycle (the setup refresh) succeeds and returns a real position.
    assert await controller.askWatchLocate(DEMO_ERROR_WUID) is True
    first = await controller.loadWatchLocation(DEMO_ERROR_WUID)
    assert first["lat"] and first["lng"]

    # Every forced re-fix cycle after that raises at the locate request -- the Reload press errors.
    with pytest.raises(XploraConnectionError):
        await controller.askWatchLocate(DEMO_ERROR_WUID)

    # A healthy Guardian's forced re-fix keeps working -- the gate is scoped to the Error persona.
    healthy = await _demo_controller(DEMO_ACCOUNT_EMAIL)
    assert await healthy.askWatchLocate(DEMO_WUID) is True
    assert await healthy.askWatchLocate(DEMO_WUID) is True


async def test_offline_account_refuses_control_actions_but_online_ones_accept() -> None:
    """The offline demo watch refuses a control action (returns False -> `watch_offline`); a Guardian
    account whose watch is online accepts it. This is what makes `watch_offline` visible by hand."""
    offline = await _demo_controller(DEMO_OFFLINE_ACCOUNT_EMAIL)
    online = await _demo_controller(DEMO_ACCOUNT_EMAIL)

    assert await offline.reboot(DEMO_OFFLINE_WUID) is False  # refused == offline
    assert await offline.shutdown(DEMO_OFFLINE_WUID) is False
    assert await online.reboot(DEMO_WUID) is True  # a healthy watch still accepts


async def test_primary_demo_account_reports_a_named_safezone_scenario() -> None:
    """The primary demo watch reports being INSIDE a named safezone, network-free.

    This is what makes the `current_safezone` sensor (and the safezone card tile) exercisable
    with no servers: the deviceList status and the fresh-fix overlay agree on the label, and a
    matching safezone definition exists so the per-zone tracker appears too. The other demo
    accounts stay outside every zone (empty label) so both sensor states can be seen live.
    """
    primary = await _demo_controller(DEMO_ACCOUNT_EMAIL)

    device = primary.getDevice(DEMO_WUID)
    assert device.get("isInSafeZone") is True
    assert device.get("safeZoneLabel") == "LEGOLAND"

    location = await primary.loadWatchLocation(DEMO_WUID)
    assert location["watch_last_location"]["safeZoneLabel"] == "LEGOLAND"

    zones = await primary.getWatchSafeZones(DEMO_WUID)
    assert [zone["name"] for zone in zones] == ["LEGOLAND"]

    # Contrast: a non-primary demo account stays outside every safezone (unknown-state path).
    second = await _demo_controller(DEMO_SECOND_PARENT_ACCOUNT_EMAIL)
    assert second.getDevice(DEMO_SECOND_PARENT_WUID).get("isInSafeZone") is False
    assert second.getDevice(DEMO_SECOND_PARENT_WUID).get("safeZoneLabel") == ""
    assert await second.getWatchSafeZones(DEMO_SECOND_PARENT_WUID) == []


def _demo_entry(hass: HomeAssistant, email: str, wuid: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Xplora®",
        unique_id=email,
        data={
            CONF_EMAIL: email,
            CONF_PASSWORD: "x",
            CONF_USERLANG: "en-GB",
            CONF_TIMEZONE: "Europe/Berlin",
            CONF_LANGUAGE: "en",
        },
        options={
            CONF_WATCHES: [wuid],
            CONF_MAPS: MAPS[0],  # OpenStreetMap -> would reverse-geocode if not short-circuited
            CONF_SCAN_INTERVAL: 0,
            CONF_MESSAGE: 10,
            CONF_REMOVE_MESSAGE: False,
            CONF_HOME_SAFEZONE: "off",
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.parametrize(
    ("email", "wuid"),
    [
        (DEMO_ACCOUNT_EMAIL, DEMO_WUID),
        (DEMO_SECOND_PARENT_ACCOUNT_EMAIL, DEMO_SECOND_PARENT_WUID),
        (DEMO_CONTACT_ACCOUNT_EMAIL, DEMO_CONTACT_WUID),
        (DEMO_OFFLINE_ACCOUNT_EMAIL, DEMO_OFFLINE_WUID),
        (DEMO_ERROR_ACCOUNT_EMAIL, DEMO_ERROR_WUID),
    ],
)
async def test_demo_entry_sets_up_network_free_and_creates_its_watch_device(hass: HomeAssistant, email: str, wuid: str) -> None:
    """A demo entry must set up with NO outbound network at all (no reverse-geocode) and create the
    watch device -- otherwise the device picker for every service is empty. No aiohttp mock is
    installed here on purpose: any real HTTP request would fail the setup and this test.
    """
    entry = _demo_entry(hass, email, wuid)

    with patch("custom_components.xplora_watch.create_www_directory", new=AsyncMock()):
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert result is True  # setup completed offline
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, f"{email}_{wuid}")})
    assert device is not None  # the watch device the service `device_id` picker lists
