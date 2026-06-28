"""Module containing graphQL client."""

from __future__ import annotations

import asyncio
import logging
from http import HTTPStatus
from typing import Any, Optional, cast

import aiohttp

from .const import DEFAULT_TIMEOUT, DEFAULT_USER_AGENT
from .exception_classes import ConnectionError as XploraConnectionError
from .exception_classes import RateLimitError

# Raw transport failures with no GraphQL response body at all -- `ClientConnectionError`
# covers `ClientConnectorError`/`ClientOSError`/`ServerDisconnectedError`/
# `ServerTimeoutError` (all subclasses); `asyncio.TimeoutError` is the builtin `TimeoutError`
# on py3.11+ and is listed for clarity/forward-compat.
_CONNECTION_ERRORS = (aiohttp.ClientConnectionError, asyncio.TimeoutError)


class GraphqlClient:
    """Class which represents the interface to make graphQL requests through."""

    def __init__(self, endpoint: str, headers: Optional[dict[str, str]] = None, **kwargs: Any):
        """Instantiate the client."""
        headers = {} if headers is None else headers
        self.logger = logging.getLogger(__name__)
        self.endpoint = endpoint
        self.headers = headers
        self.options = kwargs

    @staticmethod
    def __request_body(query: str, variables: dict[str, Any] | None = None, operation_name: str | None = None) -> dict[str, Any]:
        json: dict[str, Any] = {"query": query}

        if variables:
            json.update({"variables": variables})

        if operation_name:
            json.update({"operationName": operation_name})

        return json

    async def _parse_response(self, response: aiohttp.ClientResponse) -> dict[str, Any]:
        """Raise for HTTP errors and return the parsed JSON body on success.

        Returns `{}` for a non-JSON success body or any non-429 HTTP error, which the
        callers' retry loops treat as "request failed, maybe retry". Raises
        `RateLimitError` on a 429 instead, so it bypasses those retry loops (see
        `RateLimitError`'s docstring for why retrying a rate-limit response is wrong here).
        """
        try:
            response.raise_for_status()
            return cast(dict[str, Any], await response.json())
        except aiohttp.ContentTypeError as err:
            self.logger.debug(err)
            return {}
        except aiohttp.ClientResponseError as err:
            if err.status == HTTPStatus.TOO_MANY_REQUESTS:
                raise RateLimitError() from err
            self.logger.debug(err)
            return {}

    async def execute_async(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Make asynchronous request to graphQL server."""
        headers = {} if headers is None else headers
        request_body = self.__request_body(query=query, variables=variables, operation_name=operation_name)

        if "user-agent" not in headers:
            headers["user-agent"] = DEFAULT_USER_AGENT
        # One line per outgoing operation: lets `custom_components.xplora_watch: debug` corroborate
        # exactly which GraphQL calls a poll makes (e.g. one `deviceList`, no `Watches`, and
        # `Alarms`/`SafeZones`/`SlientTimes` only when the functions fetch is due).
        self.logger.debug("Xplora GraphQL request -> %s", operation_name or "<unnamed>")
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(DEFAULT_TIMEOUT)) as session,
                session.post(self.endpoint, json=request_body, headers={**self.headers, **headers}) as response,
            ):
                return await self._parse_response(response)
        except _CONNECTION_ERRORS as err:
            raise XploraConnectionError(f"Xplora API connection error: {err}") from err

    async def ha_execute_async(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
        headers: Optional[dict[str, str]] = None,
        session: aiohttp.ClientSession | None = None,
    ) -> dict[str, Any]:
        """Make asynchronous request to graphQL server."""
        headers = {} if headers is None else headers
        request_body = self.__request_body(query=query, variables=variables, operation_name=operation_name)

        if "user-agent" not in headers:
            headers["user-agent"] = DEFAULT_USER_AGENT
        if session is None:
            # Delegates to `execute_async`, which emits the per-operation debug line below.
            return await self.execute_async(query=query, variables=variables, operation_name=operation_name, headers=headers)
        # Per-operation trace (see `execute_async`): the single point every HA-session request
        # passes through, so a debug log here covers the whole per-poll fan-out exactly once.
        self.logger.debug("Xplora GraphQL request -> %s", operation_name or "<unnamed>")
        try:
            async with session.post(self.endpoint, json=request_body, headers={**self.headers, **headers}) as response:
                return await self._parse_response(response)
        except _CONNECTION_ERRORS as err:
            raise XploraConnectionError(f"Xplora API connection error: {err}") from err
