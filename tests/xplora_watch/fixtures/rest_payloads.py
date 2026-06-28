"""Canned REST response bodies for the non-GraphQL HTTP calls xplora_watch makes.

Covers reverse-geocoding (openstreetmap.org / opencagedata.com / api.mapbox.com,
see ``coordinator.py``'s ``openstreetmap``/``opencagedata``/``mapbox`` methods) and the
watch ``entity_picture`` reachability check in ``device_tracker.py``.
"""

from __future__ import annotations

from typing import Any

OPENSTREETMAP_REVERSE_GEOCODE: dict[str, Any] = {
    "display_name": "Teststrasse 1, 12345 Berlin, Germany",
    "address": {"road": "Teststrasse", "city": "Berlin"},
    "licence": "Data © OpenStreetMap contributors",
}

OPENCAGEDATA_REVERSE_GEOCODE: dict[str, Any] = {
    "results": [{"formatted": "Teststrasse 1, Berlin, Germany"}],
    "licenses": [{"url": "https://opencagedata.com/credits"}],
}

MAPBOX_REVERSE_GEOCODE: dict[str, Any] = {
    "features": [{"place_name": "Teststrasse 1, Berlin, Germany"}],
    "attribution": "© Mapbox",
}

ENTITY_PICTURE_BODY: bytes = b"\x89PNG\r\n\x1a\n"
