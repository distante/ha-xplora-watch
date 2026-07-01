"""Synthetic, network-free `PyXploraApi` stand-ins for screenshot/documentation purposes.

Gated solely by the entry's own sign-in email matching one of the demo sentinels (see
`_PROFILES`). Every sentinel uses the RFC 6761 reserved `.invalid` TLD, so it can never belong to a
real Xplora account -- a real user would never type it, and a real config entry can never carry it.
That makes the email a safe, self-sufficient switch: a demo entry is network-free on its own (no
environment variable, no real login attempt on startup), while a real entry -- whose email never
matches -- is never swapped to the demo controller.

Four sentinels seed four distinct accounts so the multi-account service fan-out (ADR 0004) can be
exercised in a live Home Assistant with no servers:

- `demo@xplora-watch.invalid` -- primary **Guardian** of "Patrick".
- `demo-second-parent@xplora-watch.invalid` -- a second **Guardian**, of a different child ("Rosa").
- `demo-contact@xplora-watch.invalid` -- a **Contact** (not the primary guardian) of "Timmy", so a
  control action targeting all four skips it and the partial-success notification can be seen.
- `demo-offline@xplora-watch.invalid` -- a **Guardian** of "Max" whose watch is **offline**, so a
  control action is *refused* (the `watch_offline` surfacing) rather than looking like it worked.

Put all four watch devices in one Home Assistant area, then a single service call targeting that
area fans out across every account: the online Guardians act, the Contact is skipped, and the
offline watch is reported as offline.

`make_controller()` is the single entry point both `config_flow.py` and `coordinator.py` use to
build a controller, so the check lives in exactly one place.

`DemoPyXploraApi` only overrides the methods that would otherwise reach `self._gql_handler` (a
real network client). Everything else -- `getUserName`, `getWatchUserNames`, `getDevice`,
`getWatchUserIDs`, ... -- is inherited unmodified from `PyXplora`/`PyXploraApi` and works off the
`self.watchs` / `self.user` / `self.device` state seeded by the overrides below, per the account's
`_DemoProfile`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Final

from .const import (
    DEMO_ACCOUNT_EMAIL,
    DEMO_CONTACT_ACCOUNT_EMAIL,
    DEMO_OFFLINE_ACCOUNT_EMAIL,
    DEMO_SECOND_PARENT_ACCOUNT_EMAIL,
)
from .demo_voice import DEMO_VOICE_AMR_B64
from .pyxplora_api.const import ALL_WATCH_FUNCTIONS, WatchFunction
from .pyxplora_api.model import ChatsNew, Data, SimpleChat, User
from .pyxplora_api.pyxplora_api_async import FetchError, PyXploraApi, TokenRefreshOutcome
from .pyxplora_api.status import ChatType, LocationType, NormalStatus, WatchOnlineStatus

# --- Primary demo account ("Patrick", Guardian) -----------------------------------------------------
DEMO_WUID: Final = "00000000-0000-0000-0000-000000000001"
DEMO_CHILD_NAME: Final = "Patrick"
# LEGOLAND Deutschland Resort, Günzburg, Bavaria.
DEMO_LAT: Final = 47.9008
DEMO_LNG: Final = 9.7864
DEMO_POI: Final = "LEGOLAND Deutschland Resort"
DEMO_CITY: Final = "Günzburg"
DEMO_PROVINCE: Final = "Bayern"
DEMO_COUNTRY: Final = "Deutschland"
DEMO_BATTERY: Final = 78
DEMO_STEPS: Final = 3421
DEMO_XCOIN: Final = 120
# Dash-free, like a real Xplora account id: `_async_migrate_entries` (__init__.py) compares this
# raw id against entity `unique_id`s, which always have "-" already replaced with "_" at creation
# time (see e.g. sensor.py's `_attr_unique_id`). A dash here would never match that sanitized
# form, so the "already migrated, skip" check would never fire -- silently re-appending this id
# to every entity's unique_id on every reload instead of being a no-op.
DEMO_USER_ID: Final = "demo_parent_account"
# msgId of the synthetic VOICE chat message; `get_chat_voice` returns the bundled AMR sample for
# it so reading the demo chat exercises the real AMR->mp3 conversion end to end.
DEMO_VOICE_MSG_ID: Final = "demo-msg-3"

# --- Second Guardian demo account ("Rosa", a different child) ---------------------------------------
DEMO_SECOND_PARENT_WUID: Final = "00000000-0000-0000-0000-000000000002"
# --- Contact demo account ("Timmy", not the primary guardian) --------------------------------------
DEMO_CONTACT_WUID: Final = "00000000-0000-0000-0000-000000000003"
# --- Offline demo account ("Max", Guardian but the watch is switched off/offline) ------------------
DEMO_OFFLINE_WUID: Final = "00000000-0000-0000-0000-000000000004"


@dataclass(frozen=True)
class _DemoProfile:
    """Everything a single demo account seeds: its user, its one watch, and its Guardian/Contact role.

    ``guardian_type`` is what the coordinator derives ``is_admin`` from (``"FIRST"`` == the account is
    the watch's primary Guardian; anything else == a Contact, gated out of control actions, ADR 0001).
    ``has_voice`` adds the bundled AMR sample so the primary account still exercises the voice->mp3
    path; the others keep text-only chats so a shared media cache can't collide across accounts.
    ``online`` False models a switched-off/offline watch: the deviceList reports it offline and every
    control action is *refused* (returns ``False``), so a control service surfaces ``watch_offline``.
    """

    user_id: str
    user_name: str
    wuid: str
    child_name: str
    guardian_type: str
    lat: float
    lng: float
    poi: str
    city: str
    province: str
    country: str
    battery: int
    steps: int
    xcoin: int
    has_voice: bool
    online: bool = True


_PROFILES: Final[dict[str, _DemoProfile]] = {
    DEMO_ACCOUNT_EMAIL: _DemoProfile(
        user_id=DEMO_USER_ID,
        user_name="Demo Parent",
        wuid=DEMO_WUID,
        child_name=DEMO_CHILD_NAME,
        guardian_type="FIRST",
        lat=DEMO_LAT,
        lng=DEMO_LNG,
        poi=DEMO_POI,
        city=DEMO_CITY,
        province=DEMO_PROVINCE,
        country=DEMO_COUNTRY,
        battery=DEMO_BATTERY,
        steps=DEMO_STEPS,
        xcoin=DEMO_XCOIN,
        has_voice=True,
    ),
    DEMO_SECOND_PARENT_ACCOUNT_EMAIL: _DemoProfile(
        user_id="demo_second_parent_account",
        user_name="Demo Second Parent",
        wuid=DEMO_SECOND_PARENT_WUID,
        child_name="Rosa",
        guardian_type="FIRST",
        # Europa-Park, Rust, Baden-Württemberg.
        lat=48.2664,
        lng=7.7220,
        poi="Europa-Park",
        city="Rust",
        province="Baden-Württemberg",
        country="Deutschland",
        battery=64,
        steps=5120,
        xcoin=88,
        has_voice=False,
    ),
    DEMO_CONTACT_ACCOUNT_EMAIL: _DemoProfile(
        user_id="demo_contact_account",
        user_name="Demo Contact",
        wuid=DEMO_CONTACT_WUID,
        child_name="Timmy",
        # Not "FIRST" -> a Contact: control actions (reboot/shutdown/alarm CRUD) gate this watch out.
        guardian_type="SECOND",
        # Miniatur Wunderland, Hamburg.
        lat=53.5511,
        lng=9.9937,
        poi="Miniatur Wunderland",
        city="Hamburg",
        province="Hamburg",
        country="Deutschland",
        battery=41,
        steps=1780,
        xcoin=30,
        has_voice=False,
    ),
    DEMO_OFFLINE_ACCOUNT_EMAIL: _DemoProfile(
        user_id="demo_offline_account",
        user_name="Demo Offline",
        wuid=DEMO_OFFLINE_WUID,
        child_name="Max",
        # A Guardian (so it is NOT gated) whose watch is offline -> control actions are refused.
        guardian_type="FIRST",
        # Phantasialand, Brühl, North Rhine-Westphalia.
        lat=50.7998,
        lng=6.8794,
        poi="Phantasialand",
        city="Brühl",
        province="Nordrhein-Westfalen",
        country="Deutschland",
        battery=12,
        steps=640,
        xcoin=15,
        has_voice=False,
        online=False,
    ),
}


def _profile_for(email: str | None) -> _DemoProfile:
    """The demo profile for a sentinel email (falls back to the primary account)."""
    return _PROFILES.get((email or "").strip().lower(), _PROFILES[DEMO_ACCOUNT_EMAIL])


def is_demo_account(email: str | None) -> bool:
    """Whether `email` is one of the dedicated demo sentinels (all under the `.invalid` TLD)."""
    return (email or "").strip().lower() in _PROFILES


def make_controller(**kwargs: Any) -> PyXploraApi:
    """Build the controller: a network-free `DemoPyXploraApi` iff the email is a demo sentinel.

    Single factory used by every `PyXploraApi(...)` call site (`config_flow.py`, `coordinator.py`)
    so the check lives in exactly one place. The sentinel emails' `.invalid` TLD can never be a
    real account, so matching on them alone is safe: a demo entry never reaches the network (no
    real login attempt on startup), and a real entry is never swapped to the demo controller.
    """
    if is_demo_account(kwargs.get("email")):
        return DemoPyXploraApi(**kwargs)
    return PyXploraApi(**kwargs)


class DemoPyXploraApi(PyXploraApi):
    """Drop-in `PyXploraApi` replacement that never talks to the Xplora servers.

    The account it stands in for is selected by the sign-in email at construction time (`_profile`),
    so each demo sentinel seeds its own watch identity and Guardian/Contact role.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Pick the demo profile from the sign-in email, then init the base client (network-free)."""
        self._profile = _profile_for(kwargs.get("email"))
        super().__init__(**kwargs)

    async def init(self, *args: Any, **kwargs: Any) -> None:
        """Seed this account's single demo child instead of logging in."""
        profile = self._profile
        self.user = {"id": profile.user_id, "name": profile.user_name}
        self.watchs = [
            {
                "ward": {
                    "id": profile.wuid,
                    "name": profile.child_name,
                    "phoneNumber": "+490000000000",
                    "xcoin": profile.xcoin,
                    "currentStep": profile.steps,
                    "totalStep": profile.steps,
                    "file": {"id": ""},
                }
            }
        ]
        self.dtIssueToken = int(time.time())

    async def checkEmailOrPhoneExist(self, *args: Any, **kwargs: Any) -> bool:
        """Skip the real (network) existence check used by the config flow's `validate_input`."""
        return True

    async def setDevices(self, ids: str | list[str] | None = None, functions: frozenset[WatchFunction] = ALL_WATCH_FUNCTIONS) -> list[str]:
        """Always resolve to this account's single demo watch, ignoring the requested `ids`."""
        return await self._setDevices([self._profile.wuid], functions=functions)

    async def getDeviceList(self) -> dict[str, dict[str, Any]]:
        """Synthetic `deviceList` entry for this account's watch (online, at the profile's location).

        Carries the watch-model fields (`swKey`/`osVersion`/`groupName`) that `_setDevice`
        now reads straight from the deviceList item instead of a separate `Watches` call, plus the
        `guardianType` the coordinator derives `is_admin` from.
        """
        profile = self._profile
        return {
            profile.wuid: {
                "battery": profile.battery,
                "onlineStatus": (WatchOnlineStatus.ONLINE if profile.online else WatchOnlineStatus.OFFLINE).value,
                "unreadChatMessageCount": 1,
                "swKey": "000000000000000",
                "osVersion": "1.0.0-demo",
                "groupName": "GPS-Watch",
                # Primary guardian ("FIRST") vs. Contact -> the coordinator derives is_admin from this.
                "guardianType": profile.guardian_type,
                "location": {
                    "lat": profile.lat,
                    "lng": profile.lng,
                    "isCharging": False,
                    "locateType": LocationType.GPS.value,
                    "isInSafeZone": False,
                    "safeZoneLabel": "",
                    "poi": profile.poi,
                    "city": profile.city,
                    "province": profile.province,
                    "country": profile.country,
                    "rad": 15,
                    "step": profile.steps,
                    "distance": -1,
                    "tm": int(time.time()),
                },
                "stepsInfo": {"todaysSteps": profile.steps},
            }
        }

    async def getWatchAlarm(self, wuid: str) -> list[dict[str, Any]] | FetchError:
        """Two demo alarms: a school-morning one and a daily bedtime reminder."""
        return [
            {
                "id": "demo-alarm-1",
                "vendorId": "demo-alarm-1",
                "name": "School",
                "start": "07:00",
                "weekRepeat": "0111110",  # Mon-Fri
                "status": NormalStatus.ENABLE.value,
            },
            {
                "id": "demo-alarm-2",
                "vendorId": "demo-alarm-2",
                "name": "Bedtime",
                "start": "20:30",
                "weekRepeat": "1111111",  # every day
                "status": NormalStatus.ENABLE.value,
            },
        ]

    async def getSilentTime(self, wuid: str) -> list[dict[str, Any]] | FetchError:
        """Two demo silent-time windows: school hours and overnight."""
        return [
            {
                "id": "demo-silent-1",
                "vendorId": "demo-silent-1",
                "start": "08:00",
                "end": "15:00",
                "weekRepeat": "0111110",  # Mon-Fri
                "status": NormalStatus.ENABLE.value,
            },
            {
                "id": "demo-silent-2",
                "vendorId": "demo-silent-2",
                "start": "21:00",
                "end": "06:30",
                "weekRepeat": "1111111",  # every day
                "status": NormalStatus.ENABLE.value,
            },
        ]

    async def getWatchSafeZones(self, wuid: str) -> list[dict[str, Any]] | FetchError:
        """No demo safe zones configured."""
        return []

    async def getWatches(self, wuid: str) -> dict[str, Any]:
        """Synthetic hardware info; `getSWInfo` (unused by the integration) is stubbed separately."""
        return {"imei": "000000000000000", "osVersion": "1.0.0-demo", "qrCode": "https://example.invalid/?=demo", "model": "GPS-Watch"}

    async def getSWInfo(self, wuid: str, watches: dict[str, Any] | None = None) -> dict[str, Any]:
        """Not surfaced anywhere in the integration; kept network-free for completeness."""
        return {}

    def getWatchUserIcons(self, wuid: str | list[str] | None = None) -> str | list[str]:
        """No avatar for the demo child, as requested."""
        return ""

    async def askWatchLocate(self, wuid: str) -> bool:
        """The demo watch always "responds" to a locate request."""
        return True

    async def loadWatchLocation(self, wuid: str = "", with_ask: bool = True) -> dict[str, Any]:
        """Always-fresh fix at this account's location, mirroring `PyXploraApi.loadWatchLocation`'s shape."""
        profile = self._profile
        now = int(time.time())
        watch_last_location = {
            "tm": now,
            "lat": profile.lat,
            "lng": profile.lng,
            "rad": 15,
            "poi": profile.poi,
            "city": profile.city,
            "province": profile.province,
            "country": profile.country,
            "locateType": LocationType.GPS.value,
            "isInSafeZone": False,
            "safeZoneLabel": "",
            "battery": profile.battery,
            "isCharging": False,
        }
        return {
            "tm": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "lat": profile.lat,
            "lng": profile.lng,
            "rad": 15,
            "poi": profile.poi,
            "city": profile.city,
            "province": profile.province,
            "country": profile.country,
            "locateType": LocationType.GPS.value,
            "isInSafeZone": False,
            "safeZoneLabel": "",
            "watch_battery": profile.battery,
            "watch_charging": False,
            "watch_last_location": watch_last_location,
        }

    async def getWatchChatsRaw(self, wuid: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Demo chat messages for this account's watch, so the message card has something to render.

        The primary account also carries one VOICE message (`has_voice`) so the voice->mp3
        conversion path can be exercised end to end (see `get_chat_voice`); the other accounts keep
        text-only chats so a shared media cache can't collide across accounts.
        """
        profile = self._profile
        now = int(time.time())
        child = User(id=profile.wuid, userId=profile.wuid, name=profile.child_name)
        parent = User(id=profile.user_id, userId=profile.user_id, name=profile.user_name)
        messages = [
            SimpleChat(
                id="demo-msg-1",
                msgId="demo-msg-1",
                readFlag=0,
                sender=child,
                receiver=parent,
                data=Data(tm=now - 3600, sender_name=profile.child_name, text=f"I'm at {profile.poi} with friends!"),
                create=now - 3600,
                type=ChatType.TEXT.value,
            ),
            SimpleChat(
                id="demo-msg-2",
                msgId="demo-msg-2",
                readFlag=1,
                sender=parent,
                receiver=child,
                data=Data(tm=now - 3000, sender_name=profile.user_name, text="Have fun! Pick you up at 5."),
                create=now - 3000,
                type=ChatType.TEXT.value,
            ),
        ]
        if profile.has_voice:
            messages.append(
                SimpleChat(
                    id=DEMO_VOICE_MSG_ID,
                    msgId=DEMO_VOICE_MSG_ID,
                    readFlag=0,
                    sender=child,
                    receiver=parent,
                    data=Data(tm=now - 2400, sender_name=profile.child_name),
                    create=now - 2400,
                    type=ChatType.VOICE.value,
                )
            )
        return ChatsNew(messages).to_dict()

    async def getWatchLocHistory(self, wuid: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """A short synthetic track around this account's location, so the history map has a path to draw.

        Overrides the network-backed base method (demo mode has no server). Points are recent
        (last ~2 hours) so they fall inside the sensor's bounded window and the card's default range.
        `tm` is epoch seconds (the coordinator normalizes to ms), matching the real API.
        """
        profile = self._profile
        now = int(time.time())
        # A handful of points walking away from and back toward the demo home, a few minutes apart.
        offsets = [0.0, 0.0006, 0.0013, 0.0009, 0.0003]
        points = [
            {
                "tm": now - (len(offsets) - i) * 600,
                "lat": profile.lat + off,
                "lng": profile.lng + off,
                "rad": 30,
                "city": profile.city,
                "addr": f"Demo Street {i + 1}",
                "poi": "",
                "locateType": LocationType.GPS.value,
            }
            for i, off in enumerate(offsets)
        ]
        return {"locHistory": {"offset": 0, "limit": len(points), "list": points}}

    async def isAdmin(self, wuid: str) -> bool:
        """Whether this account is the primary guardian of its watch (a Contact profile is not)."""
        return self._profile.guardian_type == "FIRST"

    async def refresh(self) -> TokenRefreshOutcome:
        """Never called in demo mode (`_hasTokenExpired` never trips), but kept network-free."""
        return TokenRefreshOutcome.REFRESHED

    async def logout(self) -> bool:
        """Pretend logout succeeded."""
        return True

    async def sendText(self, text: str, wuid: str) -> bool:
        """No-op mutator: demo mode has no server to send to."""
        return True

    async def deleteMessageFromApp(self, wuid: str, msgId: str) -> bool:
        """No-op mutator: demo mode has no server to send to."""
        return True

    async def get_chat_voice(self, wuid: str, msgId: str) -> str | None:
        """Return the bundled AMR sample (base64) for the demo voice message, else nothing.

        Mirrors the real API, which hands back the voice payload as a base64 string; the caller
        decodes it, writes the `.amr`, and transcodes it to mp3 via ffmpeg. Only the primary demo
        account carries a voice message (`has_voice`).
        """
        return DEMO_VOICE_AMR_B64 if (self._profile.has_voice and msgId == DEMO_VOICE_MSG_ID) else None

    async def get_chat_image(self, wuid: str, msgId: str) -> str | None:
        """No demo media files."""
        return None

    async def get_short_video(self, wuid: str, msgId: str) -> str | None:
        """No demo media files."""
        return None

    async def get_short_video_cover(self, wuid: str, msgId: str) -> str | None:
        """No demo media files."""
        return None

    async def shutdown(self, wuid: str) -> bool:
        """No-op mutator: succeeds unless this account's watch is offline (then refused)."""
        return self._profile.online

    async def reboot(self, wuid: str) -> bool:
        """No-op mutator: succeeds unless this account's watch is offline (then refused)."""
        return self._profile.online

    async def addAlarmTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: succeeds unless this account's watch is offline (then refused)."""
        return self._profile.online

    async def modifyAlarmTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: succeeds unless this account's watch is offline (then refused)."""
        return self._profile.online

    async def removeAlarmTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: succeeds unless this account's watch is offline (then refused)."""
        return self._profile.online

    async def setEnableAlarmTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: succeeds unless this account's watch is offline (then refused)."""
        return self._profile.online

    async def setDisableAlarmTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: succeeds unless this account's watch is offline (then refused)."""
        return self._profile.online

    async def addSilentTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: succeeds unless this account's watch is offline (then refused)."""
        return self._profile.online

    async def modifySilentTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: succeeds unless this account's watch is offline (then refused)."""
        return self._profile.online

    async def removeSilentTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: succeeds unless this account's watch is offline (then refused)."""
        return self._profile.online

    async def setEnableSilentTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: succeeds unless this account's watch is offline (then refused)."""
        return self._profile.online

    async def setDisableSilentTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: succeeds unless this account's watch is offline (then refused)."""
        return self._profile.online
