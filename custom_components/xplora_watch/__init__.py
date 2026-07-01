"""Support for Xplora® Watch Version 2."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import slugify

from .config import resolve_account_alias
from .const import ATTR_WATCH, DATA_HASS_CONFIG, DOMAIN, GUARDIAN_ONLY_KEYS
from .coordinator import XploraDataUpdateCoordinator
from .helper import account_token, async_register_frontend_card, create_www_directory
from .services import async_setup_services, async_unload_services
from .websocket import async_register_websocket_commands

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.DEVICE_TRACKER, Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, hass_config: ConfigType) -> bool:
    """Set up the HA Xplora® Watch component."""
    # Log the integration version once at component load (it is integration-wide, not per entry).
    # This is the version support actually needs -- read from manifest.json, the single source of
    # truth -- replacing the old, misleading "pyxplora_api lib version" (a frozen upstream-fork tag
    # that no longer changed once the client was adopted into this repo).
    integration = await async_get_integration(hass, DOMAIN)
    _LOGGER.debug("Setting up Xplora® Watch integration (version %s)", integration.version)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][DATA_HASS_CONFIG] = hass_config
    # Register the bundled Lovelace card here (component load), NOT in async_setup_entry: entry
    # setup is gated behind a network login + first refresh, so registering there leaves a window
    # right after an HA restart where the dashboard renders before the card's JS exists -> HA shows
    # a "custom element doesn't exist" config-error card until the slow setup finishes and you
    # reload. async_setup runs early and has no network dependency, closing that window.
    await async_register_frontend_card(hass)
    # Register websocket commands here (component load), so the card can query location history as
    # soon as it renders -- no network dependency, available before the entry's network-gated setup.
    async_register_websocket_commands(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configure based on config entry."""
    _LOGGER.debug("Configure based on config entry %s", entry.entry_id)

    # Create and initialize a session for the entity process.
    coordinator: XploraDataUpdateCoordinator = XploraDataUpdateCoordinator(hass, entry)
    session = aiohttp_client.async_get_clientsession(hass)

    await coordinator.init(session=session)

    await _async_migrate_entries(hass, entry, coordinator.user_id)
    _async_remove_orphaned_switch_entities(hass, entry)

    await coordinator.async_config_entry_first_refresh()
    wuids = coordinator.controller.getWatchUserIDs()
    username = coordinator.username

    # Runs after the first refresh (unlike the switch sweep above): the per-watch Guardian/Contact
    # role it keys off is derived from `deviceList.guardianType` during that refresh, so it isn't
    # known earlier. Removes Guardian-only entities a previous version may have created for a watch
    # the account is only a Contact of, before the platforms (re)create the allowed ones below.
    _async_remove_contact_guardian_only_entities(hass, entry, coordinator)

    # Rename any pre-token default slugs to the tokened slug so an upgraded install matches the
    # entities the platforms now create. Runs before platform setup (below), so when the platforms
    # reconcile by unique_id they find each entity already at its final slug. Reuses `wuids` (no
    # extra fetch); the user_id was resolved in init().
    await _async_migrate_entity_slugs(hass, entry, coordinator, wuids)

    for wuid in wuids:
        # Read the admin flag the coordinator already resolved during init() -- re-calling
        # isAdmin() here would fire a second `Contacts` request per watch for a log line. This is
        # purely informational: not being the primary guardian no longer blocks any action.
        if not coordinator.is_admin.get(wuid):
            watch_name = coordinator.controller.getWatchUserNames(wuid)
            _LOGGER.info("%s is not the primary guardian of the watch from %s (%s)!", username, watch_name, wuid)

    hass.data.setdefault(DOMAIN, {})

    hass.data[DOMAIN][entry.entry_id] = coordinator
    coordinator.setup_history_scheduler()
    entry.async_on_unload(coordinator.async_teardown)
    await async_setup_services(hass, entry.entry_id)

    await create_www_directory(hass)
    # The Lovelace card is registered in async_setup (component load) so it is available before this
    # network-gated entry setup runs -- see the note there. (async_register_frontend_card is
    # idempotent, so a missed async_setup would still be covered, but the early call is the point.)
    # `services.yaml` is now a static, device-targeted file (ADR 0003): services target HA devices,
    # so there is no per-setup regeneration that would write account names / watch ids to disk.

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(options_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Deliberately does NOT log out: unload runs on every reload/restart/options-change, so
    expiring the token here would force a fresh login on the next setup -- reintroducing the
    per-poll re-login churn this integration was reworked to avoid. Server-side logout happens
    only on actual deletion, in `async_remove_entry`.
    """
    _LOGGER.debug("Unload a config entry")
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    async_unload_services(hass)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Invalidate the session token server-side when the integration is removed.

    Runs only on actual deletion, after `async_unload_entry` has already popped the live
    coordinator from `hass.data`, so there is no controller to reuse -- we build a fresh one,
    log in once to obtain a bearer, then `ExpireToken` it so no valid "ghost" session is left
    on Xplora's servers (the token otherwise lives ~35 days). Best-effort: a network/auth
    failure here must never block removal of the entry.
    """
    _LOGGER.debug("Removing config entry %s: logging out server-side", entry.entry_id)
    coordinator = XploraDataUpdateCoordinator(hass, entry)
    try:
        session = aiohttp_client.async_get_clientsession(hass)
        await coordinator.set_controller(session)
        await coordinator.controller.init(forceLogin=True)
        acknowledged = await coordinator.controller.logout()
        _LOGGER.debug("Server-side logout on removal (acknowledged: %s)", acknowledged)
    except Exception as err:  # noqa: BLE001 -- removal must succeed regardless of network/auth state
        _LOGGER.debug("Server-side logout on removal failed (ignored): %s", err)
    finally:
        # Always drop the persisted token from `.storage` so a re-add doesn't resurrect a dead
        # session, even if the server-side logout above failed.
        await coordinator.async_clear_persisted_session()


async def options_update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Handle options update."""
    _LOGGER.debug("Configuration options updated, reloading Xplora® Watch Version 2 integration")
    await hass.config_entries.async_reload(config_entry.entry_id)


@callback
def _async_remove_orphaned_switch_entities(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Purge leftover alarm/silent `switch.*` entities from a pre-upgrade install.

    The per-entry alarm/silent switches were replaced by the `*_alarms` / `*_silents` list
    sensors. Home Assistant keeps registry entries after a platform stops providing them (to
    preserve history/customizations), so without this they would linger forever as
    "Unavailable". The integration only ever created `switch`-domain entities for alarms/silents,
    so removing every `switch` owned by this config entry is safe and one-shot (idempotent: once
    gone there is nothing left to remove on subsequent setups).
    """
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, config_entry.entry_id):
        if entity.domain == Platform.SWITCH.value:
            _LOGGER.debug("Removing orphaned alarm/silent switch entity '%s'", entity.entity_id)
            entity_registry.async_remove(entity.entity_id)


