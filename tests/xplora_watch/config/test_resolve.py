"""Tests for ``config.resolve`` / ``config.resolve_language``.

These guard the behavior-preserving contract of the typed settings registry: empty options
must yield exactly the historical inline defaults, provided values must be coerced to their
declared types, malformed values must fall back to the default (never raise), and the
language fallback must follow options -> data -> default.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.const import CONF_LANGUAGE, STATE_OFF, STATE_ON

from custom_components.xplora_watch.config import (
    CONF_SPECS,
    ConfKeys,
    ResolvedOptions,
    resolve,
    resolve_account_alias,
    resolve_language,
)
from custom_components.xplora_watch.const import (
    CONF_ACCOUNT_ALIAS,
    DEFAULT_HISTORY_RETENTION_DAYS,
    DEFAULT_LANGUAGE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_FUNCTIONS,
    MAPS,
)


def test_resolve_empty_returns_defaults() -> None:
    """Empty options resolve to the registry defaults (the historical inline defaults)."""
    resolved = resolve({})
    assert resolved == ResolvedOptions(
        auto_fetch_history=False,
        auto_mark_read=False,
        history_retention_days=DEFAULT_HISTORY_RETENTION_DAYS,
        home_is_safezone=STATE_OFF,
        maps=MAPS[0],
        message=10,
        opencage_apikey="",
        refresh_on_card_render=False,
        remove_message=False,
        scan_interval=DEFAULT_SCAN_INTERVAL,
        scan_interval_functions=DEFAULT_SCAN_INTERVAL_FUNCTIONS,
    )


def test_resolve_none_returns_defaults() -> None:
    """``None`` options behave like empty options."""
    assert resolve(None) == resolve({})


def test_resolve_honors_and_coerces_provided_values() -> None:
    """Provided values are coerced to their declared types."""
    resolved = resolve(
        {
            "message": "7",  # str -> int
            "auto_mark_read": "true",  # str -> bool
            "remove_message": 1,  # int -> bool
            "maps": MAPS[1],
            "opencage_apikey": "key",
            "home_is_safezone": STATE_ON,
        }
    )
    assert resolved.message == 7
    assert resolved.auto_mark_read is True
    assert resolved.remove_message is True
    assert resolved.maps == MAPS[1]
    assert resolved.opencage_apikey == "key"
    assert resolved.home_is_safezone == STATE_ON


def test_resolve_malformed_value_falls_back_to_default() -> None:
    """A value that cannot be coerced falls back to the spec default instead of raising."""
    assert resolve({"message": "not-a-number"}).message == 10


def test_resolve_snaps_scan_interval_to_preset() -> None:
    """The scan-interval converter snaps any raw value to a supported preset."""
    # 50 minutes is closest to the 60-minute preset.
    assert resolve({"scan_interval": 50 * 60}).scan_interval == 60 * 60
    # A non-positive / off value stays off.
    assert resolve({"scan_interval": 0}).scan_interval == DEFAULT_SCAN_INTERVAL


def test_resolve_history_retention_clamps_and_defaults() -> None:
    """The retention converter clamps to the supported day range and falls back on garbage."""
    from custom_components.xplora_watch.const import (
        DEFAULT_HISTORY_RETENTION_DAYS,
        HISTORY_RETENTION_DAYS_MAX,
        HISTORY_RETENTION_DAYS_MIN,
    )

    assert resolve({}).history_retention_days == DEFAULT_HISTORY_RETENTION_DAYS
    assert resolve({"history_retention_days": 14}).history_retention_days == 14
    assert resolve({"history_retention_days": 0}).history_retention_days == HISTORY_RETENTION_DAYS_MIN
    assert resolve({"history_retention_days": 99999}).history_retention_days == HISTORY_RETENTION_DAYS_MAX
    assert resolve({"history_retention_days": "nope"}).history_retention_days == DEFAULT_HISTORY_RETENTION_DAYS


def test_get_accessor_matches_fields() -> None:
    """``ResolvedOptions.get(key)`` returns the value for every registry key."""
    resolved = resolve({"message": 3})
    assert resolved.get(ConfKeys.MESSAGE) == 3
    for key in ConfKeys:
        assert resolved.get(key) == getattr(resolved, key.value)


def test_every_key_has_a_spec_and_field() -> None:
    """Each ConfKeys member is in CONF_SPECS and maps to a ResolvedOptions field."""
    field_names = set(ResolvedOptions.__dataclass_fields__)
    for key in ConfKeys:
        assert key in CONF_SPECS
        assert key.value in field_names


@pytest.mark.parametrize(
    ("options", "data", "expected"),
    [
        ({CONF_LANGUAGE: "fr"}, {CONF_LANGUAGE: "de"}, "fr"),  # options win
        ({}, {CONF_LANGUAGE: "de"}, "de"),  # fall back to data
        ({}, {}, DEFAULT_LANGUAGE),  # fall back to default
    ],
)
def test_resolve_language_fallback_chain(options: dict, data: dict, expected: str) -> None:
    """Language resolves options -> data -> default."""
    entry = SimpleNamespace(options=options, data=data)
    assert resolve_language(entry) == expected


@pytest.mark.parametrize(
    ("options", "data", "expected"),
    [
        ({CONF_ACCOUNT_ALIAS: "Mom"}, {CONF_ACCOUNT_ALIAS: "Dad"}, "Mom"),  # options (edited) win over data (setup)
        ({}, {CONF_ACCOUNT_ALIAS: "Dad"}, "Dad"),  # fall back to the alias captured at setup
        ({}, {}, ""),  # unset -> empty string (account_token then falls back to display name / id)
    ],
)
def test_resolve_account_alias_fallback_chain(options: dict, data: dict, expected: str) -> None:
    """The account alias resolves options -> data -> "" (empty when never set)."""
    entry = SimpleNamespace(options=options, data=data)
    assert resolve_account_alias(entry) == expected
