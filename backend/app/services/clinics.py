"""Clinic seed list + distance helpers (Canada / Toronto).

    load_seed_clinics() -> list[Clinic]                        # data/seed/clinics_canada.json
    find_clinics(profile, "cardiology", radius_km=15) -> list[Clinic]   # nearest first (compat)

The booking agent v2 uses `app.services.booking.discovery.discover_clinics`, which wraps Places
Text Search and falls back to this seed list when no Maps key is configured. The seed clinics
are FICTIONAL (example.com e-mails, 555-01xx phone numbers, websites on the backend's own mock
clinic at `{BASE_URL}/mock-clinic…`).
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations import Clinic

log = get_logger(__name__)

SEED_PATH = Path(__file__).resolve().parents[2] / "data" / "seed" / "clinics_canada.json"
DEFAULT_TZ = "America/Toronto"

SPECIALTIES = ("cardiology", "neurology", "memory_clinic", "geriatrics", "family_medicine", "walk_in", "hospital")
#: pre-v2 names still accepted by find_clinics
_LEGACY_SPECIALTY = {"internal_medicine": "family_medicine", "psychiatry_memory": "memory_clinic"}
PATHWAY_SPECIALTY: dict[str, str] = {
    "cardio": "cardiology",
    "cognitive": "memory_clinic",
    "parkinsons": "neurology",
    "pd": "neurology",
    "general": "family_medicine",
}


@lru_cache(maxsize=1)
def _seed_raw() -> tuple[tuple[dict[str, Any], ...], str]:
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return tuple(data["clinics"]), data.get("timezone") or DEFAULT_TZ


def seed_website(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("{BASE_URL}", settings.base_url.rstrip("/"))


def load_seed_clinics() -> list[Clinic]:
    """Fresh Clinic objects for the fictional seed list (source='seed', fictional=True)."""
    raw, tz = _seed_raw()
    out = []
    for c in raw:
        d = dict(c)
        d["website"] = seed_website(d.get("website"))
        d.setdefault("timezone", tz)
        out.append(Clinic.from_dict(d | {"source": "seed", "fictional": True}))
    return out


def specialty_for_pathway(pathway: str) -> str:
    return PATHWAY_SPECIALTY.get(pathway, "family_medicine")


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def set_distances(clinics: list[Clinic], lat: float | None, lng: float | None) -> None:
    for c in clinics:
        c.distance_km = (haversine_km(lat, lng, c.lat, c.lng)
                         if None not in (lat, lng, c.lat, c.lng) else None)


def rank_by_distance(clinics: list[Clinic], lat: float | None, lng: float | None,
                     radius_km: float | None = None) -> list[Clinic]:
    """Set distance_km and sort nearest first; drop clinics beyond radius_km (if any remain).
    Without an origin the input order is kept."""
    if lat is None or lng is None:
        return list(clinics)
    set_distances(clinics, lat, lng)
    ranked = sorted(clinics, key=lambda c: (c.distance_km is None, c.distance_km or 0.0))
    if radius_km:
        inside = [c for c in ranked if c.distance_km is not None and c.distance_km <= radius_km]
        return inside or ranked  # never return nothing just because the radius is tight
    return ranked


def find_clinics(profile: Any, specialty: str, radius_km: float = 15.0, places: Any = None,
                 language: str = "en") -> list[Clinic]:
    """Clinics of `specialty` near the profile, nearest first (pre-v2 API; seed fallback)."""
    specialty = _LEGACY_SPECIALTY.get(specialty, specialty)
    lat = getattr(profile, "lat", None) if profile is not None else None
    lng = getattr(profile, "lng", None) if profile is not None else None
    if places is None:
        from app.integrations.google_places import PlacesClient, places_configured

        places = PlacesClient() if places_configured() else None
    if places is not None and lat is not None and lng is not None:
        from app.services.booking.discovery import query_for_specialty

        try:
            found = places.search_text(query_for_specialty(specialty), lat, lng, int(radius_km * 1000), specialty)
        except Exception as exc:  # network / quota: fall back to the seed list
            log.warning("places lookup failed; using seed clinics", extra={"error": str(exc)})
            found = []
        if found:
            return rank_by_distance(found, lat, lng, radius_km)
    seed = [c for c in load_seed_clinics() if c.specialty == specialty]
    return rank_by_distance(seed, lat, lng, radius_km)
