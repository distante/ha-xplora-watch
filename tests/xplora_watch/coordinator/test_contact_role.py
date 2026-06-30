"""Guardian/Contact role primitive.

The shared ``GUARDIAN_ONLY_KEYS`` set and the fail-open ``is_confirmed_contact`` helper that every
later Contact-gating slice (entity creation, the upgrade cleanup sweep, the service gate) builds on.

This is a pure prefactor: the constant and helper exist but nothing consumes them yet, so these
tests pin the contract directly rather than through entity/service behavior.
"""

from __future__ import annotations

from custom_components.xplora_watch.const import (
    BINARY_SENSOR_CHARGING,
    BINARY_SENSOR_SAFEZONE,
    BINARY_SENSOR_STATE,
    BUTTON_REBOOT,
    BUTTON_REFRESH_FUNCTIONS,
    BUTTON_SHUTDOWN,
    BUTTON_UPDATE,
    GUARDIAN_ONLY_KEYS,
    SENSOR_ALARMS,
    SENSOR_BATTERY,
    SENSOR_DISTANCE,
    SENSOR_LAST_UPDATE,
    SENSOR_LOCATION_HISTORY,
    SENSOR_MESSAGE,
    SENSOR_SILENTS,
    SENSOR_STEP_DAY,
    SENSOR_XCOIN,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator

WUID = "watch-1"


def test_guardian_only_keys_lists_every_restricted_entity_kind() -> None:
    # Battery, distance, alarm/silent lists and location-history sensors; charging and safe-zone
    # binary sensors; and the reboot/shutdown/refresh-functions buttons -- the kinds a Contact
    # never populates or may not control. (Device-tracker entities have no description key and are
    # gated at the platform level, so they are intentionally absent from this set.)
    assert GUARDIAN_ONLY_KEYS == frozenset(
        {
            SENSOR_BATTERY,
            SENSOR_DISTANCE,
            SENSOR_ALARMS,
            SENSOR_SILENTS,
            SENSOR_LOCATION_HISTORY,
            BINARY_SENSOR_CHARGING,
            BINARY_SENSOR_SAFEZONE,
            BUTTON_REBOOT,
            BUTTON_SHUTDOWN,
            BUTTON_REFRESH_FUNCTIONS,
        }
    )


def test_guardian_only_keys_excludes_contact_allowed_kinds() -> None:
    # A Contact still gets online status, steps, xcoin, chat, last-update and the Update button.
    for key in (
        BINARY_SENSOR_STATE,
        SENSOR_STEP_DAY,
        SENSOR_XCOIN,
        SENSOR_MESSAGE,
        SENSOR_LAST_UPDATE,
        BUTTON_UPDATE,
    ):
        assert key not in GUARDIAN_ONLY_KEYS


async def test_is_confirmed_contact_true_for_confirmed_contact(coordinator: XploraDataUpdateCoordinator) -> None:
    # Role resolved and not a Guardian (guardianType present, != "FIRST") -> confirmed Contact.
    coordinator.is_admin = {WUID: False}
    assert coordinator.is_confirmed_contact(WUID) is True


async def test_is_confirmed_contact_false_for_guardian(coordinator: XploraDataUpdateCoordinator) -> None:
    # guardianType == "FIRST" -> Guardian -> never restricted.
    coordinator.is_admin = {WUID: True}
    assert coordinator.is_confirmed_contact(WUID) is False


async def test_is_confirmed_contact_fails_open_for_unknown_role(coordinator: XploraDataUpdateCoordinator) -> None:
    # Role never resolved (e.g. deviceList carried no guardianType, so the wuid is absent from the
    # flag map) -> treated as a Guardian, so incomplete data never strips a real Guardian's
    # entities or blocks their control.
    coordinator.is_admin = {}
    assert coordinator.is_confirmed_contact(WUID) is False
