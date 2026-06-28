"""Direct per-watch action buttons for Xplora® Watch Version 2.

Each button is bound to a single watch, so pressing it runs the action for that child (and the
guardian/account that owns the config entry) directly -- the same effect as the matching service
(`reboot` / `shutdown` / `see`) but without having to select a target/user. All three are created
for every configured watch: the server authorizes who may `reboot`/`shutdown` -- a non-primary
guardian can do so (ref:XW-007) -- so the integration no longer pre-gates these on admin status.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.button import (
    ENTITY_ID_FORMAT,
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ID, CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_LAST_UPDATE_STATUS,
    ATTR_LAST_UPDATE_TIME,
    ATTR_WATCH,
    BUTTON_REBOOT,
    BUTTON_REFRESH_FUNCTIONS,
    BUTTON_SHUTDOWN,
    BUTTON_UPDATE,
    CONF_WATCHES,
    DOMAIN,
    LAST_UPDATE_ERROR,
)
from .coordinator import XploraDataUpdateCoordinator
from .entity import XploraBaseEntity
from .pyxplora_api.exception_classes import AuthError, RateLimitError
from .pyxplora_api.exception_classes import ConnectionError as XploraConnectionError

_LOGGER = logging.getLogger(__name__)

# All buttons are registered but disabled-by-default (like the non-core sensors): they appear on
# the device and can be enabled with one click, rather than every watch exposing action buttons by
# default. `reboot` carries the RESTART device class (standard restart icon/semantics); `update`
# and `shutdown` use explicit icons.
BUTTON_TYPES: tuple[ButtonEntityDescription, ...] = (
    ButtonEntityDescription(
        key=BUTTON_UPDATE,
        icon="mdi:refresh",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
    ButtonEntityDescription(
        key=BUTTON_REBOOT,
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
    # Re-fetches the slow-changing "functions" data (alarms, silent times, safe zones) on demand --
    # the only control that does, since `update` only refreshes location/battery. Descriptive name
    # ("Refresh Alarms & Silent Times") set in __init__ so the controls card reads clearly.
    ButtonEntityDescription(
        key=BUTTON_REFRESH_FUNCTIONS,
        icon="mdi:calendar-refresh",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
    ButtonEntityDescription(
        key=BUTTON_SHUTDOWN,
        icon="mdi:power",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the Xplora® Watch Version 2 action buttons from a config entry."""
    coordinator: XploraDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[XploraButton] = []
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

        # Only `CONF_WATCHES` gates creation; which buttons are visible is controlled per entity via
        # `entity_registry_enabled_default`, not an options-flow type selection.
        if conf_watches is None or wuid not in conf_watches:
            continue

        # All buttons are created for every watch; the server authorizes reboot/shutdown, so there
        # is no client-side admin gate on which buttons appear.
        for description in BUTTON_TYPES:
            entities.append(XploraButton(config_entry, coordinator, ward, wuid, description))

    async_add_entities(entities)