def _normalize(value: str) -> str:
    """Normalize an id segment the way the entity platforms build their unique ids.

    The platforms lower-case and turn spaces/dashes into underscores when composing a unique id;
    mirroring that here lets registry-maintenance code match a watch id or account id against the
    stored, already-normalized segments.
    """
    return value.replace(" ", "_").replace("-", "_").lower()


def _is_guardian_only_kind(id_prefix: str) -> bool:
    """Whether ``{ward}_watch_{kind}`` -- a unique id with the trailing ``_{wuid}_...`` removed --
    names a Guardian-only entity kind.

    The kind is the token after the *last* ``_watch_`` segment of the prefix. Matching it there
    (rather than as a bare substring of the whole id) means a Ward (child) named like an entity kind
    -- e.g. "Battery" -- which sits *before* that segment can't cause a false match. The caller
    strips the wuid first, so a wuid that itself starts with "watch" can't be taken for the segment.
    """
    _, watch_segment, kind = id_prefix.rpartition(f"_{ATTR_WATCH}_")
    return bool(watch_segment) and kind in GUARDIAN_ONLY_KEYS


@callback
def _async_remove_contact_guardian_only_entities(
    hass: HomeAssistant, config_entry: ConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    """Purge Guardian-only entities left over for a watch the account is only a *Contact* of.

    A Contact is sent no battery/location/alarm data and may not control the watch, so those
    entities (battery, distance, the alarm/silent lists and location-history sensors; the charging
    and safe-zone binary sensors; the reboot/shutdown/refresh-functions buttons; and every device
    tracker) are no longer created for a confirmed-Contact watch (ref:XW-009). Home Assistant keeps a
    registry entry after a platform stops providing it, so a user upgrading from a version that
    created them would keep seeing them permanently "Unavailable" -- this one-shot, idempotent sweep
    removes them. A Guardian's watch is untouched, as are a Contact's kept entities (online, steps,
    xcoin, chat, last-update, the Update button). Fails open via `is_confirmed_contact`: an
    unknown/unresolved role is treated as a Guardian, so incomplete data never strips a Guardian's
    entities.
    """

    # A watch's entities all carry its (opaque, globally unique) wuid as a `_{wuid}_` segment of
    # their unique id, normalized the way the platforms build ids (lower-cased, spaces/dashes ->
    # underscores). Match on that segment to pin the watch: a Guardian watch on the same account is
    # never touched, and the match survives `_async_migrate_entries` having reshaped the trailing
    # `_{user_id}` above (it leaves the wuid segment intact).
    contact_markers = [f"_{_normalize(wuid)}_" for wuid in coordinator.is_admin if coordinator.is_confirmed_contact(wuid)]
    if not contact_markers:
        return

    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, config_entry.entry_id):
        unique_id = str(entity.unique_id)
        marker = next((m for m in contact_markers if m in unique_id), None)
        if marker is None:
            continue  # belongs to a Guardian watch (or no known watch) -- leave it
        # Every device tracker (the watch tracker and the per-safezone trackers) is location-based,
        # so a Contact gets none; the other Guardian-only kinds are matched on the `{ward}_watch_{kind}`
        # token ahead of the wuid (split off here so the wuid can't interfere with the match).
        if entity.domain == Platform.DEVICE_TRACKER.value or _is_guardian_only_kind(unique_id.split(marker, 1)[0]):
            _LOGGER.debug("Removing Guardian-only entity '%s' registered for a contact watch", entity.entity_id)
            entity_registry.async_remove(entity.entity_id)


