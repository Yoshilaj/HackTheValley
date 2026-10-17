"""Framingham Risk Score — 10-year general cardiovascular disease risk, as used in Canada.

Equation (verified 2026-10-17)
------------------------------
D'Agostino RB Sr, Vasan RS, Pencina MJ, Wolf PA, Cobain M, Massaro JM, Kannel WB.
"General cardiovascular risk profile for use in primary care: the Framingham Heart Study."
*Circulation* 2008;117(6):743-753. doi:10.1161/CIRCULATIONAHA.107.699579 (PMID 18212285).
Primary (lipid) model, sex-specific Cox model::

    risk_10y = 1 - S0(10) ** exp(sum(beta_i * x_i) - mean_sum)

Coefficients, S0(10) and the mean linear predictor below were checked against the Framingham
Heart Study's official risk-function page for this paper
(framinghamheartstudy.org/fhs-risk-functions/cardiovascular-disease-10-year-risk/, saved as
``data/raw/research/framingham_cvd_2008_fhs.html``, source id ``src_framingham2008``) and the
FHS calculator spreadsheet ``gencardio_lipids.xls`` (prepared by R.B. D'Agostino and
M.J. Pencina), whose worked values are reproduced in ``tests/test_framingham.py``.
Cholesterol enters the equation in **mg/dL** (as in the paper); the API also reports mmol/L.

"General CVD" = coronary death, MI, coronary insufficiency, angina, ischaemic/haemorrhagic
stroke, TIA, peripheral artery disease, heart failure. Derivation ages 30-74.

Canadian use (CCS 2021, source id ``src_ccs_lipids_2021``)
---------------------------------------------------------
Pearson GJ et al. "2021 Canadian Cardiovascular Society Guidelines for the Management of
Dyslipidemia for the Prevention of Cardiovascular Disease in Adults." *Can J Cardiol*
2021;37(8):1129-1150. doi:10.1016/j.cjca.2021.03.016. Verified from the CCS web edition:

* risk assessment with the FRS (or CLEM) every 5 years for men and women aged 40-75;
* "Among individuals 30-59 years of age without diabetes, the presence of a history of
  premature CV disease (CVD) in a first-degree relative (ie, 55 years or younger in male
  relatives and 65 years or younger in female relatives) increases an individual's calculated
  FRS percent risk by approximately twofold" → we double the percent in exactly that group
  ("modified FRS"); unknown family history is never assumed present;
* categories: low <10 %, intermediate 10-19.9 %, high >=20 %.

This is *context only* (FRS-01 is informational): it never changes the pathway level. The BP
input is the 7-day *home* systolic mean, used as-is (the paper used office SBP). Unknown inputs
are never imputed as normal.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from app.ml.context import ProfileIn

SOURCE_ID = "src_framingham2008"
CCS_SOURCE_ID = "src_ccs_lipids_2021"
CALCULATOR = "framingham_2008"
AGE_MIN, AGE_MAX = 30, 74                 # D'Agostino 2008 derivation range
CCS_FAMILY_HISTORY_AGE = (30, 59)         # CCS 2021: doubling applies to 30-59 without diabetes
#: mg/dL per mmol/L for cholesterol (molar mass 386.65 g/mol → 38.67)
CHOL_MG_DL_PER_MMOL_L = 38.67
CCS_INTERMEDIATE_PCT = 10.0
CCS_HIGH_PCT = 20.0
FRS_ELEVATED_PCT = CCS_INTERMEDIATE_PCT   # FRS-01 notes "intermediate or high" (CCS)


@dataclass(frozen=True)
class Coefficients:
    ln_age: float
    ln_total_chol: float
    ln_hdl: float
    ln_sbp_untreated: float
    ln_sbp_treated: float
    smoker: float
    diabetes: float
    s0_10y: float
    mean_sum: float


#: D'Agostino 2008 Table 2 / FHS risk-function page (primary lipid model).
COEFFICIENTS: dict[str, Coefficients] = {
    "female": Coefficients(ln_age=2.32888, ln_total_chol=1.20904, ln_hdl=-0.70833, ln_sbp_untreated=2.76157,
                           ln_sbp_treated=2.82263, smoker=0.52873, diabetes=0.69154,
                           s0_10y=0.95012, mean_sum=26.1931),
    "male": Coefficients(ln_age=3.06117, ln_total_chol=1.12370, ln_hdl=-0.93263, ln_sbp_untreated=1.93303,
                         ln_sbp_treated=1.99881, smoker=0.65451, diabetes=0.57367,
                         s0_10y=0.88936, mean_sum=23.9802),
}


def mg_dl_to_mmol_l(v: float) -> float:
    return v / CHOL_MG_DL_PER_MMOL_L


def mmol_l_to_mg_dl(v: float) -> float:
    return v * CHOL_MG_DL_PER_MMOL_L


def frs_risk(sex: str, age: float, total_chol_mg_dl: float, hdl_mg_dl: float, sbp: float,
             bp_treated: bool, smoker: bool, diabetes: bool) -> float:
    """Unmodified D'Agostino 2008 10-year general-CVD risk as a fraction (0-1)."""
    c = COEFFICIENTS[sex]
    s = (c.ln_age * math.log(age) + c.ln_total_chol * math.log(total_chol_mg_dl) + c.ln_hdl * math.log(hdl_mg_dl)
         + (c.ln_sbp_treated if bp_treated else c.ln_sbp_untreated) * math.log(sbp)
         + (c.smoker if smoker else 0.0) + (c.diabetes if diabetes else 0.0))
    return 1.0 - c.s0_10y ** math.exp(s - c.mean_sum)


