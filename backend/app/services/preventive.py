"""Preventive measures per pathway (spec phase-a §11.3).

Deterministic, 2–4 items, each tied to a fired rule's modifiable input (or to
the pathway in general) and to a ResearchSource id from
``data/raw/research/sources.json``. Every item is framed "to discuss with your
doctor". Cognitive and Parkinson's items are general brain-health guidance and
are explicitly NOT framed as lowering a disease percentage.
"""
from __future__ import annotations

from typing import Any, Iterable

from app.ml.context import ProfileIn

MIN_ITEMS, MAX_ITEMS = 2, 4

SRC_HTN_CA = "src_hypertension_canada_2020"   # primary BP source (Canada)
SRC_JSH = "src_jsh2019"                       # kept as secondary BP source
SRC_SUITA = "src_suita2014"                   # Japan only
SRC_FRS = "src_framingham2008"
SRC_CCS_LIPIDS = "src_ccs_lipids_2021"
SRC_CCS_AF = "src_ccs_af_2020"
SRC_STORY = "src_storyteller_adni4_2024"
SRC_STRIVEPD = "src_strivepd_screener"
SRC_HEARO = "src_cordio_hearo"
SRC_OMRON_AF = "src_omron_af_study_2024"

_DISCUSS_EN = " (to discuss with your doctor)"
_DISCUSS_JA = "（医師と相談しましょう）"


def _m(en: str, ja: str, source_id: str, related_rule: str | None) -> dict[str, Any]:
    return {"text_en": en + _DISCUSS_EN, "text_ja": ja + _DISCUSS_JA, "source_id": source_id, "related_rule": related_rule}


# ---- catalogue (ordered by priority within each group). BP items follow the Hypertension Canada
# 2020-22 health-behaviour recommendations (Highlights guide, data/raw/research).
_BP = [
    ("salt", "Eat less salt and follow a DASH-style diet (more vegetables, fruit and fibre, less saturated fat)",
     "塩分を控え、DASH食（野菜・果物・食物繊維を多く、飽和脂肪を少なく）を心がける"),
    ("alcohol", "Limit alcohol to fewer than 2 drinks a day, or avoid it", "お酒は1日2杯未満にするか、控える"),
    ("exercise", "Build up 30-60 minutes of moderate activity, such as brisk walking or cycling, 4-7 days a week",
     "早歩きやサイクリングなど中程度の運動を、週4〜7日、30〜60分続ける"),
    ("weight", "Keep a healthy weight and waist size", "適正体重・腹囲を保つ"),
]
_BP_MEDICATION = ("Keep taking blood-pressure medication at the same time daily",
                  "血圧の薬を毎日同じ時間に飲み続ける")
_RHYTHM = ("Bring your device's rhythm recordings to the visit", "心電図・脈の記録を受診時に持参する")
_CHD_SMOKING = ("Stopping smoking lowers long-term heart risk", "禁煙すると長期的な心臓病のリスクが下がる")
_CHD_LIPIDS = ("Ask about checking and managing cholesterol (total, LDL and HDL)", "コレステロール（LDL・HDL）の検査と管理について相談する")
_CHD_DIABETES = ("Keep diabetes well controlled", "糖尿病の管理を続ける")
_SLEEP = [
    ("Keep a regular sleep schedule", "毎日同じ時間に寝起きする"),
    ("Stay active during the day, for example a daily walk", "日中に体を動かす（毎日の散歩など）"),
]
_BRAIN = [
    ("Regular physical exercise supports general brain health", "定期的な運動は脳の健康全般に役立つ"),
    ("Have your hearing and vision checked", "聴力と視力の検査を受ける"),
    ("Stay socially active", "人との交流を続ける"),
    ("Keep good sleep habits", "よい睡眠習慣を保つ"),
]
_BRAIN_NOTE_EN = " — general brain-health guidance, not a way to lower a disease percentage"
_BRAIN_NOTE_JA = "（脳の健康全般のための一般的な助言で、病気の確率を下げるものではありません）"

BP_RULES = ("BP-01", "BP-02", "BP-03", "BP-04", "BP-05")


