from __future__ import annotations

from enum import Enum


class ErrorMSG(Enum):
    """Enum class for error messages."""

    SERVER_ERR = "Cannot connect to the server."
    LOGIN_ERR = "Login to Xplora® API failed. Check your input!\n{}"
    PHONE_MAIL_ERR = "Phone Number or Email address not exist"
    AUTH_FAIL = "Authentication failed."


class Error(Exception):
    """Base class for all Exceptions."""

    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(self.message)


class HandlerException(Error):
    """Base class for all HandlerExceptions."""

    def __str__(self) -> str:
        return f"HandlerException: {self.message}"


class NoAdminError(Error):
    """Exception raised when a user is not an administrator."""

    def __init__(self) -> None:
        super().__init__()

    def __str__(self) -> str:
        return "You are not an Administrator!"


class ChildNoError(Error):
    """Exception raised when a child's phone number or watch ID is not found."""

    def __init__(self, error_message: list[str] | None = None) -> None:
        self.error_message = ["Child phonenumber", "Watch ID"] if error_message is None else error_message
        super().__init__()

    def __str__(self) -> str:
        error_message = " & ".join(self.error_message)
        return f"{error_message} not found!"


class XTypeError(Error):
    """Exception raised when a transfer value has the wrong type."""

    def __init__(self, allow: str, deny: type) -> None:
        self.allow = allow
        self.deny = deny
        super().__init__()

    def __str__(self) -> str:
        return f"Transfer value has the wrong type! The following are permitted: {self.allow}. The specified type is: {self.deny}"


class FunctionError(Error):
    """Exception raised when a function call to the Xplora API fails."""

    # FunctionError(sys._getframe().f_code.co_name)
    def __init__(self, fnc: str) -> None:
        self.fnc = fnc
        super().__init__(self.fnc)

    def __str__(self) -> str:
        return f"Xplora API call finally failed with response: {self.fnc}"


class LoginError(Error):
    """Exception raised when login to the Xplora API fails."""

    def __init__(self, error_message: str | ErrorMSG = "") -> None:
        self.error_message = error_message if isinstance(error_message, str) else error_message.value
        super().__init__()

    def __str__(self) -> str:
        return f"{self.error_message}"


class PhoneOrEmailFail(Error):
    """Exception raised when phone number or email address is not found."""

    def __init__(self, error_message: str | ErrorMSG = ErrorMSG.PHONE_MAIL_ERR) -> None:
        self.error_message = error_message if isinstance(error_message, str) else error_message.value
        super().__init__()

    def __str__(self) -> str:
        return f"{self.error_message}"


class RateLimitError(Exception):
    """Raised when the Xplora API responds with HTTP 429 (Too Many Requests).

    Deliberately does NOT subclass `Error`: every retry loop in this package catches
    `Error`, and retrying a rate-limit response would make the ban worse. Letting this
    propagate uncaught aborts the current poll cycle instead.
    """

    def __init__(self, message: str = "Xplora API rate limit exceeded (HTTP 429)."):
        self.message = message
        super().__init__(self.message)


# GraphQL error code that signals a token has expired -> triggers a refresh.
# Returned as a top-level field, not under `extensions` (ref:XW-004).
AUTH_TOKEN_EXPIRED_CODE = "E000004"


class AuthError(Exception):
    """Raised when the Xplora API responds with the `E000004` (token expired) GraphQL error.

    Deliberately does NOT subclass `Error`: every `except Error` retry loop in this package
    (including `_login`'s and the per-field fetchers') would otherwise swallow/retry a stale
    token in place instead of letting the coordinator drive the real recovery, which is a
    `RefreshToken` mutation (and only a full re-login if that fails). Mirrors `RateLimitError`.
    """

    def __init__(self, message: str = "Xplora API token expired (E000004)."):
        self.message = message
        super().__init__(self.message)


class ConnectionError(Error):  # noqa: A001 -- intentionally named to mirror the failure it wraps
    """Raised when the transport fails before any GraphQL response body exists.

    Wraps raw `aiohttp` connection/timeout failures (`ClientConnectorError`,
    `ServerDisconnectedError`, `ClientOSError`, `ServerTimeoutError`, `asyncio.TimeoutError`)
    that `_parse_response` would otherwise let escape unhandled. DOES subclass `Error`
    (unlike `RateLimitError`/`AuthError`): a network blip during login should be retried by
    `_login`'s existing `except Error` loop, and a persistent one still ends in `LoginError`/
    `UpdateFailed` -- so the outage stays visible without retrying a genuine rate-limit/auth
    response the way those two siblings must not.
    """
