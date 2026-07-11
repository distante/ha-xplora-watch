from __future__ import annotations

import sys
from typing import Any

from .exception_classes import HandlerException

if sys.version_info >= (3, 11):
    from datetime import UTC, datetime
else:
    from datetime import datetime, timezone
import hashlib
import math
from time import time

from .const import API_KEY, API_SECRET, DEFAULT_ACCEPT_LANGUAGE
from .status import ClientType


def _normalize_accept_language(locale: str | None) -> str:
    """Coerce the configured user locale into a well-formed `Accept-Language` value.

    The config flow stores a region-qualified BCP-47 tag (`en-GB`, `de-DE`, ...), sent
    verbatim. This only guards the odd legacy/edge value: an empty/missing locale falls
    back to `DEFAULT_ACCEPT_LANGUAGE`, and a Python-locale-style underscore form (`en_GB`)
    is normalized to a hyphen so the header stays well-formed.
    """
    if not locale:
        return DEFAULT_ACCEPT_LANGUAGE
    return locale.replace("_", "-")


class HandlerGQL:
    """A class to handle GraphQL API requests for PyXplora.

    Attributes:
        accessToken (Any): The access token used for authentication.
        sessionId (None): The session ID.
        userId (None): The user ID.
        _API_KEY (str): The API key.
        _API_SECRET (str): The API secret.
        issueToken (dict[str, Any]): The issue token.
        errors (list[Any]): A list of errors.
    """

    accessToken: Any = None  # noqa: N815
    sessionId = None  # noqa: N815
    userId = None  # noqa: N815
    issueToken: dict[str, Any] | None = None  # noqa: N815
    errors: list[Any]

    def __init__(
        self,
        countryPhoneNumber: str,
        phoneNumber: str,
        password: str,
        userLang: str,
        timeZone: str,
        email: str | None = None,
        signup: bool = True,
    ) -> None:
        """Initializes the class with the given parameters.

        Args:
            countryPhoneNumber (str): The country phone number.
            phoneNumber (str): The phone number.
            password (str): The password.
            userLang (str): The user language.
            timeZone (str): The time zone.
            email (str, optional): The email address. Defaults to None.
            signup (bool, optional): Indicates if the user is signing up. Defaults to True.
        """
        # init vars
        self.accessToken = None
        self.sessionId = None
        self.userId = None
        self.issueToken = None
        self.errors = []
        self.userLocale = userLang
        self.timeZone = timeZone
        self.countryPhoneNumber = countryPhoneNumber
        self.phoneNumber = phoneNumber
        self.email = email
        self.passwordMD5 = hashlib.md5(password.encode()).hexdigest()
        self._API_KEY = API_KEY
        self._API_SECRET = API_SECRET
        self.variables = {
            "countryPhoneNumber": self.countryPhoneNumber,
            "phoneNumber": self.phoneNumber,
            "password": self.passwordMD5,
            "userLang": self.userLocale,
            "timeZone": self.timeZone,
            "emailAddress": self.email,
            "client": ClientType.APP.value,
            # `signInWithEmailOrPhone` declares a `$clientId: String`; sent (null here -- this
            # client registers no push/device id) so the login request carries the same variable
            # set as the reference client (ref:XW-014).
            "clientId": None,
        }
        self.signup = signup

    def getApiKey(self) -> str:
        """Returns the API key.

        Returns:
            str: The API key.
        """
        return self._API_KEY

    def getSecret(self) -> str:
        """Returns the API secret.

        Returns:
            str: The API secret.
        """
        return self._API_SECRET

    def getRequestHeaders(self, acceptedContentType: str) -> dict[str, Any]:
        """Returns the request headers with the specified content type.

        Args:
            acceptedContentType (str): The accepted content type.

        Returns:
            dict[str, Any]: The request headers.

        Raises:
            Exception: If `acceptedContentType` is empty or if `API_KEY` or `API_SECRET` is not set.
        """
        if acceptedContentType == "" or acceptedContentType is None:
            raise HandlerException("acceptedContentType MUST NOT be empty!")
        if self._API_KEY is None:
            raise HandlerException("Xplorao2o API_KEY MUST NOT be empty!")
        if self._API_SECRET is None:
            raise HandlerException("Xplorao2o API_SECRET MUST NOT be empty!")

        authorizationHeader = ""

        if (self.accessToken is None or not self.issueToken) and self._API_KEY == API_KEY:
            # OPEN authorization
            authorizationHeader = f"Open {self._API_KEY}:{self._API_SECRET}"
        # else:
        # BEARER authorization
        elif self.issueToken:
            # Requests are always signed with `Bearer <token>:<static M2>` (ref:XW-005);
            # `w360`, if present, is a separate downstream (goplay/360) service credential,
            # not the API bearer secret. Signing with it (and overwriting `_API_KEY`/
            # `_API_SECRET` with it, as this used to do) would corrupt the static M1/M2 for
            # the rest of the session.
            authorizationHeader = f"Bearer {self.accessToken}:{self._API_SECRET}"
        else:
            authorizationHeader = f"Bearer {self._API_KEY}:{self._API_SECRET}"
        if sys.version_info >= (3, 11):
            utc = UTC
        else:
            utc = timezone.utc
        requestHeaders = {
            "H-Date": datetime.now(utc).strftime("%a, %d %b %Y %H:%M:%S") + " GMT",
            "H-Tid": str(math.floor(time())),
            "Content-Type": acceptedContentType,
            "H-BackDoor-Authorization": authorizationHeader,
            "Accept-Language": _normalize_accept_language(self.userLocale),
        }
        return requestHeaders
