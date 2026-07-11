from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from time import time
from typing import Any, Optional, cast

import aiohttp

from .const import ALL_WATCH_FUNCTIONS, MISSING_LOCATION_TM, WatchFunction
from .exception_classes import Error, ErrorMSG, LoginError
from .gql_handler_async import GQLHandler
from .model import Chats, ChatsNew, Data, SimpleChat, SmallChat, SmallChatList, User
from .pyxplora import PyXplora
from .status import (
    Emoji,
    LocationType,
    NormalStatus,
    UserContactType,
    WatchOnlineStatus,
)

_LOGGER = logging.getLogger(__name__)

LIST_DICT: list[dict[str, Any]] = []


@dataclass(frozen=True)
class FetchError:
    """Returned by data-fetch wrappers when the request itself failed.

    Lets callers tell "the request failed after retries" apart from "the server
    genuinely returned no data" (`[]`), which a bare empty list cannot express.
    """

    operation: str
    message: str = ""


def _coerce_fetch_result(result: list[dict[str, Any]] | FetchError) -> list[dict[str, Any]]:
    """Turn a `FetchError` into a safe empty list for callers that need a plain list, logging it."""
    if isinstance(result, FetchError):
        _LOGGER.warning("%s: %s", result.operation, result.message)
        return []
    return result


class TokenRefreshOutcome(Enum):
    """Result of `PyXploraApi.refresh()`, so the coordinator can gate re-login on *why* a
    refresh failed -- a full re-login must only follow a server-confirmed auth refusal, never
    a transient/unknown failure (re-authenticating during an outage/429 window worsens bans).
    """

    REFRESHED = "refreshed"  # new token obtained -> reuse it, retry the fetch
    AUTH_REFUSED = "auth_refused"  # server returned a structured error -> warrants ONE re-login
    FAILED = "failed"  # no token, no structured error (swallowed 5xx/empty) -> do NOT re-login


