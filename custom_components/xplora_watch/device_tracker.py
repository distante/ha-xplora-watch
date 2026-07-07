"""Support for Xplora® Watch Version 2 tracking."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import ENTITY_ID_FORMAT, SourceType, TrackerEntity
from homeassistant.components.device_tracker.const import ATTR_LOCATION_NAME
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ID, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    ATTR_SERVICE_USER,
    ATTR_TRACKER_ADDR,
    ATTR_TRACKER_DISTOHOME,
    ATTR_TRACKER_IMEI,
    ATTR_TRACKER_LAST_TRACK,
    ATTR_TRACKER_LAT,
    ATTR_TRACKER_LICENCE,
    ATTR_TRACKER_LNG,
    ATTR_TRACKER_POI,
    ATTR_TRACKER_RAD,
    ATTR_TRACKER_SAFEZONE_NAME,
    ATTR_WATCH,
    CONF_WATCHES,
    DOMAIN,
)
from .coordinator import XploraDataUpdateCoordinator
from .entity import XploraBaseEntity
from .helper import download_image_to_file, get_location_distance_meter

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the Xplora® Watch Version 2 tracker from config entry."""
    coordinator: XploraDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[XploraDeviceTracker | XploraSafezoneTracker] = []

    for watch in coordinator.controller.watchs:
        options = config_entry.options
        if not options or not isinstance(watch, dict):
            _LOGGER.debug("%s %s - no config options", watch, config_entry.entry_id)
            continue

        ward = watch.get("ward", None)
        if not isinstance(ward, dict):
            continue

        wuid = ward.get(ATTR_ID)
        if wuid is None:
            continue

        conf_watches = options.get(CONF_WATCHES, None)

        # Only `CONF_WATCHES` gates creation now. The watch tracker is enabled by default; the
        # per-safezone trackers are registered disabled-by-default (see XploraSafezoneTracker)
        # so they appear per watch and can be enabled individually.
        if conf_watches is None or wuid not in conf_watches:
            continue

        # A Contact of the watch is sent no location data, so every tracker (the watch tracker and
        # the per-safezone trackers) would sit permanently unavailable -- create none for a watch the
        # account is only a Contact of (ref:XW-009). Fails open: an unknown/unresolved role is
        # treated as a Guardian, so a real Guardian still gets its trackers.
        if coordinator.is_confirmed_contact(wuid):
            continue

        # Reuse the safe-zone definitions the coordinator already fetched (the first refresh seeds
        # them, see `_setDevice`), instead of issuing another `SafeZones` request here. That extra
        # per-setup call ignored the functions-poll interval and duplicated data already in hand.
        safe_zones = coordinator.controller.getDevice(wuid).get("getWatchSafeZones") or []
        for safe_zone in safe_zones:
            entities.append(XploraSafezoneTracker(config_entry, safe_zone, coordinator, wuid, ward))

        # No photo cached/available falls back to `_attr_icon` (mdi:watch) instead of a remote
        # placeholder image -- avoids depending on Xplora's S3-hosted default icon entirely.
        image_url = coordinator.data[wuid].get("entity_picture", None)
        image = None
        if image_url:
            session = async_get_clientsession(hass)
            if await download_image_to_file(hass, session, image_url, wuid):
                image = f"/local/image/{wuid}.jpeg"
        entities.append(XploraDeviceTracker(hass, config_entry, coordinator, wuid, ward, image))
    async_add_entities(entities)


