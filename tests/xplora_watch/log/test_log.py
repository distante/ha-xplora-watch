"""Tests for ``log.Log`` -- the per-config-entry child logger wrapper."""

from __future__ import annotations

import logging

from custom_components.xplora_watch.log import _BASE_LOGGER_NAME, INSTANCE_ID_LENGTH, Log


def test_base_logger_name_is_the_package() -> None:
    """Without an entry id, the logger is the integration's base logger."""
    log = Log()
    assert log.name == _BASE_LOGGER_NAME == "custom_components.xplora_watch"
    assert isinstance(log.underlying_logger, logging.Logger)


def test_entry_id_creates_child_logger_suffixed_with_short_id() -> None:
    """With an entry id, the logger is a child named by the last few id characters."""
    entry_id = "1a2b3c4d-5e6f-7890-abcd-ef1234567890"
    log = Log(entry_id=entry_id)
    short_id = entry_id[-INSTANCE_ID_LENGTH:]
    assert log.name == f"{_BASE_LOGGER_NAME}.{short_id}"
    assert log.name.endswith("67890")


def test_set_level_is_instance_scoped() -> None:
    """``setLevel`` only affects this entry's own child logger, not other instances.

    Restores the mutated child logger afterward so this test can't leak a level into the
    shared logger registry and affect other tests (xplora's conftest has no log reset).
    """
    child_a = Log(entry_id="entry-AAAAA")
    child_b = Log(entry_id="entry-BBBBB")
    original = child_a.underlying_logger.level
    try:
        child_a.setLevel(logging.DEBUG)
        assert child_a.underlying_logger.level == logging.DEBUG
        assert child_a.isEnabledFor(logging.DEBUG) is True
        # A different entry's logger is untouched (its own level stays at the default NOTSET).
        assert child_b.underlying_logger.level != logging.DEBUG
    finally:
        child_a.underlying_logger.setLevel(original)


def test_log_methods_delegate_to_underlying_logger(caplog) -> None:
    """The wrapper forwards messages (with %-args) to the underlying logger."""
    log = Log(entry_id="entry-BBBBB")
    with caplog.at_level(logging.DEBUG, logger=log.name):
        log.debug("hello %s", "world")
    assert "hello world" in caplog.text
