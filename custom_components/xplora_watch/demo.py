"""Synthetic, network-free `PyXploraApi` stand-in for screenshot/documentation purposes.

Gated solely by the entry's own sign-in email matching `DEMO_ACCOUNT_EMAIL`
(`demo@xplora-watch.invalid`). The address uses the RFC 6761 reserved `.invalid` TLD, so it can
never belong to a real Xplora account -- a real user would never type it, and a real config entry
can never carry it. That makes the email a safe, self-sufficient switch: the demo entry is
network-free on its own (no environment variable, no real login attempt on startup), while a real
entry -- whose email never matches -- is never swapped to the demo controller.

`make_controller()` is the single entry point both `config_flow.py` and `coordinator.py` use to
build a controller, so the check lives in exactly one place.

`DemoPyXploraApi` only overrides the methods that would otherwise reach `self._gql_handler` (a
real network client). Everything else -- `getUserName`, `getWatchUserNames`, `getDevice`,
`getWatchUserIDs`, ... -- is inherited unmodified from `PyXplora`/`PyXploraApi` and works off the
`self.watchs` / `self.user` / `self.device` state seeded by the overrides below.
"""

from __future__ import annotations

import time
from typing import Any, Final

from .const import DEMO_ACCOUNT_EMAIL
from .pyxplora_api.const import ALL_WATCH_FUNCTIONS, WatchFunction
from .pyxplora_api.model import ChatsNew, Data, SimpleChat, User
from .pyxplora_api.pyxplora_api_async import FetchError, PyXploraApi, TokenRefreshOutcome
from .pyxplora_api.status import ChatType, LocationType, NormalStatus, WatchOnlineStatus

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


def is_demo_account(email: str | None) -> bool:
    """Whether `email` is the dedicated demo sentinel (`demo@xplora-watch.invalid`)."""
    return (email or "").strip().lower() == DEMO_ACCOUNT_EMAIL


def make_controller(**kwargs: Any) -> PyXploraApi:
    """Build the controller: a network-free `DemoPyXploraApi` iff the email is the demo sentinel.

    Single factory used by every `PyXploraApi(...)` call site (`config_flow.py`, `coordinator.py`)
    so the check lives in exactly one place. The sentinel email's `.invalid` TLD can never be a
    real account, so matching on it alone is safe: the demo entry never reaches the network (no
    real login attempt on startup), and a real entry is never swapped to the demo controller.
    """
    if is_demo_account(kwargs.get("email")):
        return DemoPyXploraApi(**kwargs)
    return PyXploraApi(**kwargs)


