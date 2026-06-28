import base64
from enum import StrEnum
from typing import Final

# Bootstrap API_KEY/API_SECRET below are stored XOR+base64 encoded rather than as plain
# literals, so they don't show up verbatim in a source/code search. They ship with the
# app and are sent as-is in the request header (see HandlerGQL.getRequestHeaders), so this
# is not a secret in the security sense -- it only keeps the plaintext out of plain sight.
_OBFUSCATION_KEY = b"xplora-watch-bootstrap"


def _deobfuscate(encoded: str) -> str:
    raw = base64.b64decode(encoded)
    return bytes(b ^ _OBFUSCATION_KEY[i % len(_OBFUSCATION_KEY)] for i, b in enumerate(raw)).decode()


API_KEY = _deobfuscate("QUgKDkZQTxRURABQGQddCUxARxdSSUFHDQoWB0hGAhI=")
API_SECRET = _deobfuscate("ThRdXUsCG0JXEQVeGQAJXxYWFxZSEUtHXA0QBB1BWRA=")
ENDPOINT = "https://api.prod.myxplora.com/api/"
DEFAULT_TIMEOUT = 60
DEFAULT_USER_AGENT = "okhttp/5.3.2"
DEFAULT_ACCEPT_LANGUAGE = "en-GB"

# Token lifetime fallback used only when the server omits `expireDate` and no `E000004`
# arrives. The real server TTL is ~35 days; this just bounds the narrow corner case
# without recreating the per-poll-relogin churn a too-short fallback would cause.
DEFAULT_TOKEN_LIFETIME = 24 * 60 * 60
TOKEN_REFRESH_MARGIN = 60


class GqlOperation(StrEnum):
    """Canonical Xplora GraphQL `operationName`s.

    The single source of truth for the operation-name strings that are otherwise sprinkled as
    literals across the request layer and the tests (which route/count requests by operationName).
    Values must match the names embedded in the queries/mutations verbatim -- including the
    upstream `SlientTimes` typo -- so they line up with what the server (and the test transport)
    dispatches on. `StrEnum` members compare/hash as their string value, so they drop in anywhere a
    plain operationName string is expected.
    """

    DEVICE_LIST = "deviceList"
    WATCHES = "Watches"
    CHECK_WATCH_BY_QR_CODE = "CheckWatchByQrCode"
    ALARMS = "Alarms"
    SAFE_ZONES = "SafeZones"
    SILENT_TIMES = "SlientTimes"  # upstream operation name carries this typo
    ASK_WATCH_LOCATE = "AskWatchLocate"
    WATCH_LAST_LOCATE = "WatchLastLocate"
    CHATS = "Chats"
    CONTACTS = "Contacts"
    SIGN_IN = "signInWithEmailOrPhone"
    REFRESH_TOKEN = "RefreshToken"


# The slow-changing "functions" data, each fetched by its own per-watch query (`deviceList` cannot
# carry them). Grouped here so the coordinator's separate functions-poll interval and its tests
# refer to one list instead of repeating the three operation names.
FUNCTIONS_OPERATIONS: Final = (GqlOperation.ALARMS, GqlOperation.SAFE_ZONES, GqlOperation.SILENT_TIMES)


class WatchFunction(StrEnum):
    """The three slow-changing per-watch data sets the client can fetch independently.

    Each maps 1:1 to a `FUNCTIONS_OPERATIONS` GraphQL op and to one consuming entity, but they are
    gated *individually*: disabling a single consumer (e.g. the alarms sensor) suppresses only that
    one request instead of the whole group. The member *value* is the key under which the data is
    stored in `PyXploraApi.device[wuid]`, so `_setDevice` can carry an un-requested set forward by
    that key.
    """

    ALARMS = "getWatchAlarm"
    SAFE_ZONES = "getWatchSafeZones"
    SILENT_TIMES = "getSilentTime"


# Convenience set meaning "fetch everything" -- the default for an unconstrained fetch.
ALL_WATCH_FUNCTIONS: Final = frozenset(WatchFunction)
