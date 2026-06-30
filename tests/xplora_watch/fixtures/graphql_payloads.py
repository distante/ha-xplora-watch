"""Canned 'happy path' GraphQL response payloads for the Xplora API, keyed by operationName.

These mirror the real shapes the vendored ``pyxplora_api`` GraphQL client expects to
parse (see ``gql_handler_async.py`` for the exact field access patterns). They are fed
into the ``aioresponses`` dispatcher in ``conftest.py`` so the real vendored client code
runs unmodified against a mocked transport layer.
"""

from __future__ import annotations

from typing import Any

DEFAULT_USER_ID = "user-id-001"
DEFAULT_ACCOUNT_NAME = "Parent Name"  # Account display name returned by getUserName()
DEFAULT_WUID = "watch-id-001"
DEFAULT_WARD_NAME = "Kid One"
DEFAULT_WARD_PHONE = "+491700000001"
DEFAULT_QR_CODE = "XPLORA=ABCDEF123456"


def make_login_payload(
    user_id: str = DEFAULT_USER_ID,
    wuid: str = DEFAULT_WUID,
    ward_name: str = DEFAULT_WARD_NAME,
    ward_phone: str = DEFAULT_WARD_PHONE,
    user_name: str = DEFAULT_ACCOUNT_NAME,
) -> dict[str, Any]:
    """Response for the ``signInWithEmailOrPhone`` mutation (login).

    ``user_name`` is the Account display name returned by ``getUserName()``; pass ``""`` to
    exercise the empty-display-name path (e.g. the account-token fallback to the account id).
    """
    return {
        "signInWithEmailOrPhone": {
            "id": "session-id-1",
            "token": "access-token-1",
            "refreshToken": "refresh-token-1",
            "user": {
                "id": user_id,
                "name": user_name,
                "children": [
                    {
                        "id": "child-rel-1",
                        "ward": {
                            "id": wuid,
                            "name": ward_name,
                            "phoneNumber": ward_phone,
                            "file": {"id": "file-1"},
                            "xcoin": 10,
                            "currentStep": 1234,
                            "totalStep": 56789,
                        },
                    }
                ],
            },
            "w360": {"token": "w360-token", "secret": "w360-secret"},
        }
    }


def make_contacts_payload(user_id: str = DEFAULT_USER_ID, guardian_type: str = "FIRST") -> dict[str, Any]:
    """Response for the ``Contacts`` query (used by isAdmin checks)."""
    return {
        "contacts": {
            "contacts": [
                {
                    "contactUser": {"id": user_id, "xcoin": 10},
                    "guardianType": guardian_type,
                    "create": 1700000000,
                    "update": 1700000000,
                    "name": "Parent Name",
                    "countryPhoneNumber": "49",
                    "phoneNumber": "1700000001",
                }
            ]
        }
    }


def make_watches_payload(qr_code: str = DEFAULT_QR_CODE) -> dict[str, Any]:
    """Response for the ``Watches`` query."""
    return {
        "watches": [
            {
                "swKey": "imei-0001",
                "osVersion": "1.2.3",
                "qrCode": qr_code,
                "groupName": "GPS-Watch",
            }
        ]
    }


def make_check_watch_by_qrcode_payload() -> dict[str, Any]:
    """Response for the ``CheckWatchByQrCode`` query (getSWInfo)."""
    return {"checkByQrCode": {"swVersion": "1.2.3"}}


def make_watch_last_locate_payload(
    lat: str = "52.5200",
    lng: str = "13.4050",
    rad: int = 50,
    is_in_safe_zone: bool = False,
    battery: int = 80,
    is_charging: bool = False,
) -> dict[str, Any]:
    """Response for the ``WatchLastLocate`` query."""
    return {
        "watchLastLocate": {
            "tm": 1700000000,
            "lat": lat,
            "lng": lng,
            "rad": rad,
            "poi": "Home",
            "city": "Berlin",
            "province": "Berlin",
            "country": "Germany",
            "locateType": "GPS",
            "isInSafeZone": is_in_safe_zone,
            "safeZoneLabel": "Home",
            "battery": battery,
            "isCharging": is_charging,
            "step": 1234,
            "distance": 42,
        }
    }


