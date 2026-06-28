"""Settings registry and resolution for xplora_watch.

Typed, typo-safe access to the integration's user-configurable options (the values
stored in ``ConfigEntry.options``). This mirrors a small registry pattern: a key enum
(:class:`ConfKeys`), a per-key spec carrying a default and a coercion function
(:class:`_ConfSpec` / :data:`CONF_SPECS`), and a frozen :class:`ResolvedOptions`
dataclass produced by :func:`resolve`. The point is that every option default lives in
exactly one place instead of being duplicated at each ``entry.options.get(CONF_X,
<default>)`` call site.

Scope notes:

- **Only options-sourced, static-default settings are modeled here.** Auth/setup values
  (password, phone number, email, time zone, user language, country code) live in
  ``ConfigEntry.data``, are required, and are validated by the config flow -- wrapping
  them would force awkward "required, no default" specs, so they stay as direct reads.
- Keys whose default is dynamic or context-dependent are intentionally **not** modeled:
  ``CONF_WATCHES`` (default is ``await controller.setDevices()``) and
  ``CONF_HOME_LATITUDE/LONGITUDE/RADIUS`` (fallbacks are runtime-computed home-zone values).
  Entity visibility is per-entity via ``entity_registry_enabled_default`` instead of an
  options-flow type selection (the old ``CONF_TYPES`` key has been removed).
- ``language`` has an options -> data -> default fallback and is resolved via
  :func:`resolve_language`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Any, Callable, Generic, TypeVar

from homeassistant.const import CONF_LANGUAGE, CONF_SCAN_INTERVAL, STATE_OFF

from .const import (
    CONF_AUTO_FETCH_HISTORY,
    CONF_AUTO_MARK_READ,
    CONF_HISTORY_RETENTION_DAYS,
    CONF_HOME_SAFEZONE,
    CONF_MAPS,
    CONF_MESSAGE,
    CONF_OPENCAGE_APIKEY,
    CONF_REFRESH_ON_CARD_RENDER,
    CONF_REMOVE_MESSAGE,
    CONF_SCAN_INTERVAL_FUNCTIONS,
    DEFAULT_AUTO_FETCH_HISTORY,
    DEFAULT_HISTORY_RETENTION_DAYS,
    DEFAULT_LANGUAGE,
    DEFAULT_REFRESH_ON_CARD_RENDER,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_FUNCTIONS,
    MAPS,
    normalize_history_retention_days,
    normalize_scan_interval,
    normalize_scan_interval_functions,
)

T = TypeVar("T")

__all__ = [
    "ConfKeys",
    "CONF_SPECS",
    "ResolvedOptions",
    "resolve",
    "resolve_language",
]


class ConfKeys(StrEnum):
    """Option keys modeled by the settings registry.

    Each member's value is the literal option key string stored in
    ``ConfigEntry.options`` and is also the matching :class:`ResolvedOptions` field name.
    """

    AUTO_FETCH_HISTORY = CONF_AUTO_FETCH_HISTORY
    AUTO_MARK_READ = CONF_AUTO_MARK_READ
    HISTORY_RETENTION_DAYS = CONF_HISTORY_RETENTION_DAYS
    HOME_IS_SAFEZONE = CONF_HOME_SAFEZONE
    MAPS = CONF_MAPS
    MESSAGE = CONF_MESSAGE
    OPENCAGE_APIKEY = CONF_OPENCAGE_APIKEY
    REFRESH_ON_CARD_RENDER = CONF_REFRESH_ON_CARD_RENDER
    REMOVE_MESSAGE = CONF_REMOVE_MESSAGE
    SCAN_INTERVAL = CONF_SCAN_INTERVAL
    SCAN_INTERVAL_FUNCTIONS = CONF_SCAN_INTERVAL_FUNCTIONS


@dataclass(frozen=True, slots=True)
class _ConfSpec(Generic[T]):
    """Metadata for a single option: its default value and a coercion function."""

    default: T
    converter: Callable[[Any], T]


class _Converters:
    """Coercion helpers used by :data:`CONF_SPECS`."""

    @staticmethod
    def to_bool(v: Any) -> bool:
        """Coerce common boolean representations to ``bool``."""
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            normalized = v.lower().strip()
            if normalized in ("true", "yes", "on", "1"):
                return True
            if normalized in ("false", "no", "off", "0"):
                return False
        return bool(v)

    @staticmethod
    def to_int(v: Any) -> int:
        """Coerce to ``int``."""
        return int(v)

    @staticmethod
    def to_str(v: Any) -> str:
        """Coerce to ``str``."""
        return str(v)


# Central registry: the single source of truth for each option's default and type.
# Defaults MUST match the historical inline defaults so resolution is behavior-preserving.
CONF_SPECS: dict[ConfKeys, _ConfSpec[Any]] = {
    ConfKeys.AUTO_FETCH_HISTORY: _ConfSpec(default=DEFAULT_AUTO_FETCH_HISTORY, converter=_Converters.to_bool),
    ConfKeys.AUTO_MARK_READ: _ConfSpec(default=False, converter=_Converters.to_bool),
    # The converter clamps to the supported day range (and never raises).
    ConfKeys.HISTORY_RETENTION_DAYS: _ConfSpec(default=DEFAULT_HISTORY_RETENTION_DAYS, converter=normalize_history_retention_days),
    ConfKeys.HOME_IS_SAFEZONE: _ConfSpec(default=STATE_OFF, converter=_Converters.to_str),
    ConfKeys.MAPS: _ConfSpec(default=MAPS[0], converter=_Converters.to_str),
    ConfKeys.MESSAGE: _ConfSpec(default=10, converter=_Converters.to_int),
    ConfKeys.OPENCAGE_APIKEY: _ConfSpec(default="", converter=_Converters.to_str),
    ConfKeys.REFRESH_ON_CARD_RENDER: _ConfSpec(default=DEFAULT_REFRESH_ON_CARD_RENDER, converter=_Converters.to_bool),
    ConfKeys.REMOVE_MESSAGE: _ConfSpec(default=False, converter=_Converters.to_bool),
    # The converter snaps any stored/legacy value to a supported preset (and never raises).
    ConfKeys.SCAN_INTERVAL: _ConfSpec(default=DEFAULT_SCAN_INTERVAL, converter=normalize_scan_interval),
    ConfKeys.SCAN_INTERVAL_FUNCTIONS: _ConfSpec(default=DEFAULT_SCAN_INTERVAL_FUNCTIONS, converter=normalize_scan_interval_functions),
}


@dataclass(frozen=True, slots=True)
class ResolvedOptions:
    """Fully-resolved, typed view of the registry-modeled options.

    Field names are identical to their :class:`ConfKeys` value, so :meth:`get` can map a
    key to its value generically.
    """

    auto_fetch_history: bool
    auto_mark_read: bool
    history_retention_days: int
    home_is_safezone: str
    maps: str
    message: int
    opencage_apikey: str
    refresh_on_card_render: bool
    remove_message: bool
    scan_interval: int
    scan_interval_functions: int

    def get(self, key: ConfKeys) -> Any:
        """Return the resolved value for ``key``."""
        return getattr(self, key.value)


def resolve(options: Mapping[str, Any] | None) -> ResolvedOptions:
    """Resolve ``options`` -> defaults into a typed :class:`ResolvedOptions`.

    Each key is read from ``options`` when present (otherwise its default) and run through
    its converter. If coercion fails, the converted default is used so resolution never
    raises on malformed stored values.
    """
    options = options or {}

    def _val(key: ConfKeys) -> Any:
        spec = CONF_SPECS[key]
        raw = options[key.value] if key.value in options else spec.default
        try:
            return spec.converter(raw)
        except Exception:
            return spec.converter(spec.default)

    values = {key.value: _val(key) for key in ConfKeys}
    field_names = {f.name for f in fields(ResolvedOptions)}
    return ResolvedOptions(**{name: values[name] for name in field_names})


def resolve_language(entry: Any) -> str:
    """Resolve the UI language from an entry using options -> data -> default.

    Accepts any object exposing ``options`` and ``data`` mappings (works with config
    entries and test mocks).
    """
    options = getattr(entry, "options", None) or {}
    data = getattr(entry, "data", None) or {}
    return str(options.get(CONF_LANGUAGE, data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)))
