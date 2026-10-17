"""Parkinson's screener (spec §5.1; StrivePD-style + prodromal items).

Yes/no, "in the last 3 months". Motor items = the first four (as listed in
the spec, which includes softer voice); the rest are non-motor/prodromal.
PD-01 fires on ≥2 motor items, or 1 motor + ≥1 non-motor item.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


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


def score_pd_screener(answers: Mapping[str, bool]) -> tuple[int, int, bool]:
    """Return ``(motor_count, nonmotor_count, fired)``; legacy ids accepted, unknown keys ignored."""
    answers = {LEGACY_ID_MAP.get(k, k): v for k, v in answers.items()}
    motor = sum(1 for k, v in answers.items() if v and k in MOTOR_IDS)
    nonmotor = sum(1 for k, v in answers.items() if v and k in NONMOTOR_IDS)
    fired = motor >= PD01_MIN_MOTOR or (
        motor >= PD01_MIN_MOTOR_WITH_NONMOTOR and nonmotor >= PD01_MIN_NONMOTOR
    )
    return motor, nonmotor, fired
