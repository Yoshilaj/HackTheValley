"""Device rhythm results (spec §4): label normalisation and summary.

Enum: sinus_rhythm | possible_af | afib | high_hr | low_hr | inconclusive |
poor_recording | irregular_rhythm_notification | other.
Sources: apple_irn | apple_ecg | omron_connect | manual_irregular_pulse.

The raw label string is always stored by the DB layer; this module only maps.
Decision: a manual irregular-pulse indicator (source
``manual_irregular_pulse``) maps to ``irregular_rhythm_notification`` — an
irregular pulse is a "talk to your doctor / get an ECG" signal like Apple IRN.
The BP-cuff ``irregular_pulse_flag`` on ``BpReadingIn`` is *not* mapped here
(it is reported as data quality only), to avoid double-counting cuffs that
also emit an OMRON connect result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from app.ml.context import ProfileIn, RhythmResultIn

LABELS = (
    "sinus_rhythm", "possible_af", "afib", "high_hr", "low_hr", "inconclusive",
    "poor_recording", "irregular_rhythm_notification", "other",
)
AF_LABELS = frozenset({"possible_af", "afib", "irregular_rhythm_notification"})
UNCLASSIFIED_LABELS = frozenset({"inconclusive", "poor_recording"})
HR_LABELS = frozenset({"high_hr", "low_hr"})
CLASSIFIED_LABELS = frozenset(LABELS) - UNCLASSIFIED_LABELS - {"other"}

_APPLE_ECG = {
    "sinus rhythm": "sinus_rhythm",
    "atrial fibrillation": "afib",
    "afib": "afib",
    "high heart rate": "high_hr",
    "high hr": "high_hr",
    "low heart rate": "low_hr",
    "low hr": "low_hr",
    "inconclusive": "inconclusive",
    "poor recording": "poor_recording",
    "unrecognized": "inconclusive",
    # Apple Health export HKElectrocardiogram classification identifiers
    "hkelectrocardiogramclassificationsinusrhythm": "sinus_rhythm",
    "hkelectrocardiogramclassificationatrialfibrillation": "afib",
    "hkelectrocardiogramclassificationinconclusivehighheartrate": "high_hr",
    "hkelectrocardiogramclassificationinconclusivelowheartrate": "low_hr",
    "hkelectrocardiogramclassificationinconclusiveother": "inconclusive",
    "hkelectrocardiogramclassificationinconclusivepoorreading": "poor_recording",
    "hkelectrocardiogramclassificationunrecognized": "inconclusive",
}
_OMRON = {
    "心房細動の可能性": "possible_af",
    "possible afib": "possible_af",
    "possible atrial fibrillation": "possible_af",
    # The other five OMRON connect patterns are added once the PDF is seen.
}


def normalize_label(source: str, raw: str) -> str:
    """Map a raw device label to the spec §4 enum (unknown → ``other``)."""
    key = (raw or "").strip()
    if key in LABELS:
        return key
    low = key.lower()
    if source == "apple_irn":
        return "irregular_rhythm_notification"
    if source == "manual_irregular_pulse":
        return "irregular_rhythm_notification"
    if source == "omron_connect":
        return _OMRON.get(key) or _OMRON.get(low) or "other"
    if source == "apple_ecg":
        return _APPLE_ECG.get(low, "other")
    return _APPLE_ECG.get(low) or _OMRON.get(key) or "other"


@dataclass(frozen=True)
class RhythmFinding:
    result: RhythmResultIn
    label: str

    @property
    def ref(self) -> str:
        return self.result.id or f"drr_{self.result.source}_{self.result.recorded_at.isoformat()}"


@dataclass(frozen=True)
class RhythmFindings:
    """Normalised results within the look-back window, newest first."""

    findings: list[RhythmFinding] = field(default_factory=list)
    known_af: bool = False

    def with_labels(self, labels: Iterable[str], since: datetime | None = None) -> list[RhythmFinding]:
        ls = set(labels)
        return [f for f in self.findings if f.label in ls and (since is None or f.result.recorded_at >= since)]


def rhythm_findings(
    results: Iterable[RhythmResultIn], profile: ProfileIn, as_of: datetime, lookback_days: int = 30
) -> RhythmFindings:
    """Normalise results recorded in ``(as_of - lookback_days, as_of]``."""
    since = as_of - timedelta(days=lookback_days)
    fs = [
        RhythmFinding(r, normalize_label(r.source, r.label))
        for r in results
        if since < r.recorded_at <= as_of
    ]
    fs.sort(key=lambda f: f.result.recorded_at, reverse=True)
    return RhythmFindings(fs, known_af=profile.known_af)
