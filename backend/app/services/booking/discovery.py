"""Clinic discovery near the patient (booking-v2 spec §1.2).

    result = discover_clinics(profile, "cardio", places=None, geocoder=None, limit=5)
    result.candidates      # ranked list[Clinic] (top `limit`), stored in Appointment.candidates
    result.origin          # (lat, lng) used, or None
    result.as_log()        # agent_log entry payload

Origin: `Profile.lat/lng`, else Google Geocoding of `Profile.address` / `postal_code` + province
(Canada). Search: Places API (New) Text Search, `regionCode=CA`, 15 km circular location bias.
Queries per pathway (`PATHWAY_QUERIES`): cognitive → memory clinic, neurologist, geriatrician;
cardio → cardiologist; parkinsons/pd → neurologist (movement disorders); general → family
doctor. Then fallbacks until there are enough candidates: family doctor, walk-in clinic, general
hospital. Without a Maps key (or when Places fails / returns nothing) the fictional Toronto seed
list is used with the same queries mapped to seed specialties.

Ranking (`rank_key`): specialty match (pathway query > family / walk-in > hospital), operational
(`businessStatus` OPERATIONAL or unknown; CLOSED_PERMANENTLY is dropped), distance (1 km buckets),
rating (higher first), has opening hours.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.integrations import Clinic
from app.services.clinics import load_seed_clinics, set_distances

log = get_logger(__name__)

DEFAULT_RADIUS_KM = 15.0
MAX_DISTANCE_KM = 100.0

PATHWAY_QUERIES: dict[str, list[tuple[str, str]]] = {
    "cognitive": [("memory clinic", "memory_clinic"), ("neurologist", "neurology"), ("geriatrician", "geriatrics")],
    "cardio": [("cardiologist", "cardiology")],
    "parkinsons": [("neurologist movement disorders", "neurology")],
    "pd": [("neurologist movement disorders", "neurology")],
    "general": [("family doctor", "family_medicine")],
}
FALLBACK_QUERIES: list[tuple[str, str]] = [
    ("family doctor", "family_medicine"), ("walk-in clinic", "walk_in"), ("general hospital", "hospital"),
]
FALLBACK_TIER = {"family_medicine": 1, "walk_in": 1, "hospital": 2}
REFERRAL_NOTE = ("In Canada, specialists usually need a referral from a family doctor or walk-in clinic. "
                 "The report link in the request supports that referral.")


def query_for_specialty(specialty: str) -> str:
    for queries in (*PATHWAY_QUERIES.values(), FALLBACK_QUERIES):
        for q, s in queries:
            if s == specialty:
                return q
    return "family doctor"


@dataclass
class DiscoveryResult:
    candidates: list[Clinic]
    origin: tuple[float, float] | None
    origin_source: str | None          # profile | geocoded | None
    source: str                        # google_places | seed
    queries: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_log(self) -> dict[str, Any]:
        return {"source": self.source, "origin_source": self.origin_source, "queries": self.queries,
                "notes": self.notes,
                "candidates": [{"id": c.id, "name": c.name, "specialty": c.specialty, "kind": c.kind,
                                "distance_km": round(c.distance_km, 2) if c.distance_km is not None else None,
                                "rating": c.rating, "specialty_match": c.specialty_match}
                               for c in self.candidates]}


def _operational(c: Clinic) -> bool:
    return c.business_status in (None, "", "OPERATIONAL")


def _tier(c: Clinic) -> int:
    return 0 if c.specialty_match else FALLBACK_TIER.get(c.specialty, 1)


def rank_key(c: Clinic) -> tuple:
    dist = c.distance_km if c.distance_km is not None else 1e9
    return (_tier(c), not _operational(c), round(dist), -(c.rating or 0.0), not bool(c.opening_hours), c.name)


def resolve_origin(profile: Any, geocoder: Any = None) -> tuple[tuple[float, float] | None, str | None, str | None]:
    """(origin, source, note). Uses profile lat/lng, else geocodes address / postal code."""
    lat = getattr(profile, "lat", None) if profile is not None else None
    lng = getattr(profile, "lng", None) if profile is not None else None
    if lat is not None and lng is not None:
        return (float(lat), float(lng)), "profile", None
    parts = [getattr(profile, k, None) for k in ("address", "postal_code", "province")] if profile else []
    query = ", ".join(str(p) for p in parts if p)
    if not query:
        return None, None, "no location in profile; candidates are unranked by distance"
    if geocoder is None:
        from app.integrations.google_places import get_geocoder

        geocoder = get_geocoder()
    try:
        found = geocoder.geocode(query + ", Canada")
    except Exception as exc:  # network / quota
        log.warning("geocoding failed", extra={"error": str(exc)})
        found = None
    if found is None:
        return None, None, "could not geocode the profile address"
    return (float(found[0]), float(found[1])), "geocoded", None


def _search(places: Any, query: str, specialty: str, origin, radius_km: float) -> list[Clinic]:
    lat, lng = origin if origin else (None, None)
    if hasattr(places, "search_text"):
        return places.search_text(query, lat, lng, int(radius_km * 1000), specialty)
    return places.nearby(lat, lng, query, int(radius_km * 1000), specialty)  # legacy fakes


def _seed_search(specialty: str) -> list[Clinic]:
    return [c for c in load_seed_clinics() if c.specialty == specialty]


def discover_clinics(profile: Any, pathway: str, *, places: Any = None, geocoder: Any = None,
                     limit: int = 5, radius_km: float = DEFAULT_RADIUS_KM) -> DiscoveryResult:
    """Ranked candidate clinics for `pathway` (see module docstring). Never raises for
    network problems: Places errors fall back to the seed list."""
    origin, origin_source, origin_note = resolve_origin(profile, geocoder)
    notes = [origin_note] if origin_note else []
    if places is None:
        from app.integrations.google_places import PlacesClient, places_configured

        places = PlacesClient() if places_configured() else None
    primary = PATHWAY_QUERIES.get(pathway, PATHWAY_QUERIES["general"])
    primary_specialties = {s for _, s in primary}
    fallbacks = [(q, s) for q, s in FALLBACK_QUERIES if s not in primary_specialties]
    queries_log: list[dict[str, Any]] = []
    found: dict[str, Clinic] = {}

    def run(queries: list[tuple[str, str]], is_primary: bool, search) -> None:
        for q, specialty in queries:
            if not is_primary and sum(1 for c in found.values() if _operational(c)) >= limit:
                break
            try:
                rows = search(q, specialty)
                queries_log.append({"query": q, "specialty": specialty, "results": len(rows)})
            except Exception as exc:
                queries_log.append({"query": q, "specialty": specialty, "error": f"{type(exc).__name__}: {exc}"[:200]})
                rows = []
            for c in rows:
                key = c.place_id or c.id
                if c.business_status == "CLOSED_PERMANENTLY" or key in found:
                    continue
                c.specialty_match = is_primary
                c.kind = c.kind or ("specialist" if is_primary and specialty not in FALLBACK_TIER else
                                    {"family_medicine": "family"}.get(specialty, specialty))
                found[key] = c

    source = "seed"
    if places is not None:
        search = lambda q, s: _search(places, q, s, origin, radius_km)  # noqa: E731
        run(primary, True, search)
        run(fallbacks, False, search)
        if found:
            source = "google_places" if not getattr(places, "is_fake", False) else "fake_places"
        else:
            notes.append("Places returned no usable results; using the fictional seed clinics")
    if not found:
        queries_log.append({"source": "seed"})
        run(primary, True, lambda q, s: _seed_search(s))
        run(fallbacks, False, lambda q, s: _seed_search(s))
        source = "seed"

    clinics = list(found.values())
    if origin:
        set_distances(clinics, *origin)
        near = [c for c in clinics if c.distance_km is not None and c.distance_km <= MAX_DISTANCE_KM]
        clinics = near or clinics
    clinics.sort(key=rank_key)
    if any(c.specialty_match and c.kind == "specialist" for c in clinics[:limit]):
        notes.append(REFERRAL_NOTE)
    return DiscoveryResult(candidates=clinics[:limit], origin=origin, origin_source=origin_source,
                           source=source, queries=queries_log, notes=notes)
