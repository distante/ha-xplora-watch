"""HelperClasses Xplora® Watch Version 2."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import TYPE_CHECKING

import aiohttp
from geopy import distance
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DATA_FRONTEND_REGISTERED,
    DATA_MEDIA_PATHS_REGISTERED,
    DAYS,
    DEFAULT_LANGUAGE,
    DOMAIN,
    FRONTEND_SCRIPT_FILE,
    FRONTEND_SCRIPT_URL,
    HOME,
    WEEKDAY_KEYS,
)

if TYPE_CHECKING:
    from .pyxplora_api.pyxplora_api_async import PyXploraApi

_LOGGER = logging.getLogger(__name__)


def watch_user_label(controller: PyXploraApi, wuid: str) -> str:
    """Human-readable label for a watch id: the child's name, falling back to the id.

    `getWatchUserNames(str)` returns the child's name when the watch is known, but `[]`
    when it is not, so guard for a non-empty string before using it.
    """
    name = controller.getWatchUserNames(wuid)
    return name if isinstance(name, str) and name else wuid


def account_token(alias: str, display_name: str, user_id: str) -> str:
    """Per-account differentiator for a watch's device name (and, when it slugifies, its entity slug).

    The same physical watch can be linked to several accounts (dad + mom + brother); the token
    tells those copies apart in the UI. Resolution order is **Account alias -> Account display
    name (`getUserName()`) -> opaque account id (`getUserID()`)**: the user-set alias wins when
    present, then the Account display name when non-empty, otherwise the opaque account id (which
    always exists, so the returned *string* is never empty and the device name always has a label).

    That non-empty string drives the device name verbatim ("Dana Watch (👍)"). It does **not**
    guarantee an entity-slug segment: a non-slugifiable alias (emoji / punctuation-only) is returned
    as-is here but drops out of the slug (see `entity.branded_object_id`), so the entity id falls
    back to its pre-token form and Home Assistant de-duplicates any collision with a numeric suffix.
    That is cosmetic; we deliberately do not reject or rewrite such an alias.
    """
    if alias.strip():
        return alias
    if display_name.strip():
        return display_name
    return user_id


async def async_register_frontend_card(hass: HomeAssistant) -> None:
    """Serve and register the bundled custom Lovelace card as a frontend JS module (once).

    Registers a static path for `www/xplora-watch-card.js` and adds it as an extra module URL so
    the card is available in dashboards without the user manually adding a Lovelace resource. The
    `DATA_FRONTEND_REGISTERED` flag makes this idempotent across config entries and reloads (the
    static-path registration would otherwise raise on the second call).
    """
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig
    from homeassistant.loader import async_get_integration

    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(DATA_FRONTEND_REGISTERED):
        return

    # The HTTP integration may not be ready (or present at all, e.g. in unit tests). The card is
    # optional polish -- the sensors + services work without it -- so never let a missing/failed
    # frontend registration abort the whole config-entry setup.
    if getattr(hass, "http", None) is None:
        _LOGGER.debug("hass.http unavailable; skipping frontend card registration")
        return

    card_path = os.path.join(os.path.dirname(__file__), "www", FRONTEND_SCRIPT_FILE)
    try:
        # Cache-bust the module URL with the integration version so browsers fetch the new card
        # after an update instead of serving a stale cached file. The static path stays the plain
        # URL -- the HTTP static handler ignores the query string when matching.
        try:
            integration = await async_get_integration(hass, DOMAIN)
            version = str(integration.version or "0")
        except Exception:  # noqa: BLE001 -- the version is only a cache hint; fall back if unavailable
            version = "0"
        versioned_url = f"{FRONTEND_SCRIPT_URL}?v={version}"
        await hass.http.async_register_static_paths([StaticPathConfig(FRONTEND_SCRIPT_URL, card_path, False)])
        # `add_extra_js_url` injects the bundle as a deferred ES module. It is NOT awaited before
        # Lovelace renders, so on a cold load the dashboard can build its cards before the module's
        # `customElements.define(...)` runs -> "Custom element doesn't exist" error cards that only a
        # full reload clears. It still covers YAML-mode dashboards, so we keep it...
        add_extra_js_url(hass, versioned_url)
        # ...but ALSO register the bundle as a storage-mode Lovelace *resource*, which HA loads and
        # awaits BEFORE rendering dashboards -- eliminating the race. Deferred to HA-start so the
        # `lovelace` integration's resource collection is ready (and runs immediately if already
        # started, e.g. when the integration is added at runtime). Best-effort: a no-op in YAML mode.
        from homeassistant.helpers.start import async_at_started

        async def _register_resource(_hass: HomeAssistant) -> None:
            try:
                await _register_lovelace_resource(_hass, versioned_url)
            except Exception as err:  # noqa: BLE001 -- resource is an optimisation; never break startup
                _LOGGER.debug("Could not register Lovelace resource for the card (%s)", err)

        async_at_started(hass, _register_resource)
        domain_data[DATA_FRONTEND_REGISTERED] = True
        _LOGGER.debug("Registered Xplora® Watch frontend card at %s", versioned_url)
    except Exception as err:  # noqa: BLE001 -- best-effort; card is optional, must not block setup
        _LOGGER.warning("Could not register Xplora® Watch frontend card (%s)", err)


async def _register_lovelace_resource(hass: HomeAssistant, versioned_url: str) -> None:
    """Add (or version-update) the card bundle as a storage-mode Lovelace resource.

    Resources are loaded before dashboards render, so this is what actually prevents the
    "Custom element doesn't exist" flash. No-op when Lovelace runs in YAML mode (resources are
    user-managed and the collection is read-only) or when the data isn't available.
    """
    lovelace = hass.data.get("lovelace")
    # Newer HA exposes a `LovelaceData` dataclass with `.resources`; older builds used a dict.
    resources = getattr(lovelace, "resources", None)
    if resources is None and isinstance(lovelace, dict):
        resources = lovelace.get("resources")
    # Only storage-mode collections are writable (they expose `async_create_item`).
    if resources is None or not hasattr(resources, "async_create_item"):
        return

    if not getattr(resources, "loaded", True):
        await resources.async_load()

    base_url = versioned_url.split("?", 1)[0]
    for item in resources.async_items():
        if str(item.get("url", "")).split("?", 1)[0] == base_url:
            # Already present -- keep the `?v=` cache-bust current so an upgrade isn't served stale.
            if item.get("url") != versioned_url and hasattr(resources, "async_update_item"):
                await resources.async_update_item(item["id"], {"url": versioned_url})
            return

    await resources.async_create_item({"res_type": "module", "url": versioned_url})
    _LOGGER.debug("Registered Xplora® Watch card as a Lovelace resource: %s", versioned_url)


def time_str_to_minutes(value: str) -> int:
    """Convert a ``HH:MM`` (or ``HH:MM:SS``) time string to minutes since midnight.

    Inverse of ``PyXplora._helperTime``; used to turn the time entered in the CRUD services
    into the integer the GraphQL alarm/silent mutations expect.
    """
    parts = value.strip().split(":")
    if len(parts) < 2:
        raise ValueError(f"Invalid time '{value}', expected HH:MM")
    hours, minutes = int(parts[0]), int(parts[1])
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        raise ValueError(f"Time out of range '{value}'")
    return hours * 60 + minutes


def weekdays_to_week_repeat(weekdays: list[str]) -> str:
    """Convert a list of canonical weekday keys (see ``WEEKDAY_KEYS``) to the 7-char
    ``weekRepeat`` "0"/"1" string the watch uses (index 0 = Sunday .. 6 = Saturday)."""
    selected = {str(day).strip().lower() for day in weekdays}
    return "".join("1" if key in selected else "0" for key in WEEKDAY_KEYS)


def week_repeat_to_weekdays(week_repeat: str) -> list[str]:
    """Inverse of ``weekdays_to_week_repeat``: 7-char ``weekRepeat`` -> canonical weekday keys."""
    return [WEEKDAY_KEYS[i] for i, flag in enumerate(week_repeat) if i < len(WEEKDAY_KEYS) and flag == "1"]


def week_repeat_to_localized_days(week_repeat: str, language: str) -> str:
    """Render a ``weekRepeat`` string as a localized, comma-separated day list (e.g. "Mo, Tu")."""
    names = DAYS.get(language, DAYS[DEFAULT_LANGUAGE])
    return ", ".join(names[i] for i, flag in enumerate(week_repeat) if i < len(names) and flag == "1")


def get_location_distance_meter(hass: HomeAssistant, lat_lng: tuple[float, float]) -> int:
    """Get the distance in meters between two lat / lng points."""
    home_state = hass.states.get(HOME)
    if home_state is None:
        raise HomeAssistantError(f"Zone '{HOME}' not found")
    home_zone = home_state.attributes
    return int(
        distance.distance(
            (home_zone[ATTR_LATITUDE], home_zone[ATTR_LONGITUDE]),
            lat_lng,
        ).m
    )


def is_distance_in_radius(home_lat_lng: tuple[float, float], lat_lng: tuple[float, float], radius: int) -> bool:
    """Checks if distance is within radius of home."""
    if radius >= int(distance.distance(home_lat_lng, lat_lng).m):
        return True
    return False


def chat_media_cached(hass: HomeAssistant, file_name: str, file_type: str, file_dir: str) -> bool:
    """Whether a downloaded chat attachment already exists locally.

    Mirrors the `www/{file_dir}/{file_name}.{file_type}` path scheme used by
    :func:`encoded_base64_string_to_file` / :func:`encoded_base64_string_to_mp3_file`. Lets the
    read-message service skip the (rate-limited) remote download when the file is already cached.
    """
    return os.path.exists(hass.config.path(f"www/{file_dir}/{file_name}.{file_type}"))


async def encoded_base64_string_to_file(hass: HomeAssistant, base64_string: str, file_name: str, file_type: str, file_dir: str) -> None:
    """Convert a base64-encoded string to a cached file under `www/{file_dir}/`.

    The write runs in the executor so a large attachment (image/video) can't block the event loop.
    """
    target = hass.config.path(f"www/{file_dir}/{file_name}.{file_type}")
    if os.path.exists(target):
        return
    try:
        decoded_data = base64.b64decode(base64_string.encode())
    except AttributeError:
        # `base64_string` wasn't a string (e.g. None when the remote fetch failed) -> nothing to write.
        return

    def _write() -> None:
        with open(target, "wb") as f:
            f.write(decoded_data)

    await hass.async_add_executor_job(_write)


async def encoded_base64_string_to_mp3_file(hass: HomeAssistant, base64_string: str, file_name: str) -> None:
    """Convert a base64-encoded AMR voice message to an mp3 file.

    The conversion runs Home Assistant's configured ffmpeg binary in a subprocess (ffmpeg decodes
    AMR natively), off the event loop -- the watch returns voice messages as AMR, which browsers
    can't play, so we transcode to mp3 once and cache the result under `www/voice/`.
    """
    media_path = hass.config.path("www/voice")
    mp3_path = f"{media_path}/{file_name}.mp3"
    if os.path.exists(mp3_path):
        return
    amr_path = f"{media_path}/{file_name}.amr"
    decoded_data = base64.b64decode(base64_string.encode())

    def _write_amr() -> None:
        with open(amr_path, "wb") as f:
            f.write(decoded_data)

    await hass.async_add_executor_job(_write_amr)
    try:
        try:
            ffmpeg_binary = get_ffmpeg_manager(hass).binary
        except KeyError, ValueError:
            # The `ffmpeg` integration isn't set up (it's an `after_dependency`, not forced). Most
            # installs get it via `default_config`; on a minimal one the user must add `ffmpeg:` to
            # configuration.yaml. Skip gracefully rather than crash the read.
            _LOGGER.warning(
                "Home Assistant's ffmpeg integration is not set up, so voice message %s can't be "
                "converted to mp3. Enable it via `default_config:` or add `ffmpeg:` to configuration.yaml.",
                file_name,
            )
            return
        proc = await asyncio.create_subprocess_exec(
            ffmpeg_binary,
            "-y",
            "-i",
            amr_path,
            mp3_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            _LOGGER.error("ffmpeg failed converting %s (exit %s): %s", amr_path, proc.returncode, stderr.decode("utf-8", "replace"))
    finally:
        if os.path.exists(amr_path):
            await hass.async_add_executor_job(os.remove, amr_path)


async def download_image_to_file(hass: HomeAssistant, session: aiohttp.ClientSession, url: str, file_name: str) -> bool:
    """Cache `url` once into `www/image/<file_name>.jpeg`.

    Mirrors the skip-if-exists behaviour of :func:`encoded_base64_string_to_file`: once a watch's
    icon is cached, it is served locally instead of re-hitting the remote URL on every setup.
    Returns whether the file is present locally afterwards, so the caller can fall back to a
    default icon on failure.
    """
    media_path = hass.config.path("www/image")
    target = f"{media_path}/{file_name}.jpeg"
    if os.path.exists(target):
        return True

    try:
        resp = await session.get(url=url, timeout=aiohttp.ClientTimeout(total=5))
        if resp.status != 200:
            return False
        content = await resp.read()
    except aiohttp.ClientError as exc:
        _LOGGER.warning("Failed to download watch icon from %s: %s", url, exc)
        return False

    def write() -> None:
        with open(target, "wb") as f:
            f.write(content)

    await hass.async_add_executor_job(write)
    return True


async def create_www_directory(hass: HomeAssistant) -> None:
    """Create the `www` media directories and make sure they are served under `/local`.

    Home Assistant registers the `/local` static route (-> `config/www`) at startup *only* if that
    directory already exists then (frontend sets it up conditionally). These directories are created
    here, during entry setup, which runs after startup -- so on a fresh install `/local` is never
    wired up and the cached media below would 404 until the next restart. Registering the media
    sub-paths ourselves (the same self-service approach the bundled card uses) serves them
    regardless; it is idempotent across entries via `DATA_MEDIA_PATHS_REGISTERED`.
    """
    paths = [
        hass.config.path("www"),  # http://homeassistant.local:8123/local
        hass.config.path("www/image"),  # http://homeassistant.local:8123/local/image/<filename>.jpeg
        hass.config.path("www/video"),  # http://homeassistant.local:8123/local/video/<filename>.mp4
        hass.config.path("www/video/thumb"),  # http://homeassistant.local:8123/local/video/thumb/<filename>.jpeg
        hass.config.path("www/voice"),  # http://homeassistant.local:8123/local/voice/<filename>.mp3
        hass.config.path(f"www/{DOMAIN}"),  # http://homeassistant.local:8123/local/xplora_watch/*
    ]

    def mkdir() -> None:
        """Create a directory."""
        for path in paths:
            if not os.path.exists(path):
                _LOGGER.debug("Creating directory: %s", path)
                os.makedirs(path, exist_ok=True)

    await hass.async_add_executor_job(mkdir)

    # Wire up serving for the just-created media dirs. Register the specific sub-paths rather than
    # `/local` itself: when `config/www` *did* exist at startup, frontend already mapped `/local`,
    # and re-registering it would clash -- the narrower prefixes never collide and are simply
    # redundant in that case. `www/video` covers `www/video/thumb` (StaticResource serves subdirs).
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(DATA_MEDIA_PATHS_REGISTERED) or getattr(hass, "http", None) is None:
        return
    from homeassistant.components.http import StaticPathConfig

    media_paths = [
        StaticPathConfig("/local/image", hass.config.path("www/image"), False),
        StaticPathConfig("/local/video", hass.config.path("www/video"), False),
        StaticPathConfig("/local/voice", hass.config.path("www/voice"), False),
    ]
    try:
        await hass.http.async_register_static_paths(media_paths)
    except (RuntimeError, ValueError) as err:
        # Already registered (e.g. by a reload after the routes outlived the unload). Harmless.
        _LOGGER.debug("Media static paths already registered (%s)", err)
    domain_data[DATA_MEDIA_PATHS_REGISTERED] = True
