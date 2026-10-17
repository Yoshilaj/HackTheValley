"""Suita score — 10-year coronary heart disease risk (spec §3).

Source: Nishimura et al., J Atheroscler Thromb 2014;21:784-798 (Suita study,
5,521 Japanese urban adults without CHD). Two score sheets:

* Table 5b "LDL Suita score with CKD" (preferred, needs LDL-C and HDL-C)
* Table 5a "TC Suita score" (used when only total cholesterol is known)

This is *context only*: it never drives the pathway level (SUITA-01 is
informational). Unknown inputs are never imputed as normal.

Gap decisions (the spec tables have holes; documented here):
* TC sheet age bands start at 36; age 35 is scored in the first band (30).
* TC sheet HDL "<35 = 6, 36–49 = 0": HDL in [35, 36) is scored 0.
* eGFR exactly 60 → stage 3 (spec "Stage 3 (eGFR 30–60)"; ">60" is normal).
* If eGFR is unknown but ``ckd_stage`` is recorded, the stage is used
  (≤2 → 0 points, 3 → stage-3 points, ≥4 → stage-4/5 points).
* BP input is the 7-day *home* mean fed as-is (spec §3 note).

Input plausibility: lipids are gated to the same mg/dL ranges as the Framingham module (TC 100–405,
HDL 20–100, LDL 30–300) and eGFR to (0, 200]. A value typed in mmol/L (e.g. LDL 3.9) would
otherwise silently land in the lowest band; out of range → ``not_applicable`` with the field
named in ``reason``/``notes``. Nothing is auto-converted.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

from app.ml.context import ProfileIn
from app.ml.framingham import (
    EGFR_MAX, EGFR_MIN_EXCLUSIVE, HDL_MAX, HDL_MIN, IMPLAUSIBLE_INPUT, LDL_MAX, LDL_MIN, TC_MAX, TC_MIN,
    implausible_inputs,
)

SOURCE_ID = "src_suita2014"
AGE_MIN, AGE_MAX = 35, 79
SUITA_ELEVATED_PCT = 9.0  # spec §3 SUITA-01: ≥9% → "elevated long-term risk"

# (upper bound inclusive of total points, probability %, bound marker)
LDL_PROB_TABLE: list[tuple[float, float, str | None]] = [
    (35, 1.0, "<"), (40, 1.0, None), (45, 2.0, None), (50, 3.0, None),
    (55, 5.0, None), (60, 9.0, None), (65, 14.0, None), (70, 22.0, None),
    (float("inf"), 28.0, ">"),
]
TC_PROB_TABLE: list[tuple[float, float, str | None]] = [
    (30, 1.0, "<"), (35, 1.0, None), (40, 2.0, None), (45, 4.0, None),
    (50, 6.0, None), (55, 10.0, None), (60, 15.0, None), (float("inf"), 24.0, None),
]


def bp_category(systolic: float, diastolic: float) -> str:
    """Suita BP category; the higher of the systolic/diastolic category wins."""

    def cat(s_cut: list[float], v: float) -> int:
        return sum(1 for c in s_cut if v >= c)

    idx = max(cat([120, 140, 160], systolic), cat([80, 90, 100], diastolic))
    return ["optimal", "normal_high_normal", "stage1", "stage2"][idx]


_BP_POINTS = {
    "ldl": {"optimal": -7, "normal_high_normal": 0, "stage1": 4, "stage2": 6},
    "tc": {"optimal": -7, "normal_high_normal": 0, "stage1": 3, "stage2": 6},
}


def _ckd_points(sheet: str, egfr: float | None, ckd_stage: int | None) -> int:
    s3, s45 = (3, 14) if sheet == "ldl" else (3, 15)
    if egfr is not None:
        if egfr > 60:
            return 0
        return s3 if egfr >= 30 else s45
    assert ckd_stage is not None
    if ckd_stage <= 2:
        return 0
    return s3 if ckd_stage == 3 else s45


def ldl_points(
    age: int, female: bool, smoker: bool, diabetes: bool, systolic: float,
    diastolic: float, ldl: float, hdl: float, egfr: float | None, ckd_stage: int | None = None,
) -> int:
    """Table 5b (LDL Suita score with CKD) total points."""
    p = 30 if age < 45 else 38 if age < 55 else 45 if age < 65 else 51 if age < 70 else 53
    p += -7 if female else 0
    p += 5 if smoker else 0
    p += 6 if diabetes else 0
    p += _BP_POINTS["ldl"][bp_category(systolic, diastolic)]
    p += 0 if ldl < 100 else 5 if ldl < 140 else 7 if ldl < 160 else 10 if ldl < 180 else 11
    p += 0 if hdl < 40 else -5 if hdl < 60 else -6
    p += _ckd_points("ldl", egfr, ckd_stage)
    return p


def tc_points(
    age: int, female: bool, smoker: bool, diabetes: bool, systolic: float,
    diastolic: float, total_chol: float, hdl: float, egfr: float | None, ckd_stage: int | None = None,
) -> int:
    """Table 5a (TC Suita score) total points."""
    p = 30 if age <= 45 else 39 if age <= 55 else 46 if age <= 65 else 58
    p += -6 if female else 0
    p += 5 if smoker else 0
    p += 5 if diabetes else 0
    p += _BP_POINTS["tc"][bp_category(systolic, diastolic)]
    p += -11 if total_chol < 160 else 0 if total_chol < 240 else 5 if total_chol < 280 else 9
    p += 6 if hdl < 35 else 0 if hdl < 50 else -5
    p += _ckd_points("tc", egfr, ckd_stage)
    return p


def points_to_probability(sheet: str, points: int) -> tuple[float, str | None]:
    """Map total points to (10-year CHD %, bound) — bound '<', '>' or None."""
    table = LDL_PROB_TABLE if sheet == "ldl" else TC_PROB_TABLE
    for upper, pct, bound in table:
        if points <= upper:
            return pct, bound
    raise AssertionError("unreachable")


def format_probability(pct: float, bound: str | None) -> str:
    return f"{bound or ''}{pct:g}"


@dataclass(frozen=True)
class SuitaResult:
    applicable: bool
    #: ok | not_applicable_age | not_applicable_known_chd | insufficient_data |
    #: "not_applicable_implausible_input: <field>=<value> ...; expected <range>" (one clause per field)
    reason: str
    sheet: Literal["ldl", "tc"] | None = None
    points: int | None = None
    probability_pct: float | None = None
    probability_bound: str | None = None  # '<' or '>' when the table is open-ended
    probability_display: str | None = None
    inputs_used: list[str] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)
    what_if: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.applicable:
            return "ok"
        return "insufficient_data" if self.reason == "insufficient_data" else "not_applicable"


def _score(profile: ProfileIn, sheet: str, sys_: float, dia: float) -> int:
    common = dict(
        age=profile.age, female=profile.sex == "female", smoker=bool(profile.smoker),
        diabetes=bool(profile.diabetes), systolic=sys_, diastolic=dia, hdl=profile.hdl,
        egfr=profile.egfr, ckd_stage=profile.ckd_stage,
    )
    if sheet == "ldl":
        return ldl_points(ldl=profile.ldl, **common)  # type: ignore[arg-type]
    return tc_points(total_chol=profile.total_chol, **common)  # type: ignore[arg-type]


def suita_score(profile: ProfileIn, systolic: float | None, diastolic: float | None) -> SuitaResult:
    """Compute the Suita score (spec §3, §11.2).

    Eligibility: age 35–79 and no known CHD. Sheet: LDL when LDL+HDL known,
    else TC when TC+HDL known. Missing required inputs → ``insufficient_data``
    with ``missing_inputs``; implausible lipids/eGFR → ``not_applicable`` (see
    module docstring). What-if scenarios are recomputed independently.
    """
    if profile.known_chd:
        return SuitaResult(False, "not_applicable_known_chd")
    if profile.age is not None and not AGE_MIN <= profile.age <= AGE_MAX:
        return SuitaResult(False, "not_applicable_age")

    missing: list[str] = [] if profile.age is not None else ["age"]
    if profile.sex not in ("male", "female"):
        missing.append("sex")
    if profile.smoker is None:
        missing.append("smoker")
    if profile.diabetes is None:
        missing.append("diabetes")
    if systolic is None or diastolic is None:
        missing.append("bp_7day_mean")
    if profile.egfr is None and profile.ckd_stage is None:
        missing.append("egfr")
    sheet: Literal["ldl", "tc"] | None
    if profile.ldl is not None and profile.hdl is not None:
        sheet = "ldl"
    elif profile.total_chol is not None and profile.hdl is not None:
        sheet = "tc"
    else:
        sheet = None
        if profile.hdl is None:
            missing.append("hdl")
        if profile.ldl is None:
            missing.append("ldl")
            if profile.total_chol is None:
                missing.append("total_chol")
    if missing:
        return SuitaResult(False, "insufficient_data", sheet=sheet, missing_inputs=missing)
    assert sheet is not None and systolic is not None and diastolic is not None
    bad = implausible_inputs(
        ldl_mg_dl=(profile.ldl if sheet == "ldl" else None, LDL_MIN, LDL_MAX),
        total_chol_mg_dl=(profile.total_chol if sheet == "tc" else None, TC_MIN, TC_MAX),
        hdl_mg_dl=(profile.hdl, HDL_MIN, HDL_MAX),
        egfr=(profile.egfr, EGFR_MIN_EXCLUSIVE, EGFR_MAX, "mL/min/1.73m²", True),
    )
    if bad:
        return SuitaResult(False, f"{IMPLAUSIBLE_INPUT}: {'; '.join(bad)}", sheet=sheet, notes=list(bad))

    pts = _score(profile, sheet, systolic, diastolic)
    pct, bound = points_to_probability(sheet, pts)
    used = ["age", "sex", "smoker", "diabetes", "bp", "ldl" if sheet == "ldl" else "total_chol", "hdl",
            "egfr" if profile.egfr is not None else "ckd_stage"]

    scenarios: list[tuple[str, ProfileIn, float, float]] = []
    if profile.smoker:
        scenarios.append(("stop smoking", replace(profile, smoker=False), systolic, diastolic))
    if bp_category(systolic, diastolic) != "optimal":
        scenarios.append(("BP < 120/80", profile, 119.0, 79.0))
    if sheet == "ldl" and profile.ldl is not None and profile.ldl >= 100:
        scenarios.append(("LDL < 100 mg/dL", replace(profile, ldl=99.0), systolic, diastolic))
    if sheet == "tc" and profile.total_chol is not None and profile.total_chol >= 160:
        scenarios.append(("total cholesterol < 160 mg/dL", replace(profile, total_chol=159.0), systolic, diastolic))
    if profile.hdl is not None and profile.hdl < 60:
        scenarios.append(("HDL ≥ 60 mg/dL", replace(profile, hdl=60.0), systolic, diastolic))
    what_if = []
    for change, p2, s2, d2 in scenarios:
        wp = _score(p2, sheet, s2, d2)
        wpct, wb = points_to_probability(sheet, wp)
        what_if.append({"change": change, "points": wp, "probability_pct": wpct,
                        "probability_bound": wb, "probability_display": format_probability(wpct, wb)})
    return SuitaResult(
        True, "ok", sheet=sheet, points=pts, probability_pct=pct, probability_bound=bound,
        probability_display=format_probability(pct, bound), inputs_used=used, what_if=what_if,
    )


def suita_probability_entry(res: SuitaResult) -> dict[str, Any]:
    """Shape for ``PathwayResult.probabilities`` / API §11.4."""
    return {
        "name": "10-year coronary heart disease risk (Suita score)",
        "kind": "population_estimate",
        "status": res.status,
        "reason": res.reason,
        "sheet": res.sheet,
        "points": res.points,
        "value_pct": res.probability_pct,
        "value_bound": res.probability_bound,
        "value_display": res.probability_display,
        "inputs_used": list(res.inputs_used),
        "missing_inputs": list(res.missing_inputs),
        "what_if": [{"change": w["change"], "value_pct": w["probability_pct"],
                     "value_display": w["probability_display"]} for w in res.what_if],
        "source_id": SOURCE_ID,
        "note": " ".join(res.notes)
                or "BP input is the 7-day home mean, used as-is; Japanese urban cohort (baseline 1989–1994).",
    }