class PyXploraApi(PyXplora):
    inter_error: dict[str, Any] | None = None
    _refresh_token: str | None = None
    _issueToken: dict[str, Any] | None = None  # noqa: N815

    def __init__(
        self,
        countrycode: str = "",
        phoneNumber: str = "",
        password: str = "",
        userLang: str = "",
        timeZone: str = "",
        childPhoneNumber: list[str] | None = None,
        wuid: str | list | None = None,
        email: str | None = None,
        sign_up: bool = True,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._reset(countrycode, phoneNumber, password, userLang, timeZone, childPhoneNumber, wuid, email, sign_up, session)

    def _reset(
        self,
        countrycode: str = "",
        phoneNumber: str = "",
        password: str = "",
        userLang: str = "",
        timeZone: str = "",
        childPhoneNumber: list[str] | None = None,
        wuid: str | list | None = None,
        email: str | None = None,
        sign_up: bool = True,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Reinitialize the instance's connection state, e.g. after an `inter_error`."""
        self.inter_error = None
        super().__init__(countrycode, phoneNumber, password, userLang, timeZone, childPhoneNumber, wuid, email)
        self._gql_handler: GQLHandler = GQLHandler(
            self._countrycode, self._phoneNumber, self._password, self._userLang, self._timeZone, self._email, sign_up, session
        )

    async def _login(
        self, force_login: bool = False, key: str | None = None, sec: str | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not self._isConnected() or self._hasTokenExpired() or force_login:
            if force_login or self._isConnected():
                # Without this, entering this branch while still "connected" -- a *requested*
                # forced re-login, or a naturally expired token that hasn't been cleared --
                # is a silent no-op: the retry loop below is gated on `not self._isConnected()`,
                # which is already `False`. Only skip this when we're here because we were
                # genuinely disconnected to begin with (nothing to clear).
                self._logoff()
                self._gql_handler.accessToken = None

            retryCounter = 0
            while not self._isConnected() and (retryCounter < self.maxRetries + 2):
                retryCounter += 1
                self._refresh_token = ""

                # Try to login
                try:
                    self._issueToken, self._refresh_token = await self._gql_handler.login_a(key, sec)
                except LoginError as error:
                    self.error_message = error.error_message
                    await self._retry_backoff_sleep(retryCounter)
                except Error:
                    if retryCounter == self.maxRetries + 2:
                        self.error_message = ErrorMSG.SERVER_ERR
                    else:
                        await self._retry_backoff_sleep(retryCounter)

            if self._issueToken:
                self.dtIssueToken = int(time())
                self._token_expire = self._parse_token_expire(self._issueToken.get("expireDate"))
        return self._issueToken, self._refresh_token

    async def _retry_backoff_sleep(self, attempt: int) -> None:
        """Exponential backoff with jitter, capped, instead of a flat `retryDelay` sleep.

        A 429 during login bypasses this entirely (`RateLimitError` doesn't subclass `Error`,
        so it's never caught by the loop above) -- this only paces retries of genuine
        transient failures (5xx, malformed/missing-token responses), not rate limits.
        """
        delay = min(self.retryDelay * (2 ** (attempt - 1)), 30) + random.uniform(0, 1)
        await asyncio.sleep(delay)

    @staticmethod
    def _parse_token_expire(expire_date: Any) -> int | None:
        """Normalize the login response's `expireDate` into epoch seconds.

        Live capture confirmed the server already returns 10-digit (seconds) epochs, so the
        `> 1e12` branch never fires today -- kept only as a cheap forward-compat guard in
        case a future response switches to millisecond epochs.
        """
        if not expire_date:
            return None
        try:
            value = int(expire_date)
        except TypeError, ValueError:
            return None
        if value > 1e12:
            value //= 1000
        return value

    async def init(
        self, forceLogin: bool = False, signup: bool = True, key: str | None = None, sec: str | None = None, _attempt: int = 1
    ) -> None:
        token, refresh_token = await self._login(forceLogin, key, sec)
        if not signup:
            return
        if not token and not refresh_token:
            if self.error_message:
                raise LoginError(self.error_message)
            # `_login` returned no token but also set no `error_message` (e.g. `login_a`
            # yielded a falsy/partial result without raising) -- this used to recurse
            # unbounded with no delay, the one genuine server-hammer path in this client.
            # Bound it and always pace the retry.
            if _attempt >= self.maxRetries:
                raise LoginError(ErrorMSG.SERVER_ERR)
            await self._retry_backoff_sleep(_attempt)
            return await self.init(forceLogin, signup, key, sec, _attempt + 1)

        if isinstance(token, dict):
            user = token.get("user", None)
            if not user:
                raise LoginError(self.error_message)

            children = user.get("children", [])
            if not self._childPhoneNumber:
                self.watchs = children
            else:
                self.watchs = [watch for watch in children if watch["ward"]["phoneNumber"] in self._childPhoneNumber]
            self.user = user

    async def setDevices(self, ids: str | list[str] | None = None, functions: frozenset[WatchFunction] = ALL_WATCH_FUNCTIONS) -> list[str]:
        if self.inter_error is not None:
            self._reset(
                countrycode=self._countrycode,
                phoneNumber=self._phoneNumber,
                password=self._password,
                userLang=self._userLang,
                timeZone=self._timeZone,
                email=self._email,
            )
            await self.init()
        if isinstance(ids, str):
            ids = [ids]
        return await self._setDevices(ids or [], functions=functions)

    async def getDeviceList(self) -> dict[str, dict[str, Any]]:
        """Fetch the account-wide `deviceList` once and index it by every id a watch resolves by.

        Single call replacing the old per-watch battery/charging/online-status/location/
        steps/unread-chat-count fan-out (see ISSUE-12): `Watches`+`WatchState`+
        `askWatchLocate`+`WatchLastLocate`+`UserSteps`+`UnReadChatMsgCount`, once per watch,
        every poll. Returns `{}` on a malformed/empty response so `_setDevice` falls back to
        its own per-field defaults instead of raising.

        The rest of the integration keys a watch by its *ward/user* id (`ward["id"]`, from the
        login `children` payload -- what `getWatchUserIDs()`/`CONF_WATCHES` carry), but a
        `deviceList` `WatchListItem` is keyed by its own device `id` and exposes the ward id
        only under `user.id`/`user.userId`. Indexing by `id` alone meant `_setDevices` looked
        the status up by ward id, found nothing, and every watch reported `battery == -1`
        (battery + charging stuck "unknown"). Register each item under all three ids so the
        lookup resolves regardless of which one the caller holds.
        """
        data = await self._gql_handler.get_device_list_a()
        items: list[dict[str, Any]] = data.get("deviceList") or []
        indexed: dict[str, dict[str, Any]] = {}
        for item in items:
            user = item.get("user") or {}
            for key in (item.get("id"), user.get("id"), user.get("userId")):
                if key:
                    indexed[key] = item
        return indexed

    async def _setDevices(self, ids: list[str] | None = None, functions: frozenset[WatchFunction] = ALL_WATCH_FUNCTIONS) -> list[str]:
        wuids = ids if ids else self.getWatchUserIDs()
        device_list = await self.getDeviceList()
        tasks = [self._setDevice(wuid, device_list.get(wuid), functions=functions) for wuid in wuids]
        await asyncio.gather(*tasks)
        # Debug aid for the "battery/charging/location stuck unknown" class of issues: log,
        # per watch, whether the `deviceList` lookup matched and the status fields that come
        # from it. A miss (`matched=False`) means the wuid wasn't found among the indexed ids
        # -> every field below falls back to its default (battery=-1, online=UNKNOWN, ...).
        # Off unless `custom_components.xplora_watch.pyxplora_api` debug logging is enabled.
        # NOTE: this logs the watch's coordinates (lat/lng) -- treat the debug log as sensitive.
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("deviceList: %d item(s) indexed under ids %s", len(device_list), sorted(device_list))
            for wuid in wuids:
                dev = self.device.get(wuid, {})
                _LOGGER.debug(
                    "watch ...%s: matched=%s battery=%s charging=%s online=%s lat=%s lng=%s",
                    wuid[25:],
                    device_list.get(wuid) is not None,
                    dev.get("watch_battery"),
                    dev.get("watch_charging"),
                    dev.get("getWatchOnlineStatus"),
                    dev.get("lat"),
                    dev.get("lng"),
                )
        return wuids

    async def _setDevice(
        self, wuid: str, device_status: dict[str, Any] | None = None, functions: frozenset[WatchFunction] = ALL_WATCH_FUNCTIONS
    ) -> None:
        """Populate `self.device[wuid]`.

        `device_status` (a `deviceList` `WatchListItem`, see `getDeviceList`) supplies the
        status subset (battery, charging, online status, location, steps, unread chat count)
        AND the watch-model info (`swKey`/`osVersion`/`groupName`) with no extra requests --
        so imei/os-version/model are read straight from it instead of a redundant `Watches`
        call (the old `getWatches`/`getSWInfo` per-watch queries fetched data `deviceList`
        already carries, or that nothing consumed).

        Alarms/safe-zone-definitions/silent-times ("functions") are the only data that still
        need their own per-watch queries -- `deviceList` does not carry them. They rarely
        change, so each is fetched only when its member is present in `functions`; the rest are
        **carried forward** from the previously stored values (so entities/services keep showing
        last-known data instead of going empty). `functions` is gated *per data set* by the
        coordinator (its separate functions-poll interval AND per-consumer enabled state), so a
        disabled alarms sensor suppresses just the `Alarms` request, not `SafeZones`/`SlientTimes`
        too. The caller owns the decision -- including seeding a never-fetched watch -- and this
        method strictly honors `functions`, keeping the request log truthful.
        """
        status = device_status or {}

        # Watch-model info straight from the deviceList item (no extra request). Mirrors the
        # dict the now-removed `getWatches` call used to return; `get_watch_functions` reads
        # `imei`/`osVersion`/`model` from it.
        watches: dict[str, Any] = {
            "imei": status.get("swKey"),
            "osVersion": status.get("osVersion"),
            "model": status.get("groupName"),
        }

        # Fetch only the requested functions concurrently; carry the rest forward. The member
        # *value* is the `self.device[wuid]` storage key, so a skipped function falls back to its
        # last-known value (empty list if never fetched).
        prior = self.device.get(wuid, {})
        fetchers = {
            WatchFunction.ALARMS: self.getWatchAlarm,
            WatchFunction.SAFE_ZONES: self.getWatchSafeZones,
            WatchFunction.SILENT_TIMES: self.getSilentTime,
        }
        wanted = [fn for fn in (WatchFunction.ALARMS, WatchFunction.SAFE_ZONES, WatchFunction.SILENT_TIMES) if fn in functions]
        fetched: dict[WatchFunction, list[dict[str, Any]]] = {}
        if wanted:
            results = cast(
                list[list[dict[str, Any]] | FetchError],
                await asyncio.gather(*(fetchers[fn](wuid) for fn in wanted)),
            )
            fetched = {fn: _coerce_fetch_result(res) for fn, res in zip(wanted, results)}

        def _value(fn: WatchFunction) -> list[dict[str, Any]]:
            return fetched[fn] if fn in fetched else cast(list[dict[str, Any]], prior.get(fn.value, []))

        watch_alarm = _value(WatchFunction.ALARMS)
        watch_safe_zones = _value(WatchFunction.SAFE_ZONES)
        silent_time = _value(WatchFunction.SILENT_TIMES)

        location: dict[str, Any] = status.get("location") or {}
        steps_info: dict[str, Any] = status.get("stepsInfo") or {}

        tm = location.get("tm")

        self.device[wuid] = {
            "getWatchAlarm": watch_alarm,
            "watch_battery": status.get("battery", -1),
            "watch_charging": location.get("isCharging", False),
            "locateType": location.get("locateType", LocationType.UNKNOWN.value),
            # Raw fix timestamp (epoch seconds). The coordinator turns this into the shown fix time
            # (ADR 0007) and uses it to detect when a fresh `askWatchLocate` actually moved the fix
            # (see the coordinator's fresh-fix poll). No `now()` fabrication -- a missing `tm` stays
            # unknown (None) rather than reading as a just-now fix.
            "tm": tm,
            "lat": location.get("lat", None),
            "lng": location.get("lng", None),
            "rad": location.get("rad", -1),
            "poi": location.get("poi", ""),
            "city": location.get("city", ""),
            "province": location.get("province", ""),
            "country": location.get("country", ""),
            "step": location.get("step", 0),
            "distance": location.get("distance", -1),
            "isInSafeZone": location.get("isInSafeZone", False),
            "safeZoneLabel": location.get("safeZoneLabel", ""),
            "getWatchSafeZones": watch_safe_zones,
            "getSilentTime": silent_time,
            "getWatches": watches,
            # The logged-in user's guardian relationship to THIS watch, straight from the
            # deviceList item -- "FIRST" means primary guardian (admin). Lets the coordinator
            # derive is_admin without a separate per-watch `Contacts` request.
            "guardianType": status.get("guardianType"),
            "getWatchUserSteps": {"day": steps_info.get("todaysSteps", 0)},
            "getWatchOnlineStatus": status.get("onlineStatus", WatchOnlineStatus.UNKNOWN.value),
            "getWatchUserIcons": self.getWatchUserIcons(wuid),
            "getWatchUserXCoins": self.getWatchUserXCoins(wuid),
            "unreadChatMessageCount": status.get("unreadChatMessageCount", -1),
        }

    ##### Contact Info #####
    async def getWatchUserContacts(self, wuid: str) -> list[dict[str, Any]]:
        retries = 0
        contacts = []
        while retries < self.maxRetries + 2:
            retries += 1
            try:
                raw_contacts = await self._gql_handler.getWatchUserContacts_a(wuid)
                raw_contacts = raw_contacts.get("contacts", {})
                if not raw_contacts:
                    continue
                raw_contacts = raw_contacts.get("contacts", [])
                for contact in raw_contacts:
                    contactUser = contact.get("contactUser", {})
                    if contactUser:
                        xcoin = contactUser.get("xcoin", -1)
                        _id = contactUser.get("id", None)
                        contacts.append(
                            {
                                "id": _id,
                                "guardianType": contact["guardianType"],
                                "create": datetime.fromtimestamp(contact["create"]).strftime("%Y-%m-%d %H:%M:%S"),
                                "update": datetime.fromtimestamp(contact["update"]).strftime("%Y-%m-%d %H:%M:%S"),
                                "name": contact["name"],
                                "phoneNumber": f"+{contact['countryPhoneNumber']}{contact['phoneNumber']}",
                                "xcoin": xcoin,
                            }
                        )
                break
            except (Error, TypeError) as error:
                _LOGGER.debug(error)
                await self._retry_backoff_sleep(retries)
        return contacts

    async def getWatchAlarm(self, wuid: str) -> list[dict[str, Any]] | FetchError:
        retry_counter = 0
        while retry_counter < self.maxRetries + 2:
            try:
                alarms_raw = await self._gql_handler.getAlarmTime_a(wuid)
            except Error as error:
                _LOGGER.debug(error)
                alarms_raw = {}
            if not alarms_raw:
                retry_counter += 1
                if retry_counter < self.maxRetries + 2:
                    await self._retry_backoff_sleep(retry_counter)
                continue
            raw_alarms = alarms_raw.get("alarms", [])
            if not raw_alarms:
                return []
            return [
                {
                    "id": alarm["id"],
                    "vendorId": alarm["vendorId"],
                    "name": alarm["name"],
                    "start": self._helperTime(alarm["occurMin"]),
                    "weekRepeat": alarm["weekRepeat"],
                    "status": alarm["status"],
                }
                for alarm in raw_alarms
            ]
        return FetchError("Alarms", "Failed to fetch watch alarms after retries")

    async def loadWatchLocation(self, wuid: str = "", with_ask: bool = True) -> dict[str, Any]:
        retry_counter = 0
        watch_location: dict[str, Any] = {}
        while retry_counter < self.maxRetries + 1:
            try:
                if with_ask:
                    await self.askWatchLocate(wuid)
                    await asyncio.sleep(1)
                location_raw = await self._gql_handler.getWatchLastLocation_a(wuid)
                if location_raw.get("message", None):
                    # _LOGGER.error(location_raw)
                    self.inter_error = location_raw
                _watch_last_locate = location_raw.get("watchLastLocate", {})
                if not _watch_last_locate:
                    return watch_location
                _tm = MISSING_LOCATION_TM if _watch_last_locate.get("tm") is None else _watch_last_locate.get("tm")
                _lat = _watch_last_locate.get("lat", "0.0")
                _lng = _watch_last_locate.get("lng", "0.0")
                _rad = _watch_last_locate.get("rad", -1)
                _poi = _watch_last_locate.get("poi", "")
                _city = _watch_last_locate.get("city", "")
                _province = _watch_last_locate.get("province", "")
                _country = _watch_last_locate.get("country", "")
                _locate_type = (
                    LocationType.UNKNOWN.value if _watch_last_locate.get("locateType") is None else _watch_last_locate.get("locateType")
                )
                _is_in_safe_zone = _watch_last_locate.get("isInSafeZone", False)
                _safe_zone_label = _watch_last_locate.get("safeZoneLabel", "")
                _watch_battery = _watch_last_locate.get("battery", None)
                _watch_charging = _watch_last_locate.get("isCharging", False)

                watch_location = {
                    "tm": datetime.fromtimestamp(_tm).strftime("%Y-%m-%d %H:%M:%S"),
                    "lat": _lat,
                    "lng": _lng,
                    "rad": _rad,
                    "poi": _poi,
                    "city": _city,
                    "province": _province,
                    "country": _country,
                    "locateType": _locate_type,
                    "isInSafeZone": _is_in_safe_zone,
                    "safeZoneLabel": _safe_zone_label,
                    "watch_battery": _watch_battery,
                    "watch_charging": _watch_charging,
                    "watch_last_location": _watch_last_locate,
                }
                return watch_location

            except Error as error:
                _LOGGER.debug(error)
                retry_counter += 1

            await asyncio.sleep(self.retryDelay)

        return watch_location

    async def getWatchBattery(self, wuid: str) -> int:
        tasks = [self.loadWatchLocation(wuid)]
        results = await asyncio.gather(*tasks)
        if results:
            return cast(int, results[0].get("watch_battery", -1))
        return -1

    async def getWatchIsCharging(self, wuid: str) -> bool:
        tasks = [self.loadWatchLocation(wuid)]
        results = await asyncio.gather(*tasks)
        if results:
            return cast(bool, results[0].get("watch_charging", False))
        return False

    async def getWatchOnlineStatus(self, wuid: str) -> str:
        retries = 0
        status = WatchOnlineStatus.UNKNOWN

        while status is WatchOnlineStatus.UNKNOWN and retries < self.maxRetries + 2:
            try:
                ask_raw = await self.askWatchLocate(wuid)
                track_raw = await self.getTrackWatchInterval(wuid)
                status = WatchOnlineStatus.ONLINE if ask_raw or track_raw != -1 else WatchOnlineStatus.OFFLINE
            except Error as error:
                _LOGGER.debug(error)
                retries += 1
            if status is WatchOnlineStatus.UNKNOWN:
                await asyncio.sleep(self.retryDelay)

        return status.value

    async def getWatchUnReadChatMsgCount(self, wuid: str) -> int:
        try:
            unread_count = await self._gql_handler.unReadChatMsgCount_a(wuid)
            if isinstance(unread_count, dict):
                return cast(int, unread_count.get("unReadChatMsgCount", -1))
            return -1
        except Error as e:
            _LOGGER.error("Error getting unread chat message count: %s", e)
            return -1

    async def getWatchChats(
        self, wuid: str, offset: int = 0, limit: int = 0, msgId: str = "", show_del_msg: bool = True, asObject: bool = False
    ) -> list[dict[str, Any]] | SmallChatList:
        retry_counter = 0
        chats: list[dict[str, Any] | SmallChat] = []

        while not chats and retry_counter < self.maxRetries + 2:
            retry_counter += 1
            try:
                _chats_new = await self.getWatchChatsRaw(wuid, offset, limit, msgId, show_del_msg, asObject)
                if isinstance(_chats_new, dict):
                    _chats_new = ChatsNew.from_dict(_chats_new)

                _list = _chats_new.list
                if not _list:
                    continue

                for chat in _list:
                    sender = cast(User, chat.sender)
                    receiver = cast(User, chat.receiver)
                    chat_data = cast(Data, chat.data)
                    _chat = {
                        "msgId": chat.msgId,
                        "type": chat.type,
                        "sender_id": sender.id,
                        "sender_name": sender.name,
                        "receiver_id": receiver.id,
                        "receiver_name": receiver.name,
                        "data_text": chat_data.text,
                        "data_sender_name": chat_data.sender_name,
                        "create": datetime.fromtimestamp(cast(int, chat.create)).strftime("%Y-%m-%d %H:%M:%S"),
                        "delete_flag": chat_data.delete_flag,
                        "emoticon_id": chat_data.emoticon_id,
                    }
                    if asObject:
                        chats.append(SmallChat.from_dict(_chat))
                    else:
                        chats.append(_chat)
            except Error as error:
                _LOGGER.debug(error)

            if not chats:
                await asyncio.sleep(self.retryDelay)

        if asObject:
            return SmallChatList(cast(list[SmallChat], chats))
        return cast(list[dict[str, Any]], chats)

    async def getWatchChatsRaw(
        self,
        wuid: str,
        offset: int = 0,
        limit: int = 0,
        msgId: str = "",
        show_del_msg: bool = True,
        asObject: bool = False,
        with_emoji_id: bool = True,
        mark_as_read: bool = False,
    ) -> dict[str, Any] | ChatsNew:
        retry_counter = 0
        chats_new: dict = {}
        while not chats_new and retry_counter < self.maxRetries + 2:
            retry_counter += 1
            try:
                result: dict[str, Any] | Chats | ChatsNew | str | None = await self._gql_handler.chats_a(
                    wuid, offset, limit, msgId, asObject
                )

                if not result:
                    continue

                if result == "":
                    result = ChatsNew()

                if isinstance(result, str):
                    result = json.loads(result)

                if isinstance(result, dict):
                    if result.get("chatsNew", None):
                        result = ChatsNew.from_dict(result.get("chatsNew", None))
                    else:
                        result = ChatsNew()

                if isinstance(result, Chats):
                    result = result.chatsNew

                if result is None:
                    continue

                result = cast(ChatsNew, result)
                chat_list = cast(list[SimpleChat], result.list)

                # `with_emoji_id` (display: translate the emoticon id) and `mark_as_read`
                # (server-side read receipt) are independent concerns -- they used to be
                # conflated, so every poll silently marked the whole fetched window read. Only
                # send a read receipt when explicitly enabled AND the message is still unread
                # server-side (`readFlag` falsy), so we never re-mark the same history each poll.
                if with_emoji_id or mark_as_read:
                    for d in chat_list:
                        chat_data = cast(Data, d.data)
                        if with_emoji_id:
                            chat_data.emoji_id = chat_data.emoticon_id
                            chat_data.emoticon_id = Emoji[f"M{chat_data.emoticon_id}"].value
                        if mark_as_read and not d.readFlag:
                            await self.set_read_chat_msg(wuid, cast(str, d.msgId), cast(str, d.id))

                filtered_chats = [chat for chat in chat_list if show_del_msg or cast(Data, chat.data).delete_flag == 0]
                chats_new = ChatsNew(filtered_chats).to_dict()
            except Error as error:
                _LOGGER.debug(error)

            if not chats_new:
                await self._retry_backoff_sleep(retry_counter)

        # Diagnostic (shape only -- message bodies/ids are PII and are never logged here): tells an
        # empty thread (list_len 0) apart from a fetch we dropped, when the chat window shows blank.
        _LOGGER.debug(
            "chatsNew fetch ...%s offset=%s limit=%s retries=%s -> empty=%s list_len=%s",
            wuid[25:],
            offset,
            limit,
            retry_counter,
            not chats_new,
            len((chats_new or {}).get("list") or []),
        )
        return ChatsNew.from_dict(chats_new, infer_missing=True) if asObject else chats_new

    ##### Watch Location Info #####
    async def getWatchLastLocation(self, wuid: str) -> dict[str, Any]:
        tasks = [self.loadWatchLocation(wuid)]
        results = await asyncio.gather(*tasks)
        if results:
            return cast(dict[str, Any], results[0].get("watch_last_location", {}))
        return {}

    async def getWatchLocate(self, wuid: str) -> dict[str, Any]:
        tasks = [self.loadWatchLocation(wuid)]
        results = await asyncio.gather(*tasks)
        if results:
            return results[0]
        return {}

    async def getWatchLocateType(self, wuid: str) -> str:
        locate_info = await self.getWatchLocate(wuid)
        return cast(str, locate_info.get("locateType", LocationType.UNKNOWN.value))

    async def getWatchIsInSafeZone(self, wuid: str) -> bool:
        return cast(bool, (await self.getWatchLocate(wuid)).get("isInSafeZone", False))

    async def getWatchSafeZoneLabel(self, wuid: str) -> str:
        return cast(str, (await self.getWatchLocate(wuid)).get("safeZoneLabel", ""))

    async def getWatchSafeZones(self, wuid: str) -> list[dict[str, Any]] | FetchError:
        retry_counter = 0
        while retry_counter < self.maxRetries + 2:
            try:
                safe_zones_raw = await self._gql_handler.safeZones_a(wuid)
            except Error as error:
                _LOGGER.debug(error)
                safe_zones_raw = {}
            if not safe_zones_raw:
                retry_counter += 1
                if retry_counter < self.maxRetries + 2:
                    await self._retry_backoff_sleep(retry_counter)
                continue
            raw_safe_zones = safe_zones_raw.get("safeZones", [])
            if not raw_safe_zones:
                return []
            return [
                {
                    "vendorId": sz["vendorId"],
                    "groupName": sz["groupName"],
                    "name": sz["name"],
                    "lat": sz["lat"],
                    "lng": sz["lng"],
                    "rad": sz["rad"],
                    "address": sz["address"],
                }
                for sz in raw_safe_zones
            ]
        return FetchError("SafeZones", "Failed to fetch safe zones after retries")

    async def getTrackWatchInterval(self, wuid: str) -> int:
        return cast(int, (await self._gql_handler.trackWatch_a(wuid)).get("trackWatch", -1))

    async def askWatchLocate(self, wuid: str) -> bool:
        return cast(bool, (await self._gql_handler.askWatchLocate_a(wuid)).get("askWatchLocate", False))

    ##### Feature #####
    async def getSilentTime(self, wuid: str) -> list[dict[str, Any]] | FetchError:
        retry_counter = 0
        while retry_counter < self.maxRetries + 2:
            try:
                silent_times_raw = await self._gql_handler.silentTimes_a(wuid)
            except Error as error:
                _LOGGER.debug(error)
                silent_times_raw = {}
            if not silent_times_raw:
                retry_counter += 1
                if retry_counter < self.maxRetries + 2:
                    await self._retry_backoff_sleep(retry_counter)
                continue
            raw_silent_times = silent_times_raw.get("silentTimes", [])
            if not raw_silent_times:
                return []
            return [
                {
                    "id": silent_time["id"],
                    "vendorId": silent_time["vendorId"],
                    "start": self._helperTime(silent_time["start"]),
                    "end": self._helperTime(silent_time["end"]),
                    "weekRepeat": silent_time["weekRepeat"],
                    "status": silent_time["status"],
                }
                for silent_time in raw_silent_times
            ]
        return FetchError("SlientTimes", "Failed to fetch silent times after retries")

    async def setEnableSilentTime(self, silent_id: str) -> bool:
        retries = 0
        result: Any = None

        while not result and retries < self.maxRetries + 2:
            retries += 1
            try:
                # `run_command` reads the response positionally (ADR 0010) -- no response-field key here.
                result = await self._gql_handler.setEnableSilentTime_a(silent_id)
            except Error as error:
                _LOGGER.debug(error)

            if not result:
                await asyncio.sleep(self.retryDelay)

        return bool(result)

    async def setDisableSilentTime(self, silent_id: str) -> bool:
        retry_counter = 0
        result: Any = None

        while not result and retry_counter < self.maxRetries + 2:
            retry_counter += 1
            try:
                result = await self._gql_handler.setEnableSilentTime_a(silent_id, NormalStatus.DISABLE.value)
            except Error as error:
                _LOGGER.debug(error)
            if not result:
                await asyncio.sleep(self.retryDelay)

        return bool(result)

    async def setAllEnableSilentTime(self, wuid: str) -> list[bool]:
        results = []
        silent_times = _coerce_fetch_result(await self.getSilentTime(wuid))
        for silent_time in silent_times:
            id = silent_time.get("id")
            if id:
                results.append(await self.setEnableSilentTime(id))
        return results

    async def setAllDisableSilentTime(self, wuid: str) -> list[bool]:
        results = []
        for silentTime in _coerce_fetch_result(await self.getSilentTime(wuid)):
            results.append(await self.setDisableSilentTime(silentTime.get("id", "")))
        return results

    async def setAlarmTime(self, alarm_id: str, status: NormalStatus) -> bool:
        retryCounter = 0
        result: Any = None
        while not result and (retryCounter < self.maxRetries + 2):
            retryCounter += 1
            try:
                # `run_command` reads the response positionally (ADR 0010) -- no response-field key here.
                # A returned value (even a falsy refusal) is the server's authoritative answer: the watch
                # was reached and gave a verdict, so return it immediately rather than retrying an action
                # it already declined (wasted traffic / ban hygiene). The retry loop below is reserved for
                # connection/`Error` failures, which raise and fall through to the backoff.
                return bool(await self._gql_handler.setEnableAlarmTime_a(alarm_id, status.value))
            except Error as error:
                _LOGGER.debug(error)
            if not result:
                await asyncio.sleep(self.retryDelay)
        return bool(result)

    async def setEnableAlarmTime(self, alarm_id: str) -> bool:
        return await self.setAlarmTime(alarm_id, NormalStatus.ENABLE)

    async def setDisableAlarmTime(self, alarm_id: str) -> bool:
        return await self.setAlarmTime(alarm_id, NormalStatus.DISABLE)

    async def setAllEnableAlarmTime(self, wuid: str) -> list[bool]:
        res: list[bool] = []
        for alarmTime in _coerce_fetch_result(await self.getWatchAlarm(wuid)):
            res.append(await self.setEnableAlarmTime(alarmTime.get("id", "")))
        return res

    async def setAllDisableAlarmTime(self, wuid: str) -> list[bool]:
        res: list[bool] = []
        for alarmTime in _coerce_fetch_result(await self.getWatchAlarm(wuid)):
            res.append(await self.setDisableAlarmTime(alarmTime.get("id", "")))
        return res

    async def addAlarmTime(self, wuid: str, occur_min: int, week_repeat: str, name: str = "", end: int | None = None) -> bool:
        """Create a new alarm on a watch. `occur_min`/`end` are minutes since midnight; the
        alarm's `start` mirrors `occurMin` (both represent the same instant for a
        point-in-time alarm)."""
        retry_counter = 0
        result: Any = None
        while not result and retry_counter < self.maxRetries + 2:
            retry_counter += 1
            try:
                # `run_command` reads the response positionally (ADR 0010) -- no response-field key here.
                result = await self._gql_handler.addAlarmTime_a(wuid, occur_min, occur_min, week_repeat, name, end)
            except Error as error:
                _LOGGER.debug(error)
            if not result:
                await asyncio.sleep(self.retryDelay)
        return bool(result)

    async def modifyAlarmTime(
        self,
        alarm_id: str,
        occur_min: int | None = None,
        week_repeat: str | None = None,
        name: str | None = None,
        status: NormalStatus | None = None,
    ) -> bool:
        """Modify an existing alarm's time (`occur_min`), repeat days, name and/or status."""
        retry_counter = 0
        result: Any = None
        start = occur_min  # keep `start` in sync with `occurMin` for point-in-time alarms
        while not result and retry_counter < self.maxRetries + 2:
            retry_counter += 1
            try:
                result = await self._gql_handler.modifyAlarmTime_a(
                    alarm_id, occur_min, start, week_repeat, name, status.value if status else None
                )
            except Error as error:
                _LOGGER.debug(error)
            if not result:
                await asyncio.sleep(self.retryDelay)
        return bool(result)

    async def removeAlarmTime(self, alarm_id: str) -> bool:
        """Delete an alarm from a watch."""
        retry_counter = 0
        result: Any = None
        while not result and retry_counter < self.maxRetries + 2:
            retry_counter += 1
            try:
                result = await self._gql_handler.removeAlarmTime_a(alarm_id)
            except Error as error:
                _LOGGER.debug(error)
            if not result:
                await asyncio.sleep(self.retryDelay)
        return bool(result)

    async def addSilentTime(self, wuid: str, start: int, end: int, week_repeat: str, description: str = "") -> bool:
        """Create a new silent-time window. `start`/`end` are minutes since midnight."""
        retry_counter = 0
        result: Any = None
        while not result and retry_counter < self.maxRetries + 2:
            retry_counter += 1
            try:
                # `run_command` reads the response positionally (ADR 0010) -- no response-field key here.
                result = await self._gql_handler.addSilentTime_a(wuid, start, end, week_repeat, description)
            except Error as error:
                _LOGGER.debug(error)
            if not result:
                await asyncio.sleep(self.retryDelay)
        return bool(result)

    async def modifySilentTime(
        self, silent_id: str, start: int | None = None, end: int | None = None, week_repeat: str | None = None
    ) -> bool:
        """Modify an existing silent-time window's start/end (minutes since midnight) and/or repeat days."""
        retry_counter = 0
        result: Any = None
        while not result and retry_counter < self.maxRetries + 2:
            retry_counter += 1
            try:
                result = await self._gql_handler.modifySilentTime_a(silent_id, start, end, week_repeat)
            except Error as error:
                _LOGGER.debug(error)
            if not result:
                await asyncio.sleep(self.retryDelay)
        return bool(result)

    async def removeSilentTime(self, silent_id: str) -> bool:
        """Delete a silent-time window from a watch."""
        retry_counter = 0
        result: Any = None
        while not result and retry_counter < self.maxRetries + 2:
            retry_counter += 1
            try:
                result = await self._gql_handler.removeSilentTime_a(silent_id)
            except Error as error:
                _LOGGER.debug(error)
            if not result:
                await asyncio.sleep(self.retryDelay)
        return bool(result)

    async def sendText(self, text: str, wuid: str) -> bool:
        # sender is login User
        return await self._gql_handler.sendText_a(wuid, text)

    async def isAdmin(self, wuid: str) -> bool:
        """Whether the logged-in user is the watch's primary (`FIRST`) guardian.

        Not an authorization gate on its own. The control-action gate (a Contact may not
        reboot/shutdown or edit alarms/silent times) is a
        client policy enforced a layer up, in the Home Assistant service handlers, off the
        coordinator's per-watch role flag (ref:XW-009) -- not this method and not the backend.
        """
        user_id = self.getUserID()
        contacts = await self.getWatchUserContacts(wuid)
        return any(contact["id"] == user_id and contact["guardianType"] == "FIRST" for contact in contacts)

    async def shutdown(self, wuid: str) -> bool:
        """Send the watch a shutdown command. Control is gated for Contacts a layer up, in the HA service handlers (ref:XW-009)."""
        # `run_command` reads the response positionally (ADR 0010); coerce the accept/reject value to bool.
        return bool(await self._gql_handler.shutdown_a(wuid))

    async def reboot(self, wuid: str) -> bool:
        """Send the watch a reboot command. Control is gated for Contacts a layer up, in the HA service handlers (ref:XW-009)."""
        return bool(await self._gql_handler.reboot_a(wuid))

    async def getFollowRequestWatchCount(self) -> int:
        c: dict[str, Any] = await self._gql_handler.getFollowRequestWatchCount_a()
        return cast(int, c.get("followRequestWatchCount", 0))

    async def getWatches(self, wuid: str) -> dict[str, Any]:
        retryCounter = 0
        watches_raw: dict[str, Any] = {}
        watch: dict[str, Any] = {}
        while not watch and (retryCounter < self.maxRetries + 2):
            retryCounter += 1
            try:
                watches_raw = await self._gql_handler.getWatches_a(wuid)
                _watches: list[dict[str, Any]] = watches_raw.get("watches", [])
                if not _watches:
                    return watch
                watch = {
                    "imei": _watches[0]["swKey"],
                    "osVersion": _watches[0]["osVersion"],
                    "qrCode": _watches[0]["qrCode"],
                    "model": _watches[0]["groupName"],
                }
                if watch:
                    return watch
            except Error as error:
                _LOGGER.debug(error)
            if not watch:
                await self._retry_backoff_sleep(retryCounter)
        return watch

    async def getSWInfo(self, wuid: str, watches: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        watches = {} if watches is None else watches
        wqr: dict[str, Any] = watches if watches else await self.getWatches(wuid=wuid)
        qrCode: str = wqr.get("qrCode", "=")
        return await self._gql_handler.getSWInfo_a(qrCode.split("=")[1])

    async def getWatchState(self, wuid: str, watches: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        watches = {} if watches is None else watches
        wqr: dict[str, Any] = watches if watches else await self.getWatches(wuid=wuid)
        qrCode: str = wqr.get("qrCode", "=")
        return await self._gql_handler.getWatchState_a(qrCode=qrCode.split("=")[1])

    async def conv360IDToO2OID(self, qid: str, deviceId: str) -> dict[str, Any]:
        return await self._gql_handler.conv360IDToO2OID_a(qid, deviceId)

    async def campaigns(self, _id: str, categoryId: str) -> dict[str, Any]:
        return await self._gql_handler.campaigns_a(_id, categoryId)

    async def getCountries(self) -> list[dict[str, str]]:
        countries: dict[str, Any] = await self._gql_handler.countries_a()
        return cast(list[dict[str, str]], countries.get("countries", {}))

    async def getWatchLocHistory(self, wuid: str, date: int | None = None, tz: str | None = None, limit: int = 1) -> dict[str, Any]:
        return await self._gql_handler.getWatchLocHistory_a(wuid, date, tz, limit)

    async def watchesDynamic(self) -> dict[str, Any]:
        return await self._gql_handler.watchesDynamic_a()

    async def watchGroups(self, _id: str = "") -> dict[str, Any]:
        return await self._gql_handler.watchGroups_a(_id)

    async def familyInfo(self, wuid: str, watchId: str, tz: str, date: int) -> dict[str, Any]:
        return await self._gql_handler.familyInfo_a(wuid, watchId, tz, date)

    async def avatars(self, _id: str) -> dict[str, Any]:
        return await self._gql_handler.avatars_a(_id)

    async def getWatchUserSteps(self, wuid: str, date: int) -> dict[str, Any]:
        userSteps = await self._gql_handler.getWatchUserSteps_a(wuid=wuid, tz=self._timeZone, date=date)
        if not userSteps:
            return {}
        userSteps = cast(dict[str, Any], userSteps.get("userSteps", {}))
        if not userSteps:
            return {}
        return userSteps

    # start tracking for 30min
    async def getStartTrackingWatch(self, wuid: str) -> int:
        data: dict[str, Any] = await self._gql_handler.getStartTrackingWatch_a(wuid)
        return cast(int, data.get("startTrackingWatch", -1))

    # stop tracking from getStartTrackingWatch
    async def getEndTrackingWatch(self, wuid: str) -> int:
        data: dict[str, Any] = await self._gql_handler.getEndTrackingWatch_a(wuid)
        return cast(int, data.get("endTrackingWatch", -1))

    async def addStep(self, step: int) -> bool:
        s: dict[str, bool] = await self._gql_handler.addStep_a(step)
        return s.get("addStep", False)

    async def submitIncorrectLocationData(self, wuid: str, lat: str, lng: str, timestamp: str) -> bool:
        data: dict[str, bool] = await self._gql_handler.submitIncorrectLocationData_a(wuid, lat, lng, timestamp)
        return data.get("submitIncorrectLocationData", False)

    async def getAppVersion(self) -> dict[str, Any]:
        data = await self._gql_handler.getAppVersion_a()
        return data

    async def checkEmailOrPhoneExist(self, type: UserContactType, email: str = "", countryCode: str = "", phoneNumber: str = "") -> bool:
        data = await self._gql_handler.checkEmailOrPhoneExist_a(type, email, countryCode, phoneNumber)
        return data.get("checkEmailOrPhoneExist", False)

    async def modifyContact(self, contactId: str, isAdmin: bool, contactName: str = "", fileId: str = "") -> dict[str, Any]:
        data = await self._gql_handler.modifyContact_a(contactId, isAdmin, contactName, fileId)
        return data

    async def deleteMessageFromApp(self, wuid: str, msgId: str) -> bool:
        data = await self._gql_handler.deleteMessageFromApp_a(wuid, msgId)
        if data.get("deleteMsg", False):
            return True
        return False

    async def get_chat_voice(self, wuid: str, msgId: str) -> str | None:
        data = await self._gql_handler.fetchChatVoice_a(wuid, msgId)
        if data.get("fetchChatVoice"):
            return cast(str, data.get("fetchChatVoice"))
        return None

    async def get_chat_image(self, wuid: str, msgId: str) -> str | None:
        data = await self._gql_handler.fetchChatImage_a(wuid, msgId)
        if data.get("fetchChatImage"):
            return cast(str, data.get("fetchChatImage"))
        return None

    async def get_short_video(self, wuid: str, msgId: str) -> str | None:
        data = await self._gql_handler.fetchChatShortVideo_a(wuid, msgId)
        if data.get("fetchChatShortVideo"):
            return cast(str, data.get("fetchChatShortVideo"))
        return None

    async def get_short_video_cover(self, wuid: str, msgId: str) -> str | None:
        data = await self._gql_handler.fetchChatShortVideoCover_a(wuid, msgId)
        if data.get("fetchChatShortVideoCover"):
            return cast(str, data.get("fetchChatShortVideoCover"))
        return None

    async def set_read_chat_msg(self, wuid: str, msgId: str = "", _id: str = "") -> dict[str, Any]:
        data = await self._gql_handler.setReadChatMsg_a(wuid, msgId, _id)
        return data

    async def refresh(self) -> TokenRefreshOutcome:
        """Refresh the session token via `RefreshToken(uid, refreshToken)`.

        On-demand recovery from an `AuthError` (`E000004`), not a fixed-schedule refresh.
        `uid` is the user id (`self.getUserID()`), not a watch id --
        the mutation previously (and wrongly) took a `wuid`.

        Returns a `TokenRefreshOutcome` so the coordinator can decide whether a full re-login
        is warranted:
          - `REFRESHED`    -> a new token was obtained and stored.
          - `AUTH_REFUSED` -> the server responded with a structured `errors` body (for the
            `RefreshToken` op the only realistic structured error is a rejected/expired
            refresh token) -> a single full re-login is warranted.
          - `FAILED`       -> no token and no structured error (empty body from a swallowed
            5xx / transport hiccup) -> transient/unknown, do NOT re-login.

        Transient *exceptions* (`RateLimitError`, `ConnectionError`/`Error`) raised by
        `refresh_token_a` are deliberately NOT caught here -- they propagate to the
        coordinator, which also treats them as "do not re-login".
        """
        response = await self._gql_handler.refresh_token_a(self.getUserID(), cast(str, self._refresh_token))
        new_token_info: dict[str, Any] | None = (response.get("data") or {}).get("refreshToken", None)
        new_token = new_token_info.get("token") if new_token_info else None
        new_refresh_token = new_token_info.get("refreshToken") if new_token_info else None
        if not new_token or not new_refresh_token:
            # No usable token. A structured `errors` body means the server actively refused
            # the refresh token (auth failure -> re-login); an empty response means we never
            # got a real answer (transient -> no re-login).
            if response.get("errors"):
                return TokenRefreshOutcome.AUTH_REFUSED
            return TokenRefreshOutcome.FAILED

        if self._issueToken is None:
            self._issueToken = {}
        self._issueToken["token"] = new_token
        self._issueToken["refreshToken"] = new_refresh_token
        self._issueToken["issueDate"] = new_token_info.get("issueDate") if new_token_info else None
        self._issueToken["expireDate"] = new_token_info.get("expireDate") if new_token_info else None

        self._refresh_token = new_refresh_token
        self._gql_handler.issueToken = self._issueToken
        self._gql_handler.accessToken = new_token
        self.dtIssueToken = int(time())
        self._token_expire = self._parse_token_expire(self._issueToken["expireDate"])
        return TokenRefreshOutcome.REFRESHED

    def dump_session(self) -> dict[str, Any]:
        """Serialize the current session so a later process can resume it without logging in.

        The Xplora token is valid for ~35 days but is held only in memory, so a Home Assistant
        restart otherwise spends a fresh `signInWithEmailOrPhone` every time. The `signIn` blob
        (`_issueToken`) is the whole restorable session: it carries the access/refresh tokens, the
        expiry, AND `user.children` (the watch list `init()` derives `self.watchs` from) -- so
        persisting it lets a restart skip the login entirely (see `restore_session`).

        Returns `{}` when there is no live session to persist (so callers can skip writing).
        """
        if not self._issueToken or not self._gql_handler.accessToken:
            return {}
        return {"issue_token": self._issueToken, "dt_issue_token": self.dtIssueToken}

    def restore_session(self, blob: dict[str, Any]) -> bool:
        """Restore a session previously produced by `dump_session`, mirroring `login_a`'s state.

        Sets exactly the fields a successful login would, all derived from the stored `signIn`
        blob (so there is no chance of the parts drifting out of sync). After this, a subsequent
        `init(forceLogin=False)` finds `_isConnected()` true and -- if `_hasTokenExpired()` is
        false -- performs NO network call, then populates `self.watchs`/`self.user` from the
        restored token's `user.children`.

        Returns `True` if a usable session was restored, `False` for a missing/corrupt/partial
        blob (the caller then falls through to a normal login -- never crashes on bad stored data).
        """
        if not isinstance(blob, dict):
            return False
        issue = blob.get("issue_token")
        if not isinstance(issue, dict) or not issue.get("token") or not issue.get("user"):
            return False
        self._issueToken = issue
        self._refresh_token = issue.get("refreshToken")
        self._token_expire = self._parse_token_expire(issue.get("expireDate"))
        self.dtIssueToken = int(blob.get("dt_issue_token") or 0)
        self._gql_handler.issueToken = issue
        self._gql_handler.refreshToken = self._refresh_token
        self._gql_handler.sessionId = issue.get("id")
        self._gql_handler.userId = (issue.get("user") or {}).get("id")
        self._gql_handler.accessToken = issue.get("token")
        return True

    async def logout(self) -> bool:
        """Log out: invalidate the current token server-side, then clear local session state.

        Server-side logout (`ExpireToken` mutation) plus local clear. The server call is
        best-effort: whether or not it succeeds, the local token is dropped (`_logoff()` plus
        clearing the handler's `accessToken`) so `_isConnected()` goes `False` and the next
        `init()`/poll performs a clean re-login.

        Returns whether the server acknowledged the expiry. Transient exceptions
        (`RateLimitError`, `ConnectionError`/`Error`) from `expireToken_a` are NOT caught here
        -- they propagate so the caller can decide (a manual service logs them; the removal
        hook swallows them). Even when they propagate, the local state has already been
        cleared in the `finally` block, so we never leave a half-live session behind.
        """
        acknowledged = False
        try:
            response = await self._gql_handler.expireToken_a()
            acknowledged = bool((response.get("data") or {}).get("expireToken"))
            return acknowledged
        finally:
            self._logoff()
            if self._gql_handler is not None:
                self._gql_handler.accessToken = None