def _cardio(fired: list[str], profile: ProfileIn | None, probabilities: list[dict[str, Any]],
            medications: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    bp_rule = next((r for r in fired if r in BP_RULES), None)
    if bp_rule:
        if medications:
            items.append(_m(*_BP_MEDICATION, SRC_HTN_CA, bp_rule))
        for key, en, ja in _BP:
            if key == "weight":
                continue
            items.append(_m(en, ja, SRC_HTN_CA, bp_rule))
    if "RHY-01" in fired or "RHY-03" in fired:
        items.insert(1 if items else 0, _m(*_RHYTHM, SRC_CCS_AF, "RHY-01" if "RHY-01" in fired else "RHY-03"))
    # Long-term risk (Framingham/CCS in Canada; Suita in Japan) — modifiable inputs of the estimate.
    suita = next((p for p in probabilities if p.get("source_id") == SRC_SUITA), None)
    if suita is not None:
        lt_src, lt_rule, lipid_missing = SRC_SUITA, "SUITA-01", "ldl" in (suita.get("missing_inputs") or [])
    else:
        frs = next((p for p in probabilities if p.get("source_id") == SRC_FRS), None)
        missing = (frs or {}).get("missing_inputs") or []
        lt_src, lt_rule, lipid_missing = SRC_CCS_LIPIDS, "FRS-01", "total_chol" in missing or "hdl" in missing
    chd: list[dict[str, Any]] = []
    if profile is not None:
        if profile.smoker:
            chd.append(_m(*_CHD_SMOKING, lt_src, lt_rule))
        # 140 mg/dL LDL ≈ 3.6 mmol/L; HDL 40 mg/dL ≈ 1.0 mmol/L
        if (profile.ldl is not None and profile.ldl >= 140) or (profile.hdl is not None and profile.hdl < 40) \
                or lipid_missing:
            chd.append(_m(*_CHD_LIPIDS, lt_src, lt_rule))
        if profile.diabetes:
            chd.append(_m(*_CHD_DIABETES, lt_src, lt_rule))
    # Interleave so the CHD items are not crowded out by BP items.
    if chd:
        head = items[:2] if bp_rule else items
        items = head + chd + [i for i in items if i not in head]
    if len(items) < MIN_ITEMS:
        for key, en, ja in _BP:
            cand = _m(en, ja, SRC_HTN_CA, None)
            if all(c["text_en"] != cand["text_en"] for c in items):
                items.append(cand)
            if len(items) >= MIN_ITEMS:
                break
    return items


def _brain(pathway: str, fired: list[str]) -> list[dict[str, Any]]:
    src = SRC_STORY if pathway == "cognitive" else SRC_STRIVEPD
    rel = fired[0] if fired else None
    out = []
    for en, ja in _BRAIN:
        item = _m(en, ja, src, rel)
        item["text_en"] = en + _BRAIN_NOTE_EN + _DISCUSS_EN
        item["text_ja"] = ja + _BRAIN_NOTE_JA + _DISCUSS_JA
        out.append(item)
    return out


def preventive_measures(pathway: str, fired_rule_ids: Iterable[str], profile: ProfileIn | None = None,
                        probabilities: list[dict[str, Any]] | None = None,
                        on_bp_medication: bool = False) -> list[dict[str, Any]]:
    """2–4 measures ``{text_en, text_ja, source_id, related_rule}`` for one pathway (§11.3)."""
    fired = list(fired_rule_ids)
    probabilities = probabilities or []
    if pathway == "cardio":
        items = _cardio(fired, profile, probabilities, on_bp_medication)
    elif pathway in ("cognitive", "parkinsons"):
        items = _brain(pathway, fired)
    else:  # general: sleep / activity
        rel = "GEN-01" if "GEN-01" in fired else None
        items = [_m(en, ja, SRC_HEARO, rel) for en, ja in _SLEEP]
    # de-duplicate by text, cap at MAX_ITEMS
    seen: set[str] = set()
    out = []
    for i in items:
        if i["text_en"] not in seen:
            seen.add(i["text_en"])
            out.append(i)
    return out[:MAX_ITEMS]