def ccs_category(pct: float) -> Literal["low", "intermediate", "high"]:
    """CCS 2021 FRS categories: low <10 %, intermediate 10-19.9 %, high >=20 %."""
    if pct >= CCS_HIGH_PCT:
        return "high"
    return "intermediate" if pct >= CCS_INTERMEDIATE_PCT else "low"


def family_history_doubles(profile: ProfileIn) -> bool:
    """CCS 2021 modified FRS: x2 for a first-degree relative with premature CVD, age 30-59, no diabetes."""
    lo, hi = CCS_FAMILY_HISTORY_AGE
    return bool(profile.family_history_premature_cvd) and profile.age is not None and lo <= profile.age <= hi \
        and profile.diabetes is False


def format_pct(pct: float) -> str:
    return f"{pct:.1f}"


@dataclass(frozen=True)
class FraminghamResult:
    applicable: bool
    reason: str  # ok | not_applicable_age | not_applicable_known_chd | insufficient_data
    percent_10y: float | None = None          # CCS-modified (family history) percent, rounded to 0.1
    base_percent_10y: float | None = None     # unmodified D'Agostino 2008 percent
    category: str | None = None               # CCS low | intermediate | high
    family_history_doubled: bool = False
    inputs: dict[str, Any] = field(default_factory=dict)
    inputs_used: list[str] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)
    what_if: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.applicable:
            return "ok"
        return "insufficient_data" if self.reason == "insufficient_data" else "not_applicable"

    @property
    def display(self) -> str | None:
        return None if self.percent_10y is None else format_pct(self.percent_10y)


def _pct(profile: ProfileIn, sbp: float) -> tuple[float, float, bool]:
    base = 100.0 * frs_risk(profile.sex, profile.age, profile.total_chol, profile.hdl, sbp,  # type: ignore[arg-type]
                            bool(profile.bp_treated), bool(profile.smoker), bool(profile.diabetes))
    doubled = family_history_doubles(profile)
    return (min(base * 2.0, 100.0) if doubled else base), base, doubled


