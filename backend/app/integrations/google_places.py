"""Google Places API (New) Text Search + Geocoding API, plus offline fakes.

    places = get_places_client()                 # PlacesClient if GOOGLE_MAPS_API_KEY else FakePlaces
    clinics = places.search_text("cardiologist", lat, lng, radius_m=15000, specialty="cardiology")
    geo = get_geocoder()                         # Geocoder if GOOGLE_MAPS_API_KEY else FakeGeocoder
    latlng = geo.geocode("401 Bloor St W, Toronto, ON M5S 1X5")   # (lat, lng) | None

Text Search (POST https://places.googleapis.com/v1/places:searchText) is called with
`regionCode=CA`, `languageCode=en`, a circular `locationBias` (default 15 km) and a field mask
that includes `regularOpeningHours`, `websiteUri`, `nationalPhoneNumber`,
`internationalPhoneNumber`, `businessStatus`, `rating`, `location` and `utcOffsetMinutes`.
Places results never carry an e-mail address; the booking agent looks for one on the website
(app/services/booking/channels/detect.py).

`parse_opening_hours(regularOpeningHours)` turns Places `periods` (day 0 = Sunday) into the
booking agent's weekly format `{"mon": [["09:00", "17:00"]], ...}` (local time of the place).
Tests never touch the network: they monkeypatch `httpx.post` / `httpx.get` or use the fakes.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations import Clinic

log = get_logger(__name__)

SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
FIELD_MASK = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.location",
    "places.nationalPhoneNumber", "places.internationalPhoneNumber", "places.websiteUri",
    "places.regularOpeningHours", "places.businessStatus", "places.rating", "places.utcOffsetMinutes",
    "places.types", "places.primaryType",
])
WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
#: Places `day` (0 = Sunday) -> our weekday key
_PLACES_DAY = {0: "sun", 1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat"}


class Places(Protocol):
    is_fake: bool

    def search_text(self, query: str, lat: float | None, lng: float | None, radius_m: int = 15000,
                    specialty: str | None = None) -> list[Clinic]: ...


class Geocoder(Protocol):
    is_fake: bool

    def geocode(self, address: str) -> tuple[float, float] | None: ...


def _hm(point: dict[str, Any]) -> str:
    return f"{int(point.get('hour', 0)):02d}:{int(point.get('minute', 0)):02d}"


def parse_opening_hours(roh: dict[str, Any] | None) -> dict[str, list[list[str]]] | None:
    """Places `regularOpeningHours` -> {"mon": [["09:00","17:00"]], ...}; None when unknown.
    A period crossing midnight is split at 24:00; "always open" (one open period, no close) ->
    every day 00:00-24:00."""
    if not roh:
        return None
    periods = roh.get("periods") or []
    if not periods:
        return None
    out: dict[str, list[list[str]]] = {}
    if len(periods) == 1 and not periods[0].get("close"):
        return {k: [["00:00", "24:00"]] for k in WEEKDAY_KEYS}
    for p in periods:
        o, c = p.get("open") or {}, p.get("close") or {}
        if "day" not in o:
            continue
        day = _PLACES_DAY[int(o["day"]) % 7]
        start = _hm(o)
        if not c:
            out.setdefault(day, []).append([start, "24:00"])
            continue
        end = _hm(c)
        if int(c.get("day", o["day"])) != int(o["day"]):
            out.setdefault(day, []).append([start, "24:00"])
            if end != "00:00":
                out.setdefault(_PLACES_DAY[int(c["day"]) % 7], []).append(["00:00", end])
        else:
            out.setdefault(day, []).append([start, end])
    for v in out.values():
        v.sort()
    return out or None


def _kind_for(specialty: str | None) -> str:
    return {"family_medicine": "family", "walk_in": "walk_in", "hospital": "hospital"}.get(specialty or "", "specialist")


def to_clinic(p: dict[str, Any], specialty: str | None) -> Clinic:
    """Places result -> Clinic; `timezone` from the address province (checked against
    utcOffsetMinutes), app/integrations/timezones.py."""
    from app.integrations.timezones import infer_timezone

    loc = p.get("location") or {}
    roh = p.get("regularOpeningHours") or {}
    desc = roh.get("weekdayDescriptions")
    return Clinic(
        id=f"gp_{p.get('id')}",
        name=(p.get("displayName") or {}).get("text") or "Unknown clinic",
        specialty=specialty or "family_medicine",
        address=p.get("formattedAddress"),
        lat=loc.get("latitude"), lng=loc.get("longitude"),
        email=None,
        phone=p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber"),
        hours="; ".join(desc) if desc else None,
        source="google_places", place_id=p.get("id"),
        extra={k: v for k, v in {"international_phone": p.get("internationalPhoneNumber"),
                                 "utc_offset_minutes": p.get("utcOffsetMinutes"),
                                 "types": p.get("types")}.items() if v is not None},
        website=p.get("websiteUri"),
        opening_hours=parse_opening_hours(roh),
        kind=_kind_for(specialty),
        rating=p.get("rating"),
        business_status=p.get("businessStatus"),
        timezone=infer_timezone(p.get("formattedAddress"), p.get("utcOffsetMinutes"), p.get("addressComponents")),
    )


class PlacesClient:
    """Places API (New) Text Search with a circular location bias (httpx)."""

    is_fake = False

    def __init__(self, api_key: str | None = None, language: str = "en", region: str = "CA") -> None:
        self.api_key = api_key or settings.google_maps_api_key
        if not self.api_key:
            raise RuntimeError("GOOGLE_MAPS_API_KEY not configured")
        self.language = language if language and not language.startswith("ja") else "en"
        self.region = region

    def search_text(self, query, lat, lng, radius_m=15000, specialty=None) -> list[Clinic]:
        import httpx

        body: dict[str, Any] = {"textQuery": query, "languageCode": self.language, "regionCode": self.region,
                                "maxResultCount": 10}
        if lat is not None and lng is not None:
            body["locationBias"] = {"circle": {"center": {"latitude": lat, "longitude": lng},
                                               "radius": float(min(radius_m, 50000))}}
        headers = {"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": FIELD_MASK}
        resp = httpx.post(SEARCH_TEXT_URL, json=body, headers=headers, timeout=15)
        resp.raise_for_status()
        return [to_clinic(p, specialty) for p in resp.json().get("places", [])]

    def nearby(self, lat, lng, keyword, radius_m=15000, specialty=None) -> list[Clinic]:
        """Back-compat alias of search_text."""
        return self.search_text(keyword, lat, lng, radius_m, specialty)


class FakePlaces:
    """Returns `clinics` (default: the Toronto seed list) filtered by specialty; records calls."""

    is_fake = True

    def __init__(self, clinics: list[Clinic] | None = None) -> None:
        self._clinics = clinics
        self.calls: list[dict[str, Any]] = []

    def search_text(self, query, lat, lng, radius_m=15000, specialty=None) -> list[Clinic]:
        from app.services.clinics import load_seed_clinics

        self.calls.append({"lat": lat, "lng": lng, "query": query, "radius_m": radius_m, "specialty": specialty})
        pool = self._clinics if self._clinics is not None else load_seed_clinics()
        out = [Clinic.from_dict(c.as_dict()) for c in pool if specialty is None or c.specialty == specialty]
        for c in out:
            c.source = "fake" if self._clinics is not None else c.source
        return out

    def nearby(self, lat, lng, keyword, radius_m=15000, specialty=None) -> list[Clinic]:
        return self.search_text(keyword, lat, lng, radius_m, specialty)


class GeocodingClient:
    """Google Geocoding API (address / postal code -> lat, lng), restricted to Canada."""

    is_fake = False

    def __init__(self, api_key: str | None = None, region: str = "ca") -> None:
        self.api_key = api_key or settings.google_maps_api_key
        if not self.api_key:
            raise RuntimeError("GOOGLE_MAPS_API_KEY not configured")
        self.region = region

    def geocode(self, address: str) -> tuple[float, float] | None:
        import httpx

        if not address or not address.strip():
            return None
        params = {"address": address, "region": self.region, "components": "country:CA", "key": self.api_key}
        resp = httpx.get(GEOCODE_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if data.get("status") not in (None, "OK") or not results:
            return None
        loc = (results[0].get("geometry") or {}).get("location") or {}
        if "lat" not in loc or "lng" not in loc:
            return None
        return float(loc["lat"]), float(loc["lng"])


class FakeGeocoder:
    """Offline geocoder: `table` maps address substrings (case-insensitive) or postal-code
    prefixes to coordinates; default = downtown Toronto for any Toronto / 'M' postal code."""

    is_fake = True
    TORONTO = (43.6532, -79.3832)

    def __init__(self, table: dict[str, tuple[float, float]] | None = None) -> None:
        self.table = {k.lower(): v for k, v in (table or {}).items()}
        self.calls: list[str] = []

    def geocode(self, address: str) -> tuple[float, float] | None:
        self.calls.append(address)
        a = (address or "").lower()
        for key, value in self.table.items():
            if key in a:
                return value
        if "toronto" in a or a.strip().startswith("m"):
            return self.TORONTO
        return None


def places_configured() -> bool:
    return bool(settings.google_maps_api_key)


def get_places_client(language: str = "en") -> Places:
    """PlacesClient when GOOGLE_MAPS_API_KEY is set, else FakePlaces (seed list)."""
    return PlacesClient(language=language) if places_configured() else FakePlaces()


def get_geocoder() -> Geocoder:
    return GeocodingClient() if places_configured() else FakeGeocoder()