class XploraSafezoneTracker(XploraBaseEntity, TrackerEntity, RestoreEntity):
    """Creates a safezone tracker."""

    _attr_force_update: bool = False
    _attr_icon: str | None = "mdi:crosshairs-gps"
    # Disabled by default: one tracker per configured safezone; users opt in per entity.
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        config_entry: ConfigEntry,
        safezone: dict[str, Any],
        coordinator: XploraDataUpdateCoordinator,
        wuid: str,
        ward: dict[str, Any],
    ) -> None:
        """Initialize XploraSafezoneTracker instance."""
        super().__init__(config_entry, None, coordinator, wuid)
        self._safezone = safezone

        # has_entity_name: name only the role; the device supplies the "Kid One Watch" prefix.
        self._attr_name = f"Safezone {safezone[CONF_NAME]}".replace("_", " ").title()
        self.entity_id = ENTITY_ID_FORMAT.format(self.branded_object_id("safezone", safezone[CONF_NAME]))

        # unique_id is kept unchanged to preserve existing entities' history/customizations.
        self._attr_unique_id = (
            f"{ward.get(CONF_NAME)}_{ATTR_WATCH}_safezone_{safezone['vendorId']}_{wuid}_{coordinator.user_id}".replace(" ", "_")
            .replace("-", "_")
            .lower()
        )

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        return float(self._safezone[ATTR_TRACKER_LAT])

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        return float(self._safezone[ATTR_TRACKER_LNG])

    @property
    def source_type(self) -> SourceType | str:
        """Return the source type, eg gps or router, of the device."""
        return SourceType.GPS

    @property
    def location_accuracy(self) -> int:
        """Return the gps accuracy of the device."""
        accuracy: int = self._safezone[ATTR_TRACKER_RAD]
        return accuracy

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return state attributes that should be added to SAFEZONE_STATE.

        The safezone's name lives here (`safezone_name`), not in the entity state: the deprecated
        `location_name` override is gone (HA 2027.7 removal), so the state is HA-computed from the
        zone's coordinates (`not_home`, or the name of a containing HA zone).
        """
        data = super().extra_state_attributes or {}
        return dict(
            data,
            **{
                ATTR_TRACKER_ADDR: self._safezone[ATTR_TRACKER_ADDR],
                ATTR_TRACKER_SAFEZONE_NAME: self._safezone[CONF_NAME],
            },
        )


class XploraDeviceTracker(XploraBaseEntity, TrackerEntity):
    """Xplora® Watch Version 2 device tracker."""

    _attr_force_update: bool = False
    _attr_icon: str | None = "mdi:watch"

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: XploraDataUpdateCoordinator,
        wuid: str,
        ward: dict[str, Any],
        image: str | None,
    ) -> None:
        """Initialize the Tracker."""
        super().__init__(config_entry, None, coordinator, wuid)
        if self.watch_uid not in coordinator.data:
            return

        self._hass = hass

        # has_entity_name: name only the role; the device supplies the "Kid One Watch" prefix.
        self._attr_name = "Tracker"
        self.entity_id = ENTITY_ID_FORMAT.format(self.branded_object_id("tracker"))

        # unique_id is kept unchanged to preserve existing entities' history/customizations.
        self._attr_unique_id = (
            f"{ward.get(CONF_NAME)}_{ATTR_WATCH}_Tracker_{wuid}_{coordinator.user_id}".replace(" ", "_").replace("-", "_").lower()
        )

        self._attr_entity_picture = image

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        lat: float | None = self.coordinator.data[self.watch_uid].get(ATTR_TRACKER_LAT, None)
        return lat

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        lng: float | None = self.coordinator.data[self.watch_uid].get(ATTR_TRACKER_LNG, None)
        return lng

    @property
    def source_type(self) -> SourceType | str:
        """Return the source type, eg gps or router, of the device."""
        return SourceType.GPS

    @property
    def location_accuracy(self) -> int:
        """Return the gps accuracy of the device."""
        accuracy: int = self.coordinator.data[self.watch_uid].get("location_accuracy", 0)
        return accuracy

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return state attributes that should be added to DEVICE_STATE."""
        data = super().extra_state_attributes or {}
        distance_to_home = None

        if self.coordinator.data[self.watch_uid].get(ATTR_TRACKER_LAT, None) and self.coordinator.data[self.watch_uid].get(
            ATTR_TRACKER_LNG, None
        ):
            lat_lng: tuple[float, float] = (
                float(self.coordinator.data[self.watch_uid].get(ATTR_TRACKER_LAT, None)),
                float(self.coordinator.data[self.watch_uid].get(ATTR_TRACKER_LNG, None)),
            )
            distance_to_home = get_location_distance_meter(self._hass, lat_lng)

        return dict(
            data,
            **{
                ATTR_SERVICE_USER: self.coordinator.username,
                ATTR_TRACKER_DISTOHOME: distance_to_home,
                ATTR_TRACKER_ADDR: (self.coordinator.data[self.watch_uid].get(ATTR_LOCATION_NAME, None) if distance_to_home else None),
                # Ungated from distance-to-home (ADR 0007): the fix time is the age of the shown
                # position, so it must survive even at home (distance 0.0 is falsy) -- else the one
                # place a stale pin most needs a "captured N min ago" label would blank it.
                ATTR_TRACKER_LAST_TRACK: self.coordinator.data[self.watch_uid].get("lastTrackTime", None),
                ATTR_TRACKER_IMEI: self.coordinator.data[self.watch_uid].get(ATTR_TRACKER_IMEI, None),
                ATTR_TRACKER_POI: self.coordinator.data[self.watch_uid].get(ATTR_TRACKER_POI, None),
                ATTR_TRACKER_LICENCE: self.coordinator.data[self.watch_uid].get(ATTR_TRACKER_LICENCE, None),
            },
        )