def framingham_score(profile: ProfileIn, systolic: float | None) -> FraminghamResult:
    """Compute the (CCS-modified) FRS. Eligibility: age 30-74, no known CHD/CVD.

    Required: age, sex, smoker, diabetes, 7-day home SBP mean, total cholesterol, HDL, and whether
    BP is treated. Missing → ``insufficient_data`` with ``missing_inputs``.
    """
    if profile.known_chd:
        return FraminghamResult(False, "not_applicable_known_chd")
    if profile.age is not None and not AGE_MIN <= profile.age <= AGE_MAX:
        return FraminghamResult(False, "not_applicable_age")
    missing: list[str] = [] if profile.age is not None else ["age"]
    if profile.sex not in ("male", "female"):
        missing.append("sex")
    if profile.smoker is None:
        missing.append("smoker")
    if profile.diabetes is None:
        missing.append("diabetes")
    if systolic is None:
        missing.append("bp_7day_mean")
    if profile.bp_treated is None:
        missing.append("bp_treated")
    if profile.total_chol is None:
        missing.append("total_chol")
    if profile.hdl is None:
        missing.append("hdl")
    if missing:
        return FraminghamResult(False, "insufficient_data", missing_inputs=missing)
    assert systolic is not None and profile.total_chol is not None and profile.hdl is not None

    pct, base, doubled = _pct(profile, systolic)
    notes = ["BP input is the 7-day home systolic mean, used as-is (the equation was derived on office SBP)."]
    if profile.family_history_premature_cvd is None:
        notes.append("Family history of premature CVD unknown — not applied (CCS doubles the risk when present).")
    if doubled:
        notes.append("Doubled for a first-degree relative with premature CVD (CCS 2021 modified FRS).")
    inputs = {
        "age": profile.age, "sex": profile.sex, "smoker": profile.smoker, "diabetes": profile.diabetes,
        "systolic_bp_mmhg": round(systolic, 1), "bp_treated": profile.bp_treated,
        "total_chol_mmol_l": round(mg_dl_to_mmol_l(profile.total_chol), 2),
        "hdl_mmol_l": round(mg_dl_to_mmol_l(profile.hdl), 2),
        "total_chol_mg_dl": profile.total_chol, "hdl_mg_dl": profile.hdl,
        "family_history_premature_cvd": profile.family_history_premature_cvd,
    }
    used = ["age", "sex", "smoker", "diabetes", "bp", "bp_treated", "total_chol", "hdl"]
    if profile.family_history_premature_cvd is not None:
        used.append("family_history_premature_cvd")

    scenarios: list[tuple[str, ProfileIn, float]] = []
    if profile.smoker:
        scenarios.append(("stop smoking", replace(profile, smoker=False), systolic))
    if systolic >= 120:
        scenarios.append(("systolic BP 120 mmHg", profile, 120.0))
    tc_target = mmol_l_to_mg_dl(5.2)
    if profile.total_chol > tc_target:
        scenarios.append(("total cholesterol 5.2 mmol/L", replace(profile, total_chol=tc_target), systolic))
    hdl_target = mmol_l_to_mg_dl(1.3)
    if profile.hdl < hdl_target:
        scenarios.append(("HDL cholesterol 1.3 mmol/L", replace(profile, hdl=hdl_target), systolic))
    what_if = []
    for change, p2, s2 in scenarios:
        wp, _, _ = _pct(p2, s2)
        what_if.append({"change": change, "percent_10y": round(wp, 1), "display": format_pct(wp),
                        "category": ccs_category(wp)})
    return FraminghamResult(
        True, "ok", percent_10y=round(pct, 1), base_percent_10y=round(base, 1), category=ccs_category(pct),
        family_history_doubled=doubled, inputs=inputs, inputs_used=used, what_if=what_if, notes=notes,
    )


PROBABILITY_NAME = "10-year cardiovascular disease risk (Framingham Risk Score)"


def framingham_probability_entry(res: FraminghamResult) -> dict[str, Any]:
    """Shape for ``PathwayResult.probabilities`` (same keys as the Suita entry + FRS extras)."""
    return {
        "name": PROBABILITY_NAME,
        "calculator": CALCULATOR,
        "kind": "population_estimate",
        "status": res.status,
        "reason": res.reason,
        "value_pct": res.percent_10y,
        "value_bound": None,
        "value_display": res.display,
        "base_value_pct": res.base_percent_10y,
        "category": res.category,
        "family_history_doubled": res.family_history_doubled,
        "inputs": dict(res.inputs),
        "inputs_used": list(res.inputs_used),
        "missing_inputs": list(res.missing_inputs),
        "what_if": [{"change": w["change"], "value_pct": w["percent_10y"], "value_display": w["display"],
                     "category": w["category"]} for w in res.what_if],
        "source_id": SOURCE_ID,
        "guideline_source_id": CCS_SOURCE_ID,
        "note": " ".join(res.notes) or "Framingham Heart Study cohort (US); used by the Canadian Cardiovascular Society.",
    }
