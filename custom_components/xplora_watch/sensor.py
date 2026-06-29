"""Reads watch status from Xplora® Watch Version 2."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import ENTITY_ID_FORMAT, SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ID, CONF_LANGUAGE, CONF_NAME, PERCENTAGE, EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import (
    ATTR_ALARM,
    ATTR_HISTORY_POINTS,
    ATTR_HISTORY_TOTAL_POINTS,
    ATTR_HISTORY_WINDOW_HOURS,
    ATTR_LAST_UPDATE_STATUS,
    ATTR_LAST_UPDATE_TIME,
    ATTR_LOCATION_HISTORY,
    ATTR_SILENT,
    ATTR_TRACKER_LAT,
    ATTR_TRACKER_LNG,
    ATTR_WATCH,
    CONF_TIMEZONE,
    CONF_WATCHES,
    DEFAULT_LANGUAGE,
    DOMAIN,
    LAST_UPDATE_ERROR,
    LAST_UPDATE_NO_RESPONSE,
    LAST_UPDATE_OK,
    LOC_HISTORY_ATTR_WINDOW_HOURS,
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
from .coordinator import XploraDataUpdateCoordinator
from .entity import XploraBaseEntity
from .helper import get_location_distance_meter, week_repeat_to_localized_days, week_repeat_to_weekdays

_LOGGER = logging.getLogger(__name__)

# Battery is enabled by default (a core watch status); the rest are registered but
# disabled-by-default so they appear in the device and can be enabled with one click,
# rather than being hidden behind an options-flow type selection.
SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key=SENSOR_BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key=SENSOR_STEP_DAY,
        icon="mdi:run",
        native_unit_of_measurement="step",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=SENSOR_XCOIN,
        icon="mdi:hand-coin",
        native_unit_of_measurement="💰",
        device_class=SensorDeviceClass.MONETARY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=SENSOR_MESSAGE,
        icon="mdi:message",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=SENSOR_DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        device_class=SensorDeviceClass.DISTANCE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # Outcome of the last refresh for this watch (ok / no_response / error). Enabled by default --
    # unlike the other optional sensors -- so the overview/controls cards can surface "did the watch
    # actually respond?" without the user enabling anything. The `last_update_time` is an attribute.
    SensorEntityDescription(
        key=SENSOR_LAST_UPDATE,
        icon="mdi:cloud-refresh",
        device_class=SensorDeviceClass.ENUM,
        options=[LAST_UPDATE_OK, LAST_UPDATE_NO_RESPONSE, LAST_UPDATE_ERROR],
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

# One stable sensor per watch for each list. The state is the entry count and the full list
# lives in attributes, so entities no longer appear/disappear as the watch's alarms / silent
# windows change (the old per-entry switch model did). The custom card and the CRUD services
# read/drive these. `ATTR_ALARM` / `ATTR_SILENT` are the matching coordinator-data keys.
LIST_SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key=SENSOR_ALARMS,
        icon="mdi:alarm",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=SENSOR_SILENTS,
        icon="mdi:school",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)

# Maps each list sensor to the coordinator-data key holding its raw entries.
LIST_SENSOR_DATA_KEY: dict[str, str] = {
    SENSOR_ALARMS: ATTR_ALARM,
    SENSOR_SILENTS: ATTR_SILENT,
}

# Optional, opt-in location-history sensor (one per watch). Disabled-by-default like the other
# optional sensors; enabling it is what makes the coordinator issue the `LocHistory` request.
HISTORY_SENSOR_TYPE: SensorEntityDescription = SensorEntityDescription(
    key=SENSOR_LOCATION_HISTORY,
    icon="mdi:map-marker-path",
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the Xplora® Watch Version 2 sensors from config entry."""
    coordinator: XploraDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[SensorEntity] = []
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

        # Only `CONF_WATCHES` gates creation now; which sensors are visible is controlled by
        # each description's `entity_registry_enabled_default` (enable/disable per entity in
        # the UI), not by an options-flow type selection.
        if conf_watches is None or wuid not in conf_watches:
            continue

        for description in SENSOR_TYPES:
            entities.append(XploraSensor(config_entry, coordinator, ward, wuid, description))
        for description in LIST_SENSOR_TYPES:
            entities.append(XploraListSensor(config_entry, coordinator, ward, wuid, description))
        entities.append(XploraHistorySensor(config_entry, coordinator, ward, wuid, HISTORY_SENSOR_TYPE))

    async_add_entities(entities)