async def _async_migrate_entries(hass: HomeAssistant, config_entry: ConfigEntry, new_uid: str) -> bool:
    """Migrate old entry."""
    entity_registry = er.async_get(hass)

    @callback
    def update_unique_id(entry: er.RegistryEntry) -> dict[str, str] | None:
        if (
            new_uid in str(entry.unique_id)
            and "_" in str(entry.unique_id)
            and "-" not in str(entry.unique_id)
            and " " not in str(entry.unique_id)
        ):
            return None
        if new_uid in str(entry.unique_id) and "-" in str(entry.unique_id):
            new_unique_id = f"{entry.unique_id}".replace("-", "_").replace(" ", "_").lower()
        else:
            # "{ward.get(CONF_NAME)}-{ATTR_WATCH}-{description.key}-{wuid}"                            old
            # "{ward.get(CONF_NAME)}_{ATTR_WATCH}_{description.key}_{wuid}_{self.coordinator.user_id}" new
            new_unique_id = f"{entry.unique_id}_{new_uid}".replace("-", "_").replace(" ", "_").lower()

        _LOGGER.debug("change unique_id - entity: '%s' unique_id from '%s' to '%s'", entry.entity_id, entry.unique_id, new_unique_id)
        if existing_entity_id := entity_registry.async_get_entity_id(entry.domain, entry.platform, new_unique_id):
            _LOGGER.debug("Cannot change unique_id to '%s', already exists for '%s'", new_unique_id, existing_entity_id)
            return None
        return {"new_unique_id": new_unique_id}

    await er.async_migrate_entries(hass, config_entry.entry_id, update_unique_id)

    return True


async def _async_migrate_entity_slugs(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: XploraDataUpdateCoordinator,
    wuids: list[str],
) -> None:
    """Force-migrate pre-existing default entity slugs to the tokened slug (one-time, idempotent).

    The integration began appending the per-account token to every entity slug
    (`xplora_<ward>_watch_<key>` -> `..._<token>`) so the same watch linked to several accounts no
    longer collides. Newly-created entities already carry it; this renames the entities an existing
    install created *before* the token, so old and new entities look the same (no temporal
    asymmetry). The entity-registry rename carries history and long-term statistics with it; the
    `unique_id` is left untouched (it already disambiguates per account).

    Only an entity whose current `entity_id` still equals the slug the platform would have generated
    before the token (its old default) is migrated -- a user-renamed entity is left alone. Idempotent:
    an entity already at its tokened slug is skipped, so a second run is a no-op. A pre-alias entry
    migrates using the fallback token (account display name -> opaque account id).
    """
    token = account_token(resolve_account_alias(config_entry), coordinator.username, coordinator.user_id)
    # The slugified token is the only account-derived slug segment; if it is empty there is nothing
    # to append and every entity is already at its final slug.
    if not slugify(token):
        return

    user_tail = f"_{_normalize(coordinator.user_id)}"
    # The `_{wuid}_{user_id}` tail of each entity's unique id, normalized the way the platforms build
    # ids. Stripping it recovers the pre-token `{ward}_watch_{key}` core the slug is built from. An
    # entity whose unique id lacks a known watch's tail (or whose slug isn't derived from that core,
    # e.g. a safezone tracker keyed by vendor id) won't match its old default below and is left as-is.
    watch_tails = [f"_{_normalize(wuid)}{user_tail}" for wuid in wuids]

    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, config_entry.entry_id):
        unique_id = str(entity.unique_id)
        tail = next((t for t in watch_tails if unique_id.endswith(t)), None)
        if tail is None:
            continue  # not one of this account's known watches -- leave it
        core = unique_id[: -len(tail)]
        old_object_id = slugify(f"xplora_{core}")
        new_object_id = slugify(f"xplora_{core} {token}")
        current_object_id = entity.entity_id.split(".", 1)[1]
        if current_object_id == new_object_id:
            continue  # already tokened -- idempotent no-op
        if current_object_id != old_object_id:
            # A known watch's entity we chose not to touch: either the user renamed it, or its slug
            # isn't derived from its unique-id core (so `old_object_id` is a wrong guess). Leaving it
            # is the fail-safe, but log at DEBUG so an unexpected under-migration is observable rather
            # than silent (e.g. if a future change shifts how slugs are derived).
            _LOGGER.debug(
                "Skipping slug migration for '%s': current object id '%s' is not the pre-token default '%s'",
                entity.entity_id,
                current_object_id,
                old_object_id,
            )
            continue
        new_entity_id = f"{entity.domain}.{new_object_id}"
        if entity_registry.async_get(new_entity_id) is not None:
            _LOGGER.debug("Cannot migrate slug '%s' -> '%s': target already exists", entity.entity_id, new_entity_id)
            continue
        _LOGGER.debug("Migrating entity slug '%s' -> '%s'", entity.entity_id, new_entity_id)
        entity_registry.async_update_entity(entity.entity_id, new_entity_id=new_entity_id)
