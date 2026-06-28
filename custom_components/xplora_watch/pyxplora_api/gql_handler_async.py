"""GQL Handler."""

from __future__ import annotations

import logging
from typing import Any, TypedDict, cast

import aiohttp

from . import gql_mutations as gm
from . import gql_queries as gq
from .const import ENDPOINT
from .exception_classes import AUTH_TOKEN_EXPIRED_CODE, AuthError, HandlerException, LoginError
from .graphql_client import GraphqlClient
from .handler_gql import HandlerGQL
from .model import Chats, ChatsNew
from .status import EmailAndPhoneVerificationTypeV2, NormalStatus, UserContactType

_LOGGER = logging.getLogger(__name__)

#: Dedicated logger for *raw, unparsed* server responses. Kept on its own child logger so it
#: can be turned on in isolation -- the payloads are huge and contain personal data (names,
#: phone numbers, coordinates, message bodies), so it stays silent unless explicitly enabled:
#:   logger:
#:     logs:
#:       custom_components.xplora_watch.pyxplora_api.raw: debug
#: There is no standard log level below DEBUG that Home Assistant can enable by name, so this
#: separate logger (rather than a custom sub-DEBUG level) is how the raw dumps are gated.
_RAW_LOGGER = logging.getLogger(f"{__name__.rsplit('.', 1)[0]}.raw")


class GqlResponse(TypedDict, total=False):
    """The standard GraphQL response envelope every `runGqlQuery_a` call returns.

    Typing this as a TypedDict (rather than `dict[str, Any]`) lets mypy infer a concrete
    type from `.get("data", {})`/`.get("errors", [])` instead of `Any`, without needing a
    `cast()` at every one of this file's call sites.
    """

    data: dict[str, Any]
    errors: list[dict[str, Any]]


