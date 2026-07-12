"""Synthetic, network-free `PyXploraApi` stand-ins for screenshot/documentation purposes.

Gated solely by the entry's own sign-in email matching one of the demo sentinels (see
`_PROFILES`). Every sentinel uses the RFC 6761 reserved `.invalid` TLD, so it can never belong to a
real Xplora account -- a real user would never type it, and a real config entry can never carry it.
That makes the email a safe, self-sufficient switch: a demo entry is network-free on its own (no
environment variable, no real login attempt on startup), while a real entry -- whose email never
matches -- is never swapped to the demo controller.

Six sentinels seed six distinct accounts so the multi-account service fan-out (ADR 0004) can be
exercised in a live Home Assistant with no servers:

- `demo@xplora-watch.invalid` -- primary **Guardian** of "Patrick".
- `demo-second-parent@xplora-watch.invalid` -- a second **Guardian**, of a different child ("Rosa").
- `demo-contact@xplora-watch.invalid` -- a **Contact** (not the primary guardian) of "Timmy", so a
  control action targeting all of them skips it and the partial-success notification can be seen.
- `demo-offline@xplora-watch.invalid` -- a **Guardian** of "Max" whose watch is **offline**, so a
  control action is *refused* (the `watch_offline` surfacing) rather than looking like it worked.
  An offline watch also reports no live position (the coordinator drops its coordinates), so its map
  shows "Location unavailable" -- the stale fix time still shows in the overview header chip.
- `demo-error@xplora-watch.invalid` -- a **Guardian** of "Nora" whose watch loads normally but whose
  *forced* re-fix **raises**: the first locate (the setup refresh) succeeds so the entry loads with a
  real position, then every later locate errors. That is what the map card's Reload button hits --
  the browser e2e suite asserts the button recovers instead of staying stuck spinning (ADR 0008).
  Distinct from Offline, which returns a no-response (keeps the last fix), not an error.
- `demo-stale@xplora-watch.invalid` -- a **Guardian** of "Lena" whose watch is **online** (so it
  keeps its pin on the map) but did **not respond** to the locate request, so it keeps an older fix:
  the map draws a real pin under a "Watch didn't respond - location from N ago" banner and the header
  chip shows the fix age, not the poll time. This is the reachable-but-stale case ADR 0007 exists for
  -- distinct from Offline (no pin) and from Error (raises); here the locate simply returns `False`.

Put all six watch devices in one Home Assistant area, then a single service call targeting that
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
    DEMO_ERROR_ACCOUNT_EMAIL,
    DEMO_OFFLINE_ACCOUNT_EMAIL,
    DEMO_SECOND_PARENT_ACCOUNT_EMAIL,
    DEMO_STALE_ACCOUNT_EMAIL,
)
from .demo_voice import DEMO_VOICE_AMR_B64
from .pyxplora_api.const import ALL_WATCH_FUNCTIONS, WatchFunction
from .pyxplora_api.exception_classes import ConnectionError as XploraConnectionError
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
# --- Error demo account ("Nora", Guardian whose forced re-fix raises after the first success) -------
DEMO_ERROR_WUID: Final = "00000000-0000-0000-0000-000000000005"
# --- Stale demo account ("Lena", online Guardian whose watch did not respond to the locate) ---------
DEMO_STALE_WUID: Final = "00000000-0000-0000-0000-000000000006"

# How old the kept fix is for a watch that did not respond to the locate request. A no-response watch
# keeps its LAST fix (ADR 0007) -- captured before it stopped responding -- so the demo shows
# "location from N ago" instead of a fabricated "just now". Frozen at construction (see
# `_frozen_fix_tm`), so the shown age grows with wall-clock as the session runs; ~27 min reads as
# plainly stale, not borderline. Applies whether the watch is offline (no pin) or online (stale pin).
_DEMO_STALE_FIX_AGE_SECONDS: Final = 27 * 60


@dataclass(frozen=True)
class _DemoProfile:
    """Everything a single demo account seeds: its user, its one watch, and its Guardian/Contact role.

    ``guardian_type`` is what the coordinator derives ``is_admin`` from (``"FIRST"`` == the account is
    the watch's primary Guardian; anything else == a Contact, gated out of control actions, ADR 0001).
    ``has_voice`` adds the bundled AMR sample so the primary account still exercises the voice->mp3
    path; the others keep text-only chats so a shared media cache can't collide across accounts.
    ``online`` False models a switched-off/offline watch: the deviceList reports it offline and every
    control action is *refused* (returns ``False``), so a control service surfaces ``watch_offline``.
    It also gates the map pin -- the coordinator drops an offline watch's coordinates, so its map
    reads "Location unavailable" (the stale fix time still shows in the header chip).
    ``responds`` False models a watch that did **not** accept the locate request this cycle
    (``askWatchLocate`` returns ``False``): the coordinator records a ``no_response`` and keeps the
    last known fix, so the fix time is stale rather than "just now" (ADR 0007). Independent of
    ``online``: an ``online=True, responds=False`` watch keeps its **pin** (still online) but under a
    "Watch didn't respond - location from N ago" banner -- the reachable-but-stale case. An offline
    watch never responds regardless, so ``online=False`` implies no fresh fix too.
    ``safe_zone_label`` non-empty models a watch currently INSIDE a safezone of that name: every
    location payload reports ``isInSafeZone``/``safeZoneLabel`` accordingly and a matching safezone
    definition is served, so the `current_safezone` sensor, the safezone card tile and the per-zone
    tracker are all exercisable network-free. Empty (the default) keeps the watch outside every
    zone -- the sensor's unknown-state path.
    ``refresh_raises`` True models a watch whose *forced* re-fix fails: the FIRST locate still
    succeeds (so the entry loads with a real position and the map has something to draw), then every
    later ``askWatchLocate``/``loadWatchLocation`` raises. That is the map card's Reload path (it
    presses the watch's Update button), so a rejected press can be exercised in a real browser. It is
    distinct from ``online=False`` (Offline), which returns a no-response and keeps the last fix.
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
    responds: bool = True
    safe_zone_label: str = ""
    refresh_raises: bool = False


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
        # Patrick is inside the LEGOLAND safezone, so the safezone entities have live demo data.
        safe_zone_label="LEGOLAND",
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
        # An offline watch cannot accept a locate request either -> no-response, keeps the last fix.
        responds=False,
    ),
    DEMO_ERROR_ACCOUNT_EMAIL: _DemoProfile(
        user_id="demo_error_account",
        user_name="Demo Error",
        wuid=DEMO_ERROR_WUID,
        child_name="Nora",
        # A Guardian (so it drives locate tracking, like the primary) whose *forced* re-fix fails.
        guardian_type="FIRST",
        # Heide Park Resort, Soltau, Lower Saxony.
        lat=53.0330,
        lng=9.8720,
        poi="Heide Park Resort",
        city="Soltau",
        province="Niedersachsen",
        country="Deutschland",
        battery=57,
        steps=2960,
        xcoin=45,
        has_voice=False,
        refresh_raises=True,
    ),
    DEMO_STALE_ACCOUNT_EMAIL: _DemoProfile(
        user_id="demo_stale_account",
        user_name="Demo Stale",
        wuid=DEMO_STALE_WUID,
        child_name="Lena",
        # A Guardian whose watch is online (keeps its map pin) but did not respond to the locate.
        guardian_type="FIRST",
        # Tripsdrill, Cleebronn, Baden-Württemberg.
        lat=49.0397,
        lng=9.0803,
        poi="Erlebnispark Tripsdrill",
        city="Cleebronn",
        province="Baden-Württemberg",
        country="Deutschland",
        battery=83,
        steps=4210,
        xcoin=60,
        has_voice=False,
        # Online (so the pin is retained) but the locate request was not accepted this cycle ->
        # no-response, keeps an older fix. The map draws a real pin under "Watch didn't respond -
        # location from N ago" -- the reachable-but-stale case ADR 0007 exists for.
        responds=False,
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
        # For the `refresh_raises` profile: the FIRST fix cycle must still succeed so the entry loads
        # with a real position; only forced re-fixes after that raise. `askWatchLocate` is the
        # once-per-cycle entry point (`coordinator._refresh_watch_fix` calls it once, then reads
        # `loadWatchLocation` possibly several times), so the cycle count is tracked there.
        self._located_once = False
        # The frozen "last known fix" time a no-response watch keeps reporting: captured before the
        # watch stopped responding so its shown age grows over the session (ADR 0007). A watch that
        # responds to the locate ignores this and re-fixes to `now` -- see `_fix_tm`.
        self._frozen_fix_tm = int(time.time()) - _DEMO_STALE_FIX_AGE_SECONDS
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
                    "isInSafeZone": bool(profile.safe_zone_label),
                    "safeZoneLabel": profile.safe_zone_label,
                    "poi": profile.poi,
                    "city": profile.city,
                    "province": profile.province,
                    "country": profile.country,
                    "rad": 15,
                    "step": profile.steps,
                    "distance": -1,
                    # Fresh for a reachable watch, frozen-stale for an offline one (ADR 0007).
                    "tm": self._fix_tm(),
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
        """One safezone matching the profile's `safe_zone_label` scenario (none when outside).

        Keeps the demo data coherent: a watch reporting "inside <label>" also serves the matching
        safezone definition, so the disabled-by-default per-zone tracker can be enabled and shows
        the zone (with its `safezone_name` attribute) on the map.
        """
        profile = self._profile
        if not profile.safe_zone_label:
            return []
        return [
            {
                "vendorId": f"demo-safezone-{profile.wuid}",
                "groupName": profile.safe_zone_label,
                "name": profile.safe_zone_label,
                "lat": str(profile.lat),
                "lng": str(profile.lng),
                "rad": 300,
                "address": f"{profile.poi}, {profile.city}",
            }
        ]

    async def getWatches(self, wuid: str) -> dict[str, Any]:
        """Synthetic hardware info; `getSWInfo` (unused by the integration) is stubbed separately."""
        return {"imei": "000000000000000", "osVersion": "1.0.0-demo", "qrCode": "https://example.invalid/?=demo", "model": "GPS-Watch"}

    async def getSWInfo(self, wuid: str, watches: dict[str, Any] | None = None) -> dict[str, Any]:
        """Not surfaced anywhere in the integration; kept network-free for completeness."""
        return {}

    def getWatchUserIcons(self, wuid: str | list[str] | None = None) -> str | list[str]:
        """No avatar for the demo child, as requested."""
        return ""

    def _fix_tm(self) -> int:
        """The epoch-seconds `tm` of the fix this watch reports.

        Poll outcome and fix freshness are independent (ADR 0007). A watch that accepts the locate
        (`responds`) re-fixes to `now` on every read; a no-response watch keeps its last known fix,
        frozen at `_frozen_fix_tm` (captured before it stopped responding) so the shown "location
        from N ago" age grows over the session instead of fabricating a fresh "just now". This is
        independent of `online`: an online-but-unresponsive watch keeps its pin *and* reads stale.
        """
        return int(time.time()) if self._profile.responds else self._frozen_fix_tm

    async def askWatchLocate(self, wuid: str) -> bool:
        """Whether the watch accepted the locate request (accepted -> a fresh fix follows).

        A watch that does not respond (`responds=False` -- an offline watch, or an online watch that
        did not accept the locate this cycle) returns `False`: the coordinator records a `no_response`
        and keeps the last known fix (ADR 0007), rather than pretending it just took a fresh position.

        The `refresh_raises` profile responds, so its FIRST cycle returns `True` (the entry loads with
        a real position and the map draws), then every *forced* re-fix after it raises. This is the
        map card's Reload path (it presses the watch's Update button), so the exception propagates
        through the coordinator and the frontend `callService` rejects -- driving the card's
        failed-press recovery. A connection error (not auth/rate limit) keeps it a plain, retryable
        failure. Gating here, at the once-per-cycle entry point, fails the whole cycle before any
        `loadWatchLocation` read runs.
        """
        if self._profile.refresh_raises and self._located_once:
            raise XploraConnectionError("demo error persona: the watch did not accept the locate request")
        self._located_once = True
        return self._profile.responds

    async def loadWatchLocation(self, wuid: str = "", with_ask: bool = True) -> dict[str, Any]:
        """A fix at this account's location, mirroring `PyXploraApi.loadWatchLocation`'s shape.

        Fresh ("just now") for a reachable watch; the frozen last-known fix for an offline one, whose
        `tm` sits minutes in the past and does not advance -- the no-response/stale case (ADR 0007).
        """
        profile = self._profile
        fix_tm = self._fix_tm()
        watch_last_location = {
            "tm": fix_tm,
            "lat": profile.lat,
            "lng": profile.lng,
            "rad": 15,
            "poi": profile.poi,
            "city": profile.city,
            "province": profile.province,
            "country": profile.country,
            "locateType": LocationType.GPS.value,
            "isInSafeZone": bool(profile.safe_zone_label),
            "safeZoneLabel": profile.safe_zone_label,
            "battery": profile.battery,
            "isCharging": False,
        }
        return {
            "tm": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(fix_tm)),
            "lat": profile.lat,
            "lng": profile.lng,
            "rad": 15,
            "poi": profile.poi,
            "city": profile.city,
            "province": profile.province,
            "country": profile.country,
            "locateType": LocationType.GPS.value,
            "isInSafeZone": bool(profile.safe_zone_label),
            "safeZoneLabel": profile.safe_zone_label,
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