class XploraButton(XploraBaseEntity, ButtonEntity):
    """A direct action button bound to one Xplora® watch (reboot / shutdown / refresh)."""

    def __init__(
        self,
        config_entry: ConfigEntry,
        coordinator: XploraDataUpdateCoordinator,
        ward: dict[str, Any],
        wuid: str,
        description: ButtonEntityDescription,
    ) -> None:
        """Initialize an action button for an Xplora® Watch."""
        super().__init__(config_entry, description, coordinator, wuid)
        if self.watch_uid not in self.coordinator.data:
            return

        # has_entity_name: name only the role; the device supplies the "Kid One Watch" prefix.
        # `refresh_functions` is localized via translations (entity.button.refresh_functions.name --
        # a descriptive "Refresh Alarms & Silent Times", not the generic title-cased key); the other
        # buttons keep their code-derived English names.
        if description.key == BUTTON_REFRESH_FUNCTIONS:
            self._attr_translation_key = description.key
            display_name = "Refresh Alarms & Silent Times"  # debug-log only; UI name comes from translations
        else:
            self._attr_name = description.key.replace("_", " ").title()
            display_name = self._attr_name
        self.entity_id = ENTITY_ID_FORMAT.format(self.branded_object_id(description.key))

        # unique_id mirrors the other platforms so history/customizations are stable across upgrades.
        self._attr_unique_id = (
            f"{ward.get(CONF_NAME)}_{ATTR_WATCH}_{description.key}_{wuid}_{coordinator.user_id}".replace(" ", "_").replace("-", "_").lower()
        )
        _LOGGER.debug("Updating button: %s | Typ: %s | Watch_ID ...%s", display_name, description.key, wuid[25:])

    async def async_press(self) -> None:
        """Run the button's action for this watch (same effect as the matching service)."""
        key = self.entity_description.key

        # `update` is a manual refresh of just this watch -- the same call the `see` service makes.
        if key == BUTTON_UPDATE:
            self._log.debug("Update (refresh) pressed for watch ...%s", self.watch_uid[25:])
            try:
                await self.coordinator.async_update_xplora_data([self.watch_uid])
            except Exception as err:  # noqa: BLE001 -- record the failure for the UI, then surface it
                self._record_update_error()
                raise HomeAssistantError(f"Update failed: {err}") from err
            return

        # `refresh_functions` re-fetches alarms/silent times/safe zones on demand, bypassing the
        # functions-poll gate (same effect as the `refresh_functions` service).
        if key == BUTTON_REFRESH_FUNCTIONS:
            self._log.debug("Refresh functions pressed for watch ...%s", self.watch_uid[25:])
            try:
                await self.coordinator.async_refresh_functions([self.watch_uid])
            except Exception as err:  # noqa: BLE001 -- record the failure for the UI, then surface it
                self._record_update_error()
                raise HomeAssistantError(f"Refresh failed: {err}") from err
            return

        try:
            # The control mutations return an accept/reject Boolean (true = the backend accepted the
            # command). A False is a real failure -- typically the watch is off/offline so the server
            # refuses -- so surface it as an error the UI/automation can see, instead of silently
            # swallowing it (the card would otherwise report a success it never got).
            # Routed through the coordinator's centralized single-flight recovery gate so an expired
            # token is recovered once (bounded refresh -> at-most-one re-login -> one retry) before
            # the press fails.
            if key == BUTTON_REBOOT:
                result = await self.coordinator._with_recovery(lambda: self.coordinator.controller.reboot(self.watch_uid))
                self._log.debug("Reboot result: %s", result)
                if not result:
                    raise HomeAssistantError("the watch did not accept the command (it may be off or offline)")
            elif key == BUTTON_SHUTDOWN:
                result = await self.coordinator._with_recovery(lambda: self.coordinator.controller.shutdown(self.watch_uid))
                self._log.debug("Shutdown result: %s", result)
                if not result:
                    raise HomeAssistantError("the watch did not accept the command (it may be off or offline)")
        except AuthError as error:
            raise HomeAssistantError("Xplora session expired; retry after the integration re-authenticates") from error
        except RateLimitError as error:
            raise HomeAssistantError("Xplora API rate limit (HTTP 429); please retry later") from error
        except XploraConnectionError as error:
            raise HomeAssistantError(f"could not reach the Xplora server: {error}") from error

    def _record_update_error(self) -> None:
        """Mark this watch's last-update status as failed so the `last_update` sensor/cards reflect it."""
        # Debug breadcrumb for support: pairs with the HomeAssistantError raised by `async_press`
        # so a user's log shows the update failed for THIS watch (the error itself is logged by HA).
        self._log.debug("Update marked failed for watch ...%s", self.watch_uid[25:])
        data = self.coordinator.data or {}
        if self.watch_uid in data:
            data[self.watch_uid][ATTR_LAST_UPDATE_STATUS] = LAST_UPDATE_ERROR
            data[self.watch_uid][ATTR_LAST_UPDATE_TIME] = datetime.now().isoformat()
            self.coordinator.async_set_updated_data(data)