class GQLHandler(HandlerGQL):
    def __init__(
        self,
        countryPhoneNumber: str,
        phoneNumber: str,
        password: str,
        userLang: str,
        timeZone: str,
        email: str | None = None,
        signup: bool = True,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._session = session
        self.refreshToken = None
        super().__init__(countryPhoneNumber, phoneNumber, password, userLang, timeZone, email, signup)

    async def runGqlQuery_a(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> GqlResponse:
        if query is None:
            raise HandlerException("GraphQL guery string MUST NOT be empty!")
        # Add Xplora® API headers
        requestHeaders = self.getRequestHeaders("application/json; charset=UTF-8")
        # create GQLClient
        gqlClient = GraphqlClient(endpoint=ENDPOINT, headers=requestHeaders)
        # execute QUERY|MUTATION
        if self._session:
            data = await gqlClient.ha_execute_async(query=query, variables=variables, operation_name=operation_name, session=self._session)
        else:
            data = await gqlClient.execute_async(query=query, variables=variables, operation_name=operation_name)
        return cast(GqlResponse, data)

    async def runAuthorizedGqlQuery_a(
        self, query: str, variables: dict[str, Any] | None = None, operation_name: str | None = None
    ) -> GqlResponse:
        """Run an authenticated (token-bearing) GraphQL query and detect token expiry.

        The single chokepoint every authenticated fetcher routes through. `login_a` and
        `refresh_token_a` deliberately stay on the raw `runGqlQuery_a` instead: a login or
        refresh call returning `E000004` is a credential/bootstrap problem, not something a
        token refresh can fix, so it must surface as `LoginError`, not `AuthError`.
        """
        resp = await self.runGqlQuery_a(query, variables, operation_name)
        for err in resp.get("errors") or []:
            # Xplora returns the code as a TOP-LEVEL error field, NOT under `extensions`
            # (ref:XW-004) -- also matches this library's own `{"code": "E", ...}` sentinel.
            # Read top-level first; keep an `extensions` fallback only as defensive
            # forward-compat.
            code = err.get("code") or (err.get("extensions") or {}).get("code")
            if code == AUTH_TOKEN_EXPIRED_CODE:
                raise AuthError()
        return resp

    async def login_a(self, key: str | None, sec: str | None) -> tuple[dict[str, Any], Any]:
        if key and sec:
            self._API_KEY = key
            self._API_SECRET = sec
        dataAll = await self.runGqlQuery_a(gm.SIGN_M.get("signInWithEmailOrPhoneM", ""), self.variables, "signInWithEmailOrPhone")
        if dataAll is None:
            return
        errors = dataAll.get("errors", None)
        if errors:
            self.errors.append({"function": "login", "errors": errors})
        data = dataAll.get("data", {})
        signIn: dict[str, Any] | None = data.get("signInWithEmailOrPhone", None)
        if signIn is None:
            error_message = dataAll.get("errors", [{"message": ""}])[0].get("message", "")
            if error_message:
                raise LoginError(f"Login error: {error_message}")
            raise LoginError("The server is not responding, please wait a moment and try again.")

        self.issueToken = signIn
        self.refreshToken = self.issueToken.get("refreshToken", None)
        self.sessionId = self.issueToken.get("id")
        self.userId = self.issueToken.get("user", {"id": None}).get("id", None)
        self.accessToken = self.issueToken.get("token", None)
        # `w360` (if present) is a separate downstream service credential, not the API
        # bearer secret -- requests are always signed with the static M1/M2 (ref:XW-005,
        # see `HandlerGQL.getRequestHeaders`). Deliberately not reassigning `_API_KEY`/
        # `_API_SECRET` from it here.

        return self.issueToken, self.refreshToken

    async def _runControlMutation_a(self, query: str, variables: dict[str, Any], operation_name: str) -> bool:
        """Run a fire-and-forget device-control mutation (`ShutDown` / `reboot`) and return the
        server's accept/reject `Boolean`.

        No client-side guardian/admin check: the **server** is the sole authority on whether the
        caller may control the watch. The official app sends the bare `shutDown(uid)`/`reboot(uid)`
        mutation with no precondition (a non-primary guardian can shut a watch down), so gating it
        on `guardianType == "FIRST"` here was a client-invented restriction stricter than the real
        backend authorization (ref:XW-007). Routed through
        `runAuthorizedGqlQuery_a`, so an `E000004` still raises `AuthError` for the normal recovery.
        """
        data: dict[str, Any] = (await self.runAuthorizedGqlQuery_a(query, variables, operation_name)).get("data", {})
        # The response field name matches the operation name case-insensitively (`ShutDown` ->
        # `shutDown`, `reboot` -> `reboot`).
        for k in data:
            if k.upper() == operation_name.upper():
                return bool(data.get(k, False))
        return False

    ########## SECTION QUERY start ##########

    async def get_device_list_a(self) -> dict[str, Any]:
        """Account-wide status query (battery, online status, location, steps, xcoin, unread
        chat count) for every watch in one call -- no `uid` variable. Replaces the old
        per-watch fan-out of `Watches`/`WatchState`/`askWatchLocate`/`WatchLastLocate`/
        `UserSteps`/`UnReadChatMsgCount` for that status subset (see ISSUE-12). Routed through
        `runAuthorizedGqlQuery_a` like every other authenticated fetch so an `E000004` on this
        path still triggers the `AuthError` recovery.
        """
        data: GqlResponse = await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("deviceListQ", ""), {}, "deviceList")
        # Raw, unparsed `deviceList` response -- the authoritative source of the `battery`
        # field (per `WatchListItem`) before any of our indexing/parsing touches it.
        # Disabled by default (the payload is large and contains personal data); uncomment to
        # inspect what the server actually returns. See `_RAW_LOGGER` for how to enable it.
        # _RAW_LOGGER.debug("raw deviceList response: %s", data)
        errors = data.get("errors", [])
        if errors:
            self.errors.append({"function": "get_device_list", "errors": errors})
        return data.get("data", {})

    async def askWatchLocate_a(self, wuid: str) -> dict[str, Any]:
        data: GqlResponse = await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("askLocateQ", ""), {"uid": wuid}, "AskWatchLocate")
        errors = data.get("errors", [])
        if errors:
            self.errors.append({"function": "askWatchLocate", "errors": errors})
        res: dict[str, Any] = data.get("data", {})
        if res.get("askWatchLocate", None) is not None:
            return res
        return {"askWatchLocate": False}

    async def getWatchUserContacts_a(self, wuid: str) -> dict[str, Any]:
        # Contacts from ownUser
        data: GqlResponse = await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("contactsQ", ""), {"uid": wuid}, "Contacts")
        errors = data.get("errors", [])
        if errors:
            self.errors.append({"function": "getWatchUserContacts", "errors": errors})
        return data.get("data", {})

    async def getWatches_a(self, wuid: str) -> dict[str, Any]:
        data: GqlResponse = await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("watchesQ", ""), {"uid": wuid}, "Watches")
        errors = data.get("errors", [])
        if errors:
            self.errors.append({"function": "getWatches", "errors": errors})
        return data.get("data", {})

    async def getSWInfo_a(self, qrCode: str) -> dict[str, Any]:
        data: GqlResponse = await self.runAuthorizedGqlQuery_a(
            gq.WATCH_Q.get("checkByQrCodeQ", ""), {"qrCode": qrCode}, "CheckWatchByQrCode"
        )
        errors = data.get("errors", [])
        if errors:
            self.errors.append({"function": "getSWInfo", "errors": errors})
        return data.get("data", {})

    async def getWatchState_a(self, qrCode: str, qrt: str = "", qrc: str = "") -> dict[str, Any]:
        variables = {}
        if qrCode:
            variables["qrCode"] = qrCode
        if qrt:
            variables["qrt"] = qrt
        if qrc:
            variables["qrc"] = qrc
        data: GqlResponse = await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("stateQ", ""), variables, "WatchState")
        errors = data.get("errors", [])
        if errors:
            self.errors.append({"function": "getWatchState", "errors": errors})
        return data.get("data", {})

    async def getWatchLastLocation_a(self, wuid: str) -> dict[str, Any]:
        # Auth-failure detection (E000004) is centralized in `runAuthorizedGqlQuery_a`,
        # which raises `AuthError` before returning here -- this no longer needs its own
        # ad-hoc `ErrorMSG.AUTH_FAIL` sentinel check.
        data: GqlResponse = await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("locateQ", ""), {"uid": wuid}, "WatchLastLocate")
        errors = data.get("errors", [])
        if errors:
            self.errors.append({"function": "getWatchLastLocation", "errors": errors})
        return data.get("data", {})

    async def trackWatch_a(self, wuid: str) -> dict[str, Any]:
        # tracking time - seconds
        data: GqlResponse = await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("trackQ", ""), {"uid": wuid}, "TrackWatch")
        errors = data.get("errors", [])
        if errors:
            self.errors.append({"function": "trackWatch", "errors": errors})
        res = data.get("data", {})
        if res.get("trackWatch", {"trackWatch": -1}):
            return res
        return {"trackWatch": -1}

    async def getAlarmTime_a(self, wuid: str) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("alarmsQ", ""), {"uid": wuid}, "Alarms")).get("data", {})

    async def getWifi_a(self, wuid: str) -> dict[str, Any]:
        # without function?
        return (await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("getWifisQ", ""), {"uid": wuid}, "GetWifis")).get("data", {})

    async def unReadChatMsgCount_a(self, wuid: str) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("unReadChatMsgCountQ", ""), {"uid": wuid}, "UnReadChatMsgCount")).get(
            "data", {}
        )

    async def safeZones_a(self, wuid: str) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("safeZonesQ", ""), {"uid": wuid}, "SafeZones")).get("data", {})

    async def safeZoneGroups_a(self) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("safeZoneGroupsQ", ""), {}, "SafeZoneGroups")).get("data", {})

    async def silentTimes_a(self, wuid: str) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("silentTimesQ", ""), {"uid": wuid}, "SlientTimes")).get("data", {})

    async def chats_a(
        self, wuid: str, offset: int = 0, limit: int = 0, msgId: str = "", asObject: bool = False
    ) -> dict[str, Any] | Chats | ChatsNew | str | None:
        # ownUser id
        res: GqlResponse = await self.runAuthorizedGqlQuery_a(
            gq.WATCH_Q.get("chatsQ", ""), {"uid": wuid, "offset": offset, "limit": limit, "msgId": msgId}, "Chats"
        )
        if res.get("errors", None) or res.get("data", None) is None:
            if asObject:
                _LOGGER.error(res.get("errors", None))
                return Chats.from_dict(res.get("data", {}))
            return {}
        if asObject:
            return Chats.from_dict(res.get("data", {}))
        return res.get("data", {})

    async def fetchChatImage_a(self, wuid: str, msgId: str) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("fetchChatImageQ", ""), {"uid": wuid, "msgId": msgId}, "FetchChatImage")
        ).get("data", {})

    async def fetchChatMp3_a(self, wuid: str, msgId: str) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("fetchChatMp3Q", ""), {"uid": wuid, "msgId": msgId}, "FetchChatMp3")).get(
            "data", {}
        )

    async def fetchChatShortVideo_a(self, wuid: str, msgId: str) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gq.WATCH_Q.get("fetchChatShortVideoQ", ""), {"uid": wuid, "msgId": msgId}, "FetchChatShortVideo"
            )
        ).get("data", {})

    async def fetchChatShortVideoCover_a(self, wuid: str, msgId: str) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gq.WATCH_Q.get("fetchChatShortVideoCoverQ", ""), {"uid": wuid, "msgId": msgId}, "FetchChatShortVideoCover"
            )
        ).get("data", {})

    async def fetchChatVoice_a(self, wuid: str, msgId: str) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("fetchChatVoiceQ", ""), {"uid": wuid, "msgId": msgId}, "FetchChatVoice")
        ).get("data", {})

    async def watchImei_a(self, imei: str, qrCode: str, deviceKey: str) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gq.WATCH_Q.get("imeiQ", ""), {"imei": imei, "qrCode": qrCode, "deviceKey": deviceKey}, "WatchImei"
            )
        ).get("data", {})

    async def getWatchLocHistory_a(self, wuid: str, date: int | None = None, tz: str | None = None, limit: int = 1) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gq.WATCH_Q.get("locHistoryQ", ""), {"uid": wuid, "date": date, "tz": tz, "limit": limit}, "LocHistory"
            )
        ).get("data", {})

    async def watchesDynamic_a(self) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("watchesDynamicQ", ""), {}, "WatchesDynamic")).get("data", {})

    async def coinHistory_a(self, wuid: str, start: int, end: int, _type: str, offset: int, limit: int) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gq.XCOIN_Q.get("historyQ", ""),
                {"uid": wuid, "start": start, "end": end, "type": _type, "offset": offset, "limit": limit},
                "CoinHistory",
            )
        ).get("data", {})

    async def reminders_a(self, wuid: str) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.XMOVE_Q.get("remindersQ", ""), {"uid": wuid}, "Reminders")).get("data", {})

    async def groups_a(self, isCampaign: bool) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.CARD_Q.get("groupsQ", ""), {"isCampaign": isCampaign}, "CardGroups")).get("data", {})

    async def dynamic_a(self) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.CARD_Q.get("dynamicQ", ""), {}, "DynamicCards")).get("data", {})

    async def staticCard_a(self) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.CARD_Q.get("staticQ", ""), {}, "StaticCard")).get("data", {})

    async def familyInfo_a(self, wuid: str, watchId: str, tz: str, date: int) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gq.FAMILY_Q.get("infoQ", ""), {"uid": wuid, "watchId": watchId, "tz": tz, "date": date}, "FamilyInfo"
            )
        ).get("data", {})

    async def getMyTotalInfo_a(
        self, wuid: str, tz: str, date: int, start: int, end: int, _type: str, offset: int, limit: int
    ) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gq.MYINFO_Q.get("getMyTotalInfoQ", ""),
                {
                    "uid": wuid,
                    "tz": tz,
                    "date": date,
                    "start": start,
                    "end": end,
                    "type": _type,
                    "offset": offset,
                    "limit": limit,
                },
                "GetMyTotalInfo",
            )
        ).get("data", {})

    async def myInfoWithCoinHistory_a(
        self, wuid: str, start: int, end: int, tz: str, _type: str, offset: int, limit: int
    ) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gq.MYINFO_Q.get("myInfoWithCoinHistoryQ", ""),
                {"uid": wuid, "start": start, "end": end, "tz": tz, "type": _type, "offset": offset, "limit": limit},
                "MyInfoWithCoinHistory",
            )
        ).get("data", {})

    async def getMyInfo_a(self) -> dict[str, Any]:
        # Profil from login Account
        return (await self.runAuthorizedGqlQuery_a(gq.MYINFO_Q.get("readQ", ""), {}, "ReadMyInfo")).get("data", {})

    async def readCampaignProfile_a(self, wuid: str) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gq.MYINFO_Q.get("readCampaignProfileQ", ""),
                {"uid": wuid},
            )
        ).get("data", {})

    async def getReviewStatus_a(self, wuid: str) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.REVIEW_Q.get("getStatusQ", ""), {"uid": wuid}, "GetReviewStatus")).get("data", {})

    async def getWatchUserSteps_a(self, wuid: str, tz: str, date: int) -> dict[str, Any]:
        data: GqlResponse = await self.runAuthorizedGqlQuery_a(
            gq.STEP_Q.get("userQ", ""), {"uid": wuid, "tz": tz, "date": date}, "UserSteps"
        )
        errors = data.get("errors", [])
        if errors:
            self.errors.append({"function": "getWatchUserSteps", "errors": errors})
        return data.get("data", {})

    async def countries_a(self) -> dict[str, Any]:
        # Country Support
        return (await self.runAuthorizedGqlQuery_a(gq.UTILS_Q.get("countriesQ", ""), {}, "Countries")).get("data", {})

    async def avatars_a(self, _id: str) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.CAMPAIGN_Q.get("avatarsQ", ""), {"id": _id}, "Avatars")).get("data", {})

    async def getFollowRequestWatchCount_a(self) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.CAMPAIGN_Q.get("followRequestWatchCountQ", ""), {}, "FollowRequestWatchCount")).get(
            "data", {}
        )

    async def campaigns_a(self, _id: str, categoryId: str) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(gq.CAMPAIGN_Q.get("campaignsQ", ""), {"id": _id, "categoryId": categoryId}, "Campaigns")
        ).get("data", {})

    async def isSubscribed_a(self, _id: str, wuid: str) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(gq.CAMPAIGN_Q.get("isSubscribedQ", ""), {"id": _id, "uid": wuid}, "IsSubscribedCampaign")
        ).get("data", {})

    async def subscribed_a(self, wuid: str, needDetail: bool) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gq.CAMPAIGN_Q.get("subscribedQ", ""), {"uid": wuid, "needDetail": needDetail}, "SubscribedCampaign"
            )
        ).get("data", {})

    async def ranks_a(self, campaignId: str) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.CAMPAIGN_Q.get("ranksQ", ""), {"campaignId": campaignId}, "Ranks")).get("data", {})

    async def conv360IDToO2OID_a(self, qid: str, deviceId: str) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gq.QUERY.get("conv360IDToO2OIDQ", ""), {"qid": qid, "deviceId": deviceId}, "Conv360IDToO2OID"
            )
        ).get("data", {})

    async def getAppVersion_a(self) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.QUERY.get("getAppVersionQ", ""), {}, "GetAppVersion")).get("data", {})

    async def watchGroups_a(self, _id: str = "") -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gq.WATCHGROUP_Q.get("watchGroupsQ", ""), {"id": _id}, "WatchGroups")).get("data", {})

    async def getStartTrackingWatch_a(self, wuid: str) -> dict[str, Any]:
        data = await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("startTrackingWatchQ", ""), {"uid": wuid}, "StartTrackingWatch")
        errors: list[dict[str, str]] = data.get("errors", [])
        if errors:
            self.errors.append({"function": "getStartTrackingWatch", "error": errors})
        return data.get("data", {})

    async def getEndTrackingWatch_a(self, wuid: str) -> dict[str, Any]:
        data = await self.runAuthorizedGqlQuery_a(gq.WATCH_Q.get("endTrackingWatchQ", ""), {"uid": wuid}, "EndTrackingWatch")
        errors: list[dict[str, str]] = data.get("errors", [])
        if errors:
            self.errors.append({"function": "getEndTrackingWatch", "error": errors})
        return data.get("data", {})

    async def checkEmailOrPhoneExist_a(
        self, _type: UserContactType, email: str = "", countryCode: str = "", phoneNumber: str = ""
    ) -> dict[str, bool]:
        data: GqlResponse = await self.runAuthorizedGqlQuery_a(
            gq.UTILS_Q.get("checkEmailOrPhoneExistQ", ""),
            {"type": _type.value, "email": email, "countryCode": countryCode, "phoneNumber": phoneNumber},
            "CheckEmailOrPhoneExist",
        )
        return data.get("data", {})

    ########## SECTION QUERY end ##########

    ########## SECTION MUTATION start ##########

    async def sendText_a(self, wuid: str, text: str) -> bool:
        # ownUser id
        result = await self.runAuthorizedGqlQuery_a(gm.WATCH_M.get("sendChatTextM", ""), {"uid": wuid, "text": text}, "SendChatText")
        errors = result.get("errors", None)
        if errors is not None:
            for error in errors:
                _LOGGER.error(error)
        if result.get("data", {})["sendChatText"] is not None:
            return True
        return False

    async def addStep_a(self, stepCount: int) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gm.STEP_M.get("addM", ""), {"stepCount": stepCount}, "AddStep")).get("data", {})

    async def shutdown_a(self, wuid: str) -> bool:
        return await self._runControlMutation_a(gm.WATCH_M.get("shutdownM", ""), {"uid": wuid}, "ShutDown")

    async def reboot_a(self, wuid: str) -> bool:
        return await self._runControlMutation_a(gm.WATCH_M.get("rebootM", ""), {"uid": wuid}, "reboot")

    async def modifyAlert_a(self, _id: str, yesOrNo: str) -> dict[str, Any]:
        # function?
        return (await self.runAuthorizedGqlQuery_a(gm.WATCH_M.get("modifyAlertM", ""), {"uid": _id, "remind": yesOrNo}, "modifyAlert")).get(
            "data", {}
        )

    async def setEnableSilentTime_a(self, silent_id: str, status: str = NormalStatus.ENABLE.value) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gm.WATCH_M.get("setEnableSlientTimeM", ""), {"silentId": silent_id, "status": status}, "SetEnableSlientTime"
            )
        ).get("data", {})

    async def setEnableAlarmTime_a(self, alarm_id: str, status: str = NormalStatus.ENABLE.value) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(gm.WATCH_M.get("modifyAlarmM", ""), {"alarmId": alarm_id, "status": status}, "ModifyAlarm")
        ).get("data", {})

    async def addAlarmTime_a(
        self, wuid: str, occur_min: int, start: int, week_repeat: str, name: str = "", end: int | None = None
    ) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gm.WATCH_M.get("addAlarmM", ""),
                {"uid": wuid, "name": name, "occurMin": occur_min, "start": start, "end": end, "weekRepeat": week_repeat},
                "AddAlarm",
            )
        ).get("data", {})

    async def modifyAlarmTime_a(
        self,
        alarm_id: str,
        occur_min: int | None = None,
        start: int | None = None,
        week_repeat: str | None = None,
        name: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        # Only forward the variables the caller actually set; the mutation declares them all optional.
        variables: dict[str, Any] = {"alarmId": alarm_id}
        if occur_min is not None:
            variables["occurMin"] = occur_min
        if start is not None:
            variables["start"] = start
        if week_repeat is not None:
            variables["weekRepeat"] = week_repeat
        if name is not None:
            variables["name"] = name
        if status is not None:
            variables["status"] = status
        return (await self.runAuthorizedGqlQuery_a(gm.WATCH_M.get("modifyAlarmM", ""), variables, "ModifyAlarm")).get("data", {})

    async def removeAlarmTime_a(self, alarm_id: str) -> dict[str, Any]:
        return (await self.runAuthorizedGqlQuery_a(gm.WATCH_M.get("removeAlarmM", ""), {"alarmId": alarm_id}, "RemoveAlarm")).get(
            "data", {}
        )

    async def addSilentTime_a(self, wuid: str, start: int, end: int, week_repeat: str, description: str = "") -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gm.WATCH_M.get("addSilentTimeM", ""),
                {"uid": wuid, "start": start, "end": end, "weekRepeat": week_repeat, "description": description},
                "AddSilentTime",
            )
        ).get("data", {})

    async def modifySilentTime_a(
        self,
        silent_id: str,
        start: int | None = None,
        end: int | None = None,
        week_repeat: str | None = None,
    ) -> dict[str, Any]:
        # Only forward the variables the caller actually set; the mutation declares them all optional.
        variables: dict[str, Any] = {"silentId": silent_id}
        if start is not None:
            variables["start"] = start
        if end is not None:
            variables["end"] = end
        if week_repeat is not None:
            variables["weekRepeat"] = week_repeat
        return (await self.runAuthorizedGqlQuery_a(gm.WATCH_M.get("modifySilentTimeM", ""), variables, "ModifySilentTime")).get("data", {})

    async def removeSilentTime_a(self, silent_id: str) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(gm.WATCH_M.get("removeSilentTimeM", ""), {"silentId": silent_id}, "RemoveSilentTime")
        ).get("data", {})

    async def setReadChatMsg_a(self, wuid: str, msgId: str, _id: str) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gm.WATCH_M.get("setReadChatMsgM", ""), {"uid": wuid, "msgId": msgId, "id": _id}, "setReadChatMsg"
            )
        ).get("data", {})

    async def submitIncorrectLocationData_a(self, wuid: str, lat: str, lng: str, timestamp: str) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gm.WATCH_M.get("submitIncorrectLocationDataM", ""),
                {"uid": wuid, "lat": lat, "lng": lng, "timestamp": timestamp},
                "SubmitIncorrectLocationData",
            )
        ).get("data", {})

    async def modifyContact_a(self, contactId: str, isAdmin: bool, contactName: str = "", fileId: str = "") -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gm.WATCH_M.get("modifyContactM", ""),
                {"contactId": contactId, "contactName": contactName, "fileId": fileId, "isAdmin": isAdmin},
            )
        ).get("data", {})

    async def issueEmailOrPhoneCode_a(
        self,
        purpose: EmailAndPhoneVerificationTypeV2 = EmailAndPhoneVerificationTypeV2.UNKNOWN__,
        _type: UserContactType = UserContactType.UNKNOWN__,
        email: str = "",
        phoneNumber: str = "",
        countryCode: str = "",
        previousToken: str = "",
        lang: str = "",
    ) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gm.SIGN_M.get("issueEmailOrPhoneCodeM", ""),
                {
                    "purpose": purpose.value,
                    "type": _type.value,
                    "email": email,
                    "phoneNumber": phoneNumber,
                    "countryCode": countryCode,
                    "previousToken": previousToken,
                    "lang": lang,
                },
                "IssueEmailOrPhoneCode",
            )
        ).get("data", {})

    async def signUpWithEmailAndPhoneV2_a(
        self,
        countryPhoneCode: str = "",
        phoneNumber: str = "",
        password: str = "",
        name: str = "",
        emailAddress: str = "",
        emailConsent: int = -1,
    ) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gm.SIGN_M.get("signUpWithEmailAndPhoneV2M", ""),
                {
                    "countryPhoneCode": countryPhoneCode,
                    "phoneNumber": phoneNumber,
                    "password": password,
                    "name": name,
                    "emailAddress": emailAddress,
                    "emailConsent": emailConsent,
                },
                "SignUpWithEmailAndPhoneV2",
            )
        ).get("data", {})

    async def verifyCaptcha_a(self, captchaString: str = "", _type: str = "") -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gm.SIGN_M.get("verifyCaptchaM", ""), {"captchaString": captchaString, "type": _type}, "verifyCaptcha"
            )
        ).get("data", {})

    async def verifyEmailOrPhoneCode_a(
        self,
        _type: UserContactType = UserContactType.UNKNOWN__,
        email: str = "",
        phoneNumber: str = "",
        countryCode: str = "",
        verifyCode: str = "",
        verificationToken: str = "",
    ) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(
                gm.SIGN_M.get("verifyEmailOrPhoneCodeM", ""),
                {
                    "type": _type.value,
                    "email": email,
                    "phoneNumber": phoneNumber,
                    "countryCode": countryCode,
                    "verifyCode": verifyCode,
                    "verificationToken": verificationToken,
                },
                "verifyEmailOrPhoneCode",
            )
        ).get("data", {})

    async def deleteMessageFromApp_a(self, wuid: str, msgId: str) -> dict[str, Any]:
        return (
            await self.runAuthorizedGqlQuery_a(gm.WATCH_M.get("deleteChatMessageM", ""), {"uid": wuid, "msgId": msgId}, "DeleteChatMessage")
        ).get("data", {})

    async def connect360_a(self) -> dict[str, Any]:
        data: GqlResponse = await self.runAuthorizedGqlQuery_a(gm.SIGN_M.get("connect360M", ""), {}, "connect360")
        return data.get("data", {})

    async def refresh_token_a(self, uid: str, refresh_token: str) -> GqlResponse:
        # `uid` MUST be the user id (`signIn.user.id`), not a watch id (ref:XW-006). A
        # refresh returning E000004/invalid is a "refresh failed" signal, not a
        # token-refresh trigger, so this stays on the raw `runGqlQuery_a` like `login_a`.
        # Returns the FULL envelope (data + errors) so `PyXploraApi.refresh()` can tell a
        # server-confirmed auth refusal (structured `errors` body) from an empty/transport
        # failure -- only the former warrants a full re-login.
        return await self.runGqlQuery_a(gm.SIGN_M.get("refreshTokenM", ""), {"uid": uid, "refreshToken": refresh_token}, "RefreshToken")

    async def expireToken_a(self) -> GqlResponse:
        # Server-side logout: invalidates the CURRENT bearer token (no variables -- it acts on
        # whatever token signs this request).
        # Deliberately on the raw `runGqlQuery_a`, NOT `runAuthorizedGqlQuery_a`: a logout that
        # comes back `E000004` means the token is already dead, which is exactly the desired
        # end state -- raising `AuthError` there would be nonsensical. Returns the full
        # envelope so the caller can report whether the server actually acknowledged it.
        return await self.runGqlQuery_a(gm.SIGN_M.get("expireTokenM", ""), {}, "ExpireToken")

    ########## SECTION MUTATION end ##########
