"""Per-instance logger for xplora_watch.

A thin ``logging.Logger``-compatible wrapper that, when given a config ``entry_id``,
logs through a *child* logger named after the last few characters of that id. This lets
a single config entry be flipped to verbose (DEBUG) at runtime -- via
``log.setLevel(logging.DEBUG)`` -- without raising the log level for every other entry.

Usage:
    # Module/early/static context (no entry yet):
    from .log import Log
    log = Log()
    log.info("Setting up")
    # -> logs under "custom_components.xplora_watch"

    # Per-config-entry context (coordinator, entity, service handler):
    log = Log(entry_id="1a2b3c4d-...-ef1234567890")
    log.debug("Polling watch")
    # -> logs under "custom_components.xplora_watch.67890"
"""

from __future__ import annotations

import logging
from typing import Any, Final

#: Length of the short instance-id suffix appended to the base logger name.
INSTANCE_ID_LENGTH: Final[int] = 5

#: Base logger name for the integration ("custom_components.xplora_watch" as a subpackage).
_BASE_LOGGER_NAME: Final[str] = __package__ or __name__.rsplit(".", 1)[0]


class Log:
    """``logging.Logger``-compatible wrapper supporting per-entry child loggers.

    Implements the commonly used subset of the ``logging.Logger`` interface so it can be
    used as a drop-in replacement in most contexts.
    """

    def __init__(self, entry_id: str | None = None) -> None:
        """Initialize the logger.

        Args:
            entry_id: Optional config entry id. When provided, a child logger named with the
                last ``INSTANCE_ID_LENGTH`` characters of the id is used, so log-level changes
                affect only this instance.
        """
        if entry_id:
            short_id = entry_id[-INSTANCE_ID_LENGTH:]
            self._logger = logging.getLogger(f"{_BASE_LOGGER_NAME}.{short_id}")
        else:
            self._logger = logging.getLogger(_BASE_LOGGER_NAME)

    @property
    def name(self) -> str:
        """Return the underlying logger's name."""
        return self._logger.name

    @property
    def underlying_logger(self) -> logging.Logger:
        """Return the wrapped ``logging.Logger``.

        Useful when an API requires a real ``logging.Logger`` (e.g. Home Assistant's
        ``DataUpdateCoordinator``).
        """
        return self._logger

    def debug(self, msg: object, *args: object, **kwargs: Any) -> None:
        """Log a debug message."""
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: object, *args: object, **kwargs: Any) -> None:
        """Log an info message."""
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: object, *args: object, **kwargs: Any) -> None:
        """Log a warning message."""
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg: object, *args: object, **kwargs: Any) -> None:
        """Log an error message."""
        self._logger.error(msg, *args, **kwargs)

    def exception(self, msg: object, *args: object, **kwargs: Any) -> None:
        """Log an exception message (call only from an exception handler)."""
        self._logger.exception(msg, *args, **kwargs)

    def setLevel(self, level: int | str) -> None:
        """Set the log level for this instance's (child) logger only."""
        self._logger.setLevel(level)

    def isEnabledFor(self, level: int) -> bool:
        """Return whether the logger would process a message at ``level``."""
        return self._logger.isEnabledFor(level)