class XploraSensor(XploraBaseEntity, SensorEntity):
    """A sensor implementation for Xplora® Watch."""

    def __init__(
        self,
        config_entry: ConfigEntry,
        coordinator: XploraDataUpdateCoordinator,
        ward: dict[str, Any],
        wuid: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize a sensor for an Xplora® Watch."""
        super().__init__(config_entry, description, coordinator, wuid)
        if self.watch_uid not in self.coordinator.data:
            return

        # has_entity_name: name only the role; the device supplies the "Kid One Watch" prefix.
        self._attr_name: str = description.key.replace("_", " ").title()
        self.entity_id = ENTITY_ID_FORMAT.format(self.branded_object_id(description.key))

        # unique_id is kept unchanged to preserve existing entities' history/customizations.
        self._attr_unique_id = (
            f"{ward.get(CONF_NAME)}_{ATTR_WATCH}_{description.key}_{wuid}_{coordinator.user_id}".replace(" ", "_").replace("-", "_").lower()
        )
        _LOGGER.debug("Updating sensor: %s | Typ: %s | Watch_ID ...%s", self._attr_name, description.key, wuid[25:])

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if self.entity_description.key == SENSOR_BATTERY:
            battery: StateType = self.coordinator.data[self.watch_uid].get(SENSOR_BATTERY, None)
            return battery
        if self.entity_description.key == SENSOR_STEP_DAY:
            step_day: StateType = self.coordinator.data[self.watch_uid].get(SENSOR_STEP_DAY, 0)
            return step_day
        if self.entity_description.key == SENSOR_XCOIN:
            xcoin: StateType = self.coordinator.data[self.watch_uid].get(SENSOR_XCOIN, 0)
            return xcoin
        if self.entity_description.key == SENSOR_MESSAGE:
            unread_msg: StateType = self.coordinator.data[self.watch_uid].get("unreadMsg", 0)
            return unread_msg
        if self.entity_description.key == SENSOR_DISTANCE:
            lat = self.coordinator.data[self.watch_uid].get(ATTR_TRACKER_LAT, None)
            lng = self.coordinator.data[self.watch_uid].get(ATTR_TRACKER_LNG, None)
            if lat and lng:
                lat_lng: tuple[float, float] = (float(lat), float(lng))
                return get_location_distance_meter(self.hass, lat_lng)
            return -1
        if self.entity_description.key == SENSOR_LAST_UPDATE:
            status: StateType = self.coordinator.data[self.watch_uid].get(ATTR_LAST_UPDATE_STATUS, None)
            return status
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return state attributes that should be added to SENSOR_STATE."""
        data = super().extra_state_attributes or {}
        if self.entity_description.key == SENSOR_LAST_UPDATE:
            return dict(
                data,
                **{ATTR_LAST_UPDATE_TIME: self.coordinator.data[self.watch_uid].get(ATTR_LAST_UPDATE_TIME)},
                user=self.coordinator.controller.getUserName(),
            )
        if self.entity_description.key is SENSOR_MESSAGE:
            # `entry_id`, `wuid` and `account_user_id` are STATIC per-watch identifiers, surfaced
            # unconditionally -- even before any chat has been fetched. The custom chat card reads
            # them to target the message services (`send_message` / `read_message`): the `user`
            # field is resolved from `entry_id`, `target` is the `wuid` (same convention as the
            # alarm/silent list sensors), and `account_user_id` (the logged-in user's id) lets the
            # card tell outgoing messages (we sent them, so `sender.id` is our id) from incoming
            # ones -- see `sendText`'s "sender is login User". They must NOT be gated on a non-empty
            # chat dict: chats are fetched only by the `read_message` service, which the card can
            # only call once it has these ids -- gating them deadlocked a cold start (no chats -> no
            # ids -> the card can't fetch -> still no chats). The chat payload (`list`, ...) is
            # merged in once `read_message` has populated it.
            messages = ((self.coordinator.data or {}).get(self.watch_uid) or {}).get(SENSOR_MESSAGE) or {}
            return dict(
                data,
                entry_id=self._config_entry.entry_id,
                wuid=self.watch_uid,
                account_user_id=self.coordinator.user_id,
                **messages,
            )
        return dict(data, user=self.coordinator.controller.getUserName())


class XploraListSensor(XploraBaseEntity, SensorEntity):
    """A stable, one-per-watch sensor exposing the full alarm or silent-time list.

    The state is the number of entries; the entries themselves (id, time(s), repeat days and
    status) are exposed as a list attribute. Because the entity is fixed per watch, the list can
    grow/shrink without entities appearing or disappearing -- the old per-entry switch model's
    main drawback. The CRUD services and the custom Lovelace card read/drive this.
    """

    def __init__(
        self,
        config_entry: ConfigEntry,
        coordinator: XploraDataUpdateCoordinator,
        ward: dict[str, Any],
        wuid: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize a list sensor for an Xplora® Watch."""
        super().__init__(config_entry, description, coordinator, wuid)
        if self.watch_uid not in self.coordinator.data:
            return

        self._data_key = LIST_SENSOR_DATA_KEY[description.key]

        # has_entity_name: name only the role; the device supplies the "Kid One Watch" prefix.
        self._attr_name = description.key.replace("_", " ").title()
        self.entity_id = ENTITY_ID_FORMAT.format(self.branded_object_id(description.key))

        # unique_id mirrors the other sensors so history/customizations are stable across upgrades.
        self._attr_unique_id = (
            f"{ward.get(CONF_NAME)}_{ATTR_WATCH}_{description.key}_{wuid}_{coordinator.user_id}".replace(" ", "_").replace("-", "_").lower()
        )
        _LOGGER.debug("Updating sensor: %s | Typ: %s | Watch_ID ...%s", self._attr_name, description.key, wuid[25:])

    def _entries(self) -> list[dict[str, Any]]:
        """Return the raw alarm / silent entries for this watch from the coordinator."""
        watch_data = self.coordinator.data.get(self.watch_uid, {}) if self.coordinator.data else {}
        entries = watch_data.get(self._data_key, [])
        return entries if isinstance(entries, list) else []

    @property
    def native_value(self) -> StateType:
        """Return the number of entries in the list."""
        return len(self._entries())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the full list of entries plus localized repeat days for each."""
        data = super().extra_state_attributes or {}
        language = self._options.get(CONF_LANGUAGE, self._data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE))
        items: list[dict[str, Any]] = []
        for entry in self._entries():
            week_repeat = entry.get("weekRepeat", "")
            items.append(
                dict(
                    entry,
                    weekdays=week_repeat_to_weekdays(week_repeat),
                    days=week_repeat_to_localized_days(week_repeat, language),
                )
            )
        # `entry_id` and `wuid` are surfaced so the custom card can target the CRUD services
        # (the `user` service field is resolved from the entry id; `target` is the wuid).
        return dict(
            data,
            user=self.coordinator.controller.getUserName(),
            entry_id=self._config_entry.entry_id,
            wuid=self.watch_uid,
            **{self._data_key: items},
        )


class XploraHistorySensor(XploraBaseEntity, SensorEntity):
    """A one-per-watch sensor exposing the watch's accumulated location history.

    The state is the number of points in the bounded recent window; the points themselves are a
    list attribute the custom card reads to draw a map track. The full retained set (which can span
    far more than the app's ~3-day window) lives only in the coordinator's history Store and is
    reached via the `xplora_watch/location_history` websocket command -- the attribute is bounded
    so it stays small. `history_points` is intentionally excluded from the recorder (see
    `_unrecorded_attributes`) so the point list never bloats the DB nor risks attribute truncation.
    """

    # Keep the (already bounded) point list out of the recorder: only the small count state is
    # worth recording; recording a per-poll geo-point list would bloat the DB for no benefit.
    _unrecorded_attributes = frozenset({ATTR_HISTORY_POINTS})

    def __init__(
        self,
        config_entry: ConfigEntry,
        coordinator: XploraDataUpdateCoordinator,
        ward: dict[str, Any],
        wuid: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the location-history sensor for an Xplora® Watch."""
        super().__init__(config_entry, description, coordinator, wuid)
        if self.watch_uid not in self.coordinator.data:
            return

        # has_entity_name: name only the role; the device supplies the "Kid One Watch" prefix.
        self._attr_name = description.key.replace("_", " ").title()
        self.entity_id = ENTITY_ID_FORMAT.format(self.branded_object_id(description.key))
        # unique_id mirrors the other sensors so history/customizations are stable across upgrades.
        self._attr_unique_id = (
            f"{ward.get(CONF_NAME)}_{ATTR_WATCH}_{description.key}_{wuid}_{coordinator.user_id}".replace(" ", "_").replace("-", "_").lower()
        )
        _LOGGER.debug("Updating sensor: %s | Typ: %s | Watch_ID ...%s", self._attr_name, description.key, wuid[25:])

    def _history(self) -> dict[str, Any]:
        """Return the bounded `{points, total}` history slice for this watch from the coordinator."""
        watch_data = self.coordinator.data.get(self.watch_uid, {}) if self.coordinator.data else {}
        history = watch_data.get(ATTR_LOCATION_HISTORY, {})
        return history if isinstance(history, dict) else {}

    @property
    def native_value(self) -> StateType:
        """Return the number of points in the bounded recent window."""
        points = self._history().get("points", [])
        return len(points) if isinstance(points, list) else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the bounded recent points (for the card) plus the full retained count.

        `entry_id` / `wuid` let the card target the `xplora_watch/location_history` websocket
        command for ranges longer than the bounded window, matching the list sensors' convention.
        """
        data = super().extra_state_attributes or {}
        history = self._history()
        points = history.get("points", [])
        return dict(
            data,
            user=self.coordinator.controller.getUserName(),
            entry_id=self._config_entry.entry_id,
            wuid=self.watch_uid,
            # The watch's configured timezone, so the card builds day keys/labels for the same
            # calendar days the user (and the app) see, regardless of the browser's timezone.
            timezone=self._data.get(CONF_TIMEZONE),
            # The days (YYYY-MM-DD) already cached for this watch, so the card's selector can offer
            # archived days (built up by the daily `fetch_history` service) next to the recent ones.
            history_days=self.coordinator.cached_history_days(self.watch_uid),
            **{
                ATTR_HISTORY_POINTS: points if isinstance(points, list) else [],
                ATTR_HISTORY_TOTAL_POINTS: history.get("total", 0),
                ATTR_HISTORY_WINDOW_HOURS: LOC_HISTORY_ATTR_WINDOW_HOURS,
            },
        )