class DemoPyXploraApi(PyXploraApi):
    """Drop-in `PyXploraApi` replacement that never talks to the Xplora servers."""

    async def init(self, *args: Any, **kwargs: Any) -> None:
        """Seed the single demo child ("Patrick") instead of logging in."""
        self.user = {"id": DEMO_USER_ID, "name": "Demo Parent"}
        self.watchs = [
            {
                "ward": {
                    "id": DEMO_WUID,
                    "name": DEMO_CHILD_NAME,
                    "phoneNumber": "+490000000000",
                    "xcoin": DEMO_XCOIN,
                    "currentStep": DEMO_STEPS,
                    "totalStep": DEMO_STEPS,
                    "file": {"id": ""},
                }
            }
        ]
        self.dtIssueToken = int(time.time())

    async def checkEmailOrPhoneExist(self, *args: Any, **kwargs: Any) -> bool:
        """Skip the real (network) existence check used by the config flow's `validate_input`."""
        return True

    async def setDevices(self, ids: str | list[str] | None = None, functions: frozenset[WatchFunction] = ALL_WATCH_FUNCTIONS) -> list[str]:
        """Always resolve to the single demo watch, ignoring the requested `ids`."""
        return await self._setDevices([DEMO_WUID], functions=functions)

    async def getDeviceList(self) -> dict[str, dict[str, Any]]:
        """Synthetic `deviceList` entry: online, Lego Land Germany coordinates.

        Carries the watch-model fields (`swKey`/`osVersion`/`groupName`) that `_setDevice`
        now reads straight from the deviceList item instead of a separate `Watches` call.
        """
        return {
            DEMO_WUID: {
                "battery": DEMO_BATTERY,
                "onlineStatus": WatchOnlineStatus.ONLINE.value,
                "unreadChatMessageCount": 1,
                "swKey": "000000000000000",
                "osVersion": "1.0.0-demo",
                "groupName": "GPS-Watch",
                # Primary guardian -> the coordinator derives is_admin from this (no Contacts call).
                "guardianType": "FIRST",
                "location": {
                    "lat": DEMO_LAT,
                    "lng": DEMO_LNG,
                    "isCharging": False,
                    "locateType": LocationType.GPS.value,
                    "isInSafeZone": False,
                    "safeZoneLabel": "",
                    "poi": DEMO_POI,
                    "city": DEMO_CITY,
                    "province": DEMO_PROVINCE,
                    "country": DEMO_COUNTRY,
                    "rad": 15,
                    "step": DEMO_STEPS,
                    "distance": -1,
                    "tm": int(time.time()),
                },
                "stepsInfo": {"todaysSteps": DEMO_STEPS},
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
        """Always-fresh fix at Lego Land Germany, mirroring `PyXploraApi.loadWatchLocation`'s shape."""
        now = int(time.time())
        watch_last_location = {
            "tm": now,
            "lat": DEMO_LAT,
            "lng": DEMO_LNG,
            "rad": 15,
            "poi": DEMO_POI,
            "city": DEMO_CITY,
            "province": DEMO_PROVINCE,
            "country": DEMO_COUNTRY,
            "locateType": LocationType.GPS.value,
            "isInSafeZone": False,
            "safeZoneLabel": "",
            "battery": DEMO_BATTERY,
            "isCharging": False,
        }
        return {
            "tm": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "lat": DEMO_LAT,
            "lng": DEMO_LNG,
            "rad": 15,
            "poi": DEMO_POI,
            "city": DEMO_CITY,
            "province": DEMO_PROVINCE,
            "country": DEMO_COUNTRY,
            "locateType": LocationType.GPS.value,
            "isInSafeZone": False,
            "safeZoneLabel": "",
            "watch_battery": DEMO_BATTERY,
            "watch_charging": False,
            "watch_last_location": watch_last_location,
        }

    async def getWatchChatsRaw(self, wuid: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Two demo chat messages, so the message card has something to render."""
        now = int(time.time())
        chats = ChatsNew(
            [
                SimpleChat(
                    id="demo-msg-1",
                    msgId="demo-msg-1",
                    readFlag=0,
                    sender=User(id=DEMO_WUID, userId=DEMO_WUID, name=DEMO_CHILD_NAME),
                    receiver=User(id=DEMO_USER_ID, userId=DEMO_USER_ID, name="Demo Parent"),
                    data=Data(tm=now - 3600, sender_name=DEMO_CHILD_NAME, text="I'm at LEGOLAND with friends!"),
                    create=now - 3600,
                    type=ChatType.TEXT.value,
                ),
                SimpleChat(
                    id="demo-msg-2",
                    msgId="demo-msg-2",
                    readFlag=1,
                    sender=User(id=DEMO_USER_ID, userId=DEMO_USER_ID, name="Demo Parent"),
                    receiver=User(id=DEMO_WUID, userId=DEMO_WUID, name=DEMO_CHILD_NAME),
                    data=Data(tm=now - 3000, sender_name="Demo Parent", text="Have fun! Pick you up at 5."),
                    create=now - 3000,
                    type=ChatType.TEXT.value,
                ),
            ]
        )
        return chats.to_dict()

    async def getWatchLocHistory(self, wuid: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """A short synthetic track around the demo location, so the history map has a path to draw.

        Overrides the network-backed base method (demo mode has no server). Points are recent
        (last ~2 hours) so they fall inside the sensor's bounded window and the card's default range.
        `tm` is epoch seconds (the coordinator normalizes to ms), matching the real API.
        """
        now = int(time.time())
        # A handful of points walking away from and back toward the demo home, a few minutes apart.
        offsets = [0.0, 0.0006, 0.0013, 0.0009, 0.0003]
        points = [
            {
                "tm": now - (len(offsets) - i) * 600,
                "lat": DEMO_LAT + off,
                "lng": DEMO_LNG + off,
                "rad": 30,
                "city": "Demo City",
                "addr": f"Demo Street {i + 1}",
                "poi": "",
                "locateType": LocationType.GPS.value,
            }
            for i, off in enumerate(offsets)
        ]
        return {"locHistory": {"offset": 0, "limit": len(points), "list": points}}

    async def isAdmin(self, wuid: str) -> bool:
        """The demo account is always the admin of its single watch."""
        return True

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
        """No demo media files."""
        return None

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
        """No-op mutator: demo mode has no server to send to."""
        return True

    async def reboot(self, wuid: str) -> bool:
        """No-op mutator: demo mode has no server to send to."""
        return True

    async def addAlarmTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: demo mode has no server to send to."""
        return True

    async def modifyAlarmTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: demo mode has no server to send to."""
        return True

    async def removeAlarmTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: demo mode has no server to send to."""
        return True

    async def setEnableAlarmTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: demo mode has no server to send to."""
        return True

    async def setDisableAlarmTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: demo mode has no server to send to."""
        return True

    async def addSilentTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: demo mode has no server to send to."""
        return True

    async def modifySilentTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: demo mode has no server to send to."""
        return True

    async def removeSilentTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: demo mode has no server to send to."""
        return True

    async def setEnableSilentTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: demo mode has no server to send to."""
        return True

    async def setDisableSilentTime(self, *args: Any, **kwargs: Any) -> bool:
        """No-op mutator: demo mode has no server to send to."""
        return True