def make_device_list_payload(
    wuid: str = DEFAULT_WUID,
    lat: str = "52.5200",
    lng: str = "13.4050",
    rad: int = 50,
    is_in_safe_zone: bool = False,
    battery: int = 80,
    is_charging: bool = False,
    online_status: str = "ONLINE",
    today_steps: int = 1234,
    unread_chat_message_count: int = 0,
    guardian_type: str = "FIRST",
) -> dict[str, Any]:
    """Response for the ``deviceList`` query (one ``WatchListItem`` per watch, no ``uid`` arg).

    Mirrors the same default location/battery/safezone shape as
    ``make_watch_last_locate_payload`` so existing entity-value assertions stay valid after
    the coordinator's per-watch status fan-out collapsed into this single account-wide call.

    A ``WatchListItem`` is keyed by its own *device* id (here a distinct ``device-<wuid>``),
    and carries the *ward* id the integration looks watches up by only under ``user.id`` --
    mirroring real Xplora data. This guards the regression where indexing the list by the
    device id alone left every watch's battery/charging "unknown" (see ``getDeviceList``).
    """
    return {
        "deviceList": [
            {
                "id": f"device-{wuid}",
                "battery": battery,
                "onlineStatus": online_status,
                "unreadChatMessageCount": unread_chat_message_count,
                # Watch-model fields read straight from the deviceList item by `_setDevice`
                # (replacing the redundant per-watch `Watches`/`CheckWatchByQrCode` calls).
                "swKey": "imei-0001",
                "osVersion": "1.2.3",
                "groupName": "GPS-Watch",
                # The logged-in user's guardian relationship; the coordinator derives is_admin from
                # this (replacing the per-watch `Contacts`/`isAdmin` call).
                "guardianType": guardian_type,
                "stepsInfo": {"dailyStepsGoal": 10000, "todaysSteps": today_steps, "totalSteps": 56789},
                "user": {"id": wuid, "xcoin": 10},
                "location": {
                    "tm": 1700000000,
                    "lat": lat,
                    "lng": lng,
                    "rad": rad,
                    "poi": "Home",
                    "city": "Berlin",
                    "province": "Berlin",
                    "country": "Germany",
                    "locateType": "GPS",
                    "isInSafeZone": is_in_safe_zone,
                    "safeZoneLabel": "Home",
                    "isCharging": is_charging,
                    "step": 1234,
                    "distance": 42,
                },
            }
        ],
        "deviceFollowRequests": [],
    }


