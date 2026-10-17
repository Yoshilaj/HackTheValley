"""Parkinson's screener (spec §5.1; StrivePD-style + prodromal items).

Yes/no, "in the last 3 months". Motor items = the first four (as listed in
the spec, which includes softer voice); the rest are non-motor/prodromal.
PD-01 fires on ≥2 motor items, or 1 motor + ≥1 non-motor item.

Validation status
-----------------
This questionnaire is a custom, StrivePD-style item list written for this app.
It is **not** a validated instrument: it is not the Tanner et al. (1990)
9-item PD screening questionnaire, not the Goetz / MDS-UPDRS Part I-II
self-report, and not the PD-NMS questionnaire, and no sensitivity /
specificity has been measured for it. The PD-01 cut-offs (≥2 motor, or 1 motor
+ ≥1 non-motor) come from spec §5.1 and are unvalidated conventions, not
published thresholds. Note also that "a fall or near-fall" is a sign of
established / later-stage PD (postural instability), not a prodromal marker in
the MDS research criteria for prodromal PD (Berg 2015 / Heinzel 2019); it is
kept as a general safety item. ``SCREENER_NOTE`` carries this caveat so API
responses and reports can display it next to any screener result.

Answers may arrive as booleans, 0/1 or strings ("yes"/"no", "true"/"false",
"1"/"0"); ``_yes()`` coerces them so a string ``"false"`` is never counted as a
"yes" (a plain truthiness check would).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, NamedTuple


@dataclass(frozen=True)
class ScreenerItem:
    id: str
    motor: bool
    en: str
    ja: str


PD_SCREENER_ITEMS: tuple[ScreenerItem, ...] = (
    ScreenerItem("tremor_rest", True, "New shaking of a hand while it is resting",
                 "安静にしているときに、手が新たにふるえるようになった"),
    ScreenerItem("stiffness_slowness", True, "Stiffness or slowness of movement",
                 "体のこわばりや、動作が遅くなったと感じる"),
    ScreenerItem("clumsiness_handwriting", True, "Clumsiness, or handwriting getting smaller",
                 "手先が不器用になった、または字が小さくなった"),
    ScreenerItem("soft_voice", True, "Voice has become softer or quieter",
                 "声が小さくなった、弱くなった"),
    ScreenerItem("smell_loss", False, "Reduced sense of smell",
                 "においが分かりにくくなった"),
    ScreenerItem("dream_enactment", False, "Acting out dreams or shouting during sleep",
                 "寝ている間に夢の内容に合わせて体が動いたり、大声を出したりする"),
    ScreenerItem("constipation", False, "Constipation", "便秘がある"),
    ScreenerItem("falls", False, "A fall or near-fall", "転倒した、または転びそうになった"),
)
# Item ids are the content-bank ids (app/content/screener_pd.json). Ids used by early builds are
# still accepted everywhere (stored ScreenerResponse rows, API clients) via LEGACY_ID_MAP.
LEGACY_ID_MAP: dict[str, str] = {
    "pd_rest_tremor": "tremor_rest",
    "pd_stiffness_slowness": "stiffness_slowness",
    "pd_clumsiness_handwriting": "clumsiness_handwriting",
    "pd_soft_voice": "soft_voice",
    "pd_reduced_smell": "smell_loss",
    "pd_dream_enactment": "dream_enactment",
    "pd_constipation": "constipation",
    "pd_fall": "falls",
}
PD_SCREENER_PERIOD_EN = "In the last 3 months, have you noticed:"
PD_SCREENER_PERIOD_JA = "この3か月間に、次のようなことはありましたか："
MOTOR_IDS = frozenset(i.id for i in PD_SCREENER_ITEMS if i.motor)
NONMOTOR_IDS = frozenset(i.id for i in PD_SCREENER_ITEMS if not i.motor)

# spec §5.1 PD-01
PD01_MIN_MOTOR = 2
PD01_MIN_MOTOR_WITH_NONMOTOR = 1
PD01_MIN_NONMOTOR = 1

SCREENER_NOTE = (
    "Custom StrivePD-style item list, not a validated instrument (not Tanner 1990, Goetz/MDS-UPDRS "
    "or PD-NMS); no sensitivity/specificity measured. PD-01 cut-offs (≥2 motor, or 1 motor + ≥1 "
    "non-motor) are from spec §5.1 and unvalidated. 'Falls' is a late PD sign, not a prodromal "
    "marker (MDS prodromal criteria). Not diagnostic."
)

_YES_STRINGS = frozenset({"yes", "y", "true", "t", "1", "on"})
_NO_STRINGS = frozenset({"no", "n", "false", "f", "0", "off", "", "none", "null"})


def _yes(v: Any) -> bool:
    """Coerce an answer to a yes/no bool: bool, 0/1, or "yes"/"no"/"true"/"false"/"1"/"0".

    Unknown strings are treated as "no" so a malformed answer never counts as a
    symptom. ``None`` is "no".
    """
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v == 1
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _YES_STRINGS:
            return True
        return False  # _NO_STRINGS and anything unrecognised
    return False


class ScreenerScore(NamedTuple):
    """``(motor_count, nonmotor_count, fired)``; compares equal to a plain 3-tuple."""

    motor_count: int
    nonmotor_count: int
    fired: bool

    @property
    def validation_note(self) -> str:
        return SCREENER_NOTE

    def to_dict(self) -> dict[str, Any]:
        """Result dict for API responses / reports, including ``validation_note``."""
        return {
            "motor_count": self.motor_count,
            "nonmotor_count": self.nonmotor_count,
            "pd01_would_fire": self.fired,
            "validation_note": SCREENER_NOTE,
        }


def score_pd_screener(answers: Mapping[str, Any]) -> ScreenerScore:
    """Return ``(motor_count, nonmotor_count, fired)``; legacy ids accepted, unknown keys ignored.

    The result is a :class:`ScreenerScore` (a NamedTuple, so tuple unpacking and
    ``== (m, n, fired)`` still work); ``.to_dict()`` adds ``validation_note``.
    """
    answers = {LEGACY_ID_MAP.get(k, k): _yes(v) for k, v in answers.items()}
    motor = sum(1 for k, v in answers.items() if v and k in MOTOR_IDS)
    nonmotor = sum(1 for k, v in answers.items() if v and k in NONMOTOR_IDS)
    fired = motor >= PD01_MIN_MOTOR or (
        motor >= PD01_MIN_MOTOR_WITH_NONMOTOR and nonmotor >= PD01_MIN_NONMOTOR
    )
    return ScreenerScore(motor, nonmotor, fired)