def make_loc_history_payload(points: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Response for the ``LocHistory`` query (the accumulated per-day track).

    `tm` is epoch seconds here (the coordinator normalizes to ms). Two points by default so a
    polyline has something to draw.
    """
    if points is None:
        points = [
            {
                "tm": 1700000000,
                "lat": "52.5200",
                "lng": "13.4050",
                "rad": 35,
                "city": "Berlin",
                "addr": "Teststrasse 1, Berlin",
                "poi": "Home",
                "locateType": "GPS",
            },
            {
                "tm": 1700003600,
                "lat": "52.5210",
                "lng": "13.4060",
                "rad": 40,
                "city": "Berlin",
                "addr": "Schulstrasse 4, Berlin",
                "poi": "School",
                "locateType": "WIFI",
            },
        ]
    return {"locHistory": {"offset": 0, "limit": len(points), "list": points}}


def make_track_watch_payload(interval: int = 10) -> dict[str, Any]:
    """Response for the ``TrackWatch`` query (used to derive online status)."""
    return {"trackWatch": interval}


def make_ask_watch_locate_payload(success: bool = True) -> dict[str, Any]:
    """Response for the ``AskWatchLocate`` query."""
    return {"askWatchLocate": success}


def make_alarms_payload() -> dict[str, Any]:
    """Response for the ``Alarms`` query."""
    return {
        "alarms": [
            {
                "id": "alarm-1",
                "vendorId": "vendor-alarm-1",
                "name": "Wake up",
                "occurMin": "420",
                "weekRepeat": "1111100",
                "status": "ENABLE",
            }
        ]
    }


def make_silent_times_payload() -> dict[str, Any]:
    """Response for the ``SlientTimes`` query."""
    return {
        "silentTimes": [
            {
                "id": "silent-1",
                "vendorId": "vendor-silent-1",
                "start": "480",
                "end": "900",
                "weekRepeat": "1111100",
                "status": "ENABLE",
            }
        ]
    }


def make_safe_zones_payload() -> dict[str, Any]:
    """Response for the ``SafeZones`` query."""
    return {
        "safeZones": [
            {
                "vendorId": "vendor-safezone-1",
                "groupName": "Home",
                "name": "Home",
                "lat": "52.5200",
                "lng": "13.4050",
                "rad": 100,
                "address": "Teststrasse 1, Berlin",
            }
        ]
    }


def make_chats_payload() -> dict[str, Any]:
    """Response for the ``Chats`` query."""
    return {
        "chatsNew": {
            "offset": 0,
            "limit": 10,
            "list": [],
        }
    }


def make_user_steps_payload(day: int = 1234) -> dict[str, Any]:
    """Response for the ``UserSteps`` query."""
    return {"userSteps": {"day": day}}


def make_unread_chat_msg_count_payload(count: int = 0) -> dict[str, Any]:
    """Response for the ``UnReadChatMsgCount`` query."""
    return {"unReadChatMsgCount": count}


def make_check_email_or_phone_exist_payload(exists: bool = True) -> dict[str, Any]:
    """Response for the ``CheckEmailOrPhoneExist`` query."""
    return {"checkEmailOrPhoneExist": exists}


def make_send_chat_text_payload(success: bool = True) -> dict[str, Any]:
    """Response for the ``SendChatText`` mutation."""
    return {"sendChatText": success}


def make_delete_chat_message_payload(success: bool = True) -> dict[str, Any]:
    """Response for the ``DeleteChatMessage`` mutation."""
    return {"deleteMsg": success}


def make_shutdown_payload(success: bool = True) -> dict[str, Any]:
    """Response for the ``ShutDown`` mutation (field matched case-insensitively to the op name)."""
    return {"ShutDown": success}


def make_reboot_payload(success: bool = True) -> dict[str, Any]:
    """Response for the ``reboot`` mutation (field matched case-insensitively to the op name)."""
    return {"reboot": success}


def make_expire_token_payload(token: str = "expired-token-1") -> dict[str, Any]:
    """Response for the ``ExpireToken`` mutation (server-side logout)."""
    return {"expireToken": {"token": token}}


def make_modify_alarm_payload(success: bool = True) -> dict[str, Any]:
    """Response for the ``ModifyAlarm`` mutation."""
    return {"modifyAlarm": success}


def make_set_enable_silent_time_payload(success: bool = True) -> dict[str, Any]:
    """Response for the ``SetEnableSlientTime`` mutation."""
    return {"setEnableSlientTime": success}


def make_fetch_chat_voice_payload() -> dict[str, Any]:
    return {"fetchChatVoice": "base64-voice-data"}


def make_fetch_chat_image_payload() -> dict[str, Any]:
    return {"fetchChatImage": "base64-image-data"}


def make_fetch_chat_short_video_payload() -> dict[str, Any]:
    return {"fetchChatShortVideo": "base64-video-data"}


def make_fetch_chat_short_video_cover_payload() -> dict[str, Any]:
    return {"fetchChatShortVideoCover": "base64-cover-data"}


# Full default "happy path" set covering every operationName the integration's call
# paths can trigger (login, isAdmin/Contacts, full setDevices() fan-out, messaging,
# alarms/silent times, and the shutdown/control mutations). Tests mutate a per-test
# copy of this (see the `graphql_operations` fixture in conftest.py) to exercise
# alternate/error branches.
DEFAULT_OPERATION_PAYLOADS: dict[str, dict[str, Any]] = {
    "signInWithEmailOrPhone": make_login_payload(),
    "Contacts": make_contacts_payload(),
    "Watches": make_watches_payload(),
    "CheckWatchByQrCode": make_check_watch_by_qrcode_payload(),
    "WatchLastLocate": make_watch_last_locate_payload(),
    "LocHistory": make_loc_history_payload(),
    "deviceList": make_device_list_payload(),
    "TrackWatch": make_track_watch_payload(),
    "AskWatchLocate": make_ask_watch_locate_payload(),
    "Alarms": make_alarms_payload(),
    "SlientTimes": make_silent_times_payload(),
    "SafeZones": make_safe_zones_payload(),
    "Chats": make_chats_payload(),
    "UnReadChatMsgCount": make_unread_chat_msg_count_payload(),
    "UserSteps": make_user_steps_payload(),
    "CheckEmailOrPhoneExist": make_check_email_or_phone_exist_payload(),
    "SendChatText": make_send_chat_text_payload(),
    "DeleteChatMessage": make_delete_chat_message_payload(),
    "ShutDown": make_shutdown_payload(),
    "reboot": make_reboot_payload(),
    "ExpireToken": make_expire_token_payload(),
    "ModifyAlarm": make_modify_alarm_payload(),
    "SetEnableSlientTime": make_set_enable_silent_time_payload(),
    "FetchChatVoice": make_fetch_chat_voice_payload(),
    "FetchChatImage": make_fetch_chat_image_payload(),
    "FetchChatShortVideo": make_fetch_chat_short_video_payload(),
    "FetchChatShortVideoCover": make_fetch_chat_short_video_cover_payload(),
}
