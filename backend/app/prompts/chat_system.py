"""System prompt for the advocate chat (BACKEND_PLAN C2/C3, Phase E guardrails) and the
compact health-context block that is sent with every turn.

Key rules enforced by the prompt (and backed up by ``app.services.guardrails.output_guard``):
advocate framing, no diagnosis, no treatment/medication instructions, cite only the
numbered excerpts with ``[n]``, state limitations, answer in the user's language (English by
default — the product is for Canada), Canadian emergency guidance (911 / 811 / 9-8-8), end
with the standard disclaimer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import fmean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PATHWAYS, Observation, Profile, RiskAssessment, User

CONTEXT_MAX_CHARS = 1500

#: Metric types summarised in the context block (7-day vs 30-day mean of daily values).
KEY_METRICS: tuple[tuple[str, str], ...] = (
    ("resting_hr", "resting HR"),
    ("hrv_sdnn", "HRV SDNN"),
    ("steps", "steps"),
    ("sleep_asleep_minutes", "sleep min"),
    ("walking_speed_mps", "walking speed m/s"),
    ("spo2", "SpO2"),
)

_RULES_EN = """You are a health ADVOCATE inside a personal health-tracking app for adults in Canada. \
You help the user understand their own tracked data and published evidence, and prepare for conversations with \
their doctor. You are not a doctor.

Rules (all mandatory):
1. Never diagnose. Do not say or imply that the user has, or does not have, any disease. \
Describe what was measured or reported and what might be worth discussing with a doctor.
2. Never give treatment instructions: no medication starts/stops/dose changes, no specific \
therapies. You may list general, guideline-level lifestyle measures framed as "to discuss with \
your doctor".
3. Use only the numbered research excerpts provided in the message for factual claims about \
evidence, and cite them inline as [n] (e.g. [1], [2]). Never invent sources, numbers or \
citations. If the excerpts do not answer the question, say so plainly.
4. Use the user's health-context block only as context; do not speculate beyond it. Never \
present the follow-up priority score as a disease probability.
5. State limitations briefly (e.g. home devices and apps can be wrong; study cohorts may differ \
from the user; a missing measurement is not reassurance).
6. Emergency and crisis numbers are CONDITIONAL. Only if the user's own message describes sudden or \
severe symptoms, tell them to call {emergency_number} immediately (if unsure whether it is urgent: \
{health_line}, a registered nurse line — Health Link / Health811). Only if the user's own message \
mentions thoughts of suicide or self-harm, give 9-8-8 (Suicide Crisis Helpline, call or text, 24/7) \
and {emergency_number} if they are in danger. Otherwise NEVER add 9-8-8 or {health_line}, and \
mention {emergency_number} only inside the disclaimer line of rule 9 — not as an extra footer, not "just in case", not for test results, memory or \
story-recall scores, sad story content or low mood words inside excerpts or task material (the app \
detects red flags separately and adds emergency guidance itself). Use Canadian units (mmol/L for cholesterol and glucose, mmHg for blood \
pressure) and Canadian guideline sources (e.g. Hypertension Canada, Canadian Cardiovascular Society).
7. Be warm, concise (≤ 200 words unless asked for more) and practical: what the data shows, \
what the evidence says, what to ask or bring to the doctor.
8. Answer in {language_name}.
9. End with this disclaimer on its own line, verbatim: {disclaimer}"""


def system_prompt(language: str) -> str:
    """The advocate system prompt in the user's language (rules in English, answer language enforced)."""
    from app.services.guardrails import disclaimer_for

    from app.core.config import settings

    lang = "ja" if (language or "").lower().startswith("ja") else "en"
    return _RULES_EN.format(language_name="Japanese (日本語)" if lang == "ja" else "English (Canadian)",
                            disclaimer=disclaimer_for(lang), emergency_number=settings.emergency_number,
                            health_line=settings.health_line_number)


def format_excerpts(hits: list[Any]) -> str:
    """Numbered excerpt block ``[n] (title, year) text`` for the user turn."""
    if not hits:
        return "(no research excerpts were retrieved — say that the library has nothing relevant)"
    lines = []
    for n, h in enumerate(hits, start=1):
        src = h.source or {}
        lines.append(f"[{n}] {src.get('title') or h.source_id} ({src.get('year') or 'n.d.'})\n{h.text.strip()}")
    return "\n\n".join(lines)


def build_user_turn(question: str, excerpts: str, context_block: str) -> str:
    parts = []
    if context_block:
        parts.append(f"<health_context>\n{context_block}\n</health_context>")
    parts.append(f"<research_excerpts>\n{excerpts}\n</research_excerpts>")
    parts.append(f"<question>\n{question}\n</question>")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- health-context block

def _metric_lines(db: Session, user_id: str) -> tuple[list[str], dict[str, Any] | None]:
    try:
        from app.services.ingest_service import metrics_summary

        summary = metrics_summary(db, user_id, days=30)
    except Exception:
        return [], None
    metrics = summary.get("metrics") or {}
    today = datetime.fromisoformat(summary["as_of"]).date()
    lines = []
    for mtype, label in KEY_METRICS:
        m = metrics.get(mtype)
        if not m or not m.get("daily"):
            continue
        vals = [(datetime.fromisoformat(d["date"]).date(), d["value"]) for d in m["daily"]]
        last7 = [v for d, v in vals if (today - d).days < 7]
        all30 = [v for _, v in vals]
        if not last7:
            continue
        lines.append(f"- {label}: 7d {fmean(last7):.4g} vs 30d {fmean(all30):.4g} ({len(all30)} days)")
    return lines, summary.get("bp")


def _bp_lines(bp: dict[str, Any] | None) -> list[str]:
    if not bp:
        return []
    w = bp.get("window_7d") or {}
    jsh = w.get("jsh")
    out = []
    if jsh and jsh.get("systolic_mean") is not None:
        out.append(f"- home BP 7d mean {jsh['systolic_mean']:.0f}/{jsh['diastolic_mean']:.0f} "
                   f"({jsh['n_days']} days; morning+evening home series)")
        if jsh.get("morning_sys") is not None:
            out.append(f"  morning {jsh['morning_sys']:.0f}/{jsh['morning_dia']:.0f}"
                       + (f", evening {jsh['evening_sys']:.0f}/{jsh['evening_dia']:.0f}"
                          if jsh.get("evening_sys") is not None else ""))
    elif w.get("day_mean"):
        dm = w["day_mean"]
        out.append(f"- home BP 7d mean {dm['systolic']:.0f}/{dm['diastolic']:.0f} ({dm['n_days']} days"
                   + ("" if dm.get("sufficient") else ", <5 days: insufficient") + ")")
    elif bp.get("n_readings", 0) == 0:
        out.append("- home BP: no readings in the last 14 days")
    if bp.get("n_irregular_pulse_flags"):
        out.append(f"- irregular-pulse flags on BP monitor: {bp['n_irregular_pulse_flags']} (14d)")
    return out


def _observation_lines(db: Session, user_id: str, days: int = 30, limit: int = 8) -> list[str]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.scalars(select(Observation).where(Observation.user_id == user_id, Observation.created_at >= since)
                      .order_by(Observation.created_at.desc()).limit(40)).all()
    seen: set[tuple[str, str]] = set()
    out = []
    for o in rows:
        key = (o.kind, o.label)
        if key in seen:
            continue
        seen.add(key)
        sev = f", {o.severity}" if o.severity else ""
        out.append(f"- {o.kind}: {o.label}{sev} ({o.created_at.date().isoformat()})")
        if len(out) >= limit:
            break
    return out


def latest_levels(db: Session, user_id: str) -> dict[str, dict[str, Any]]:
    """Latest RiskAssessment per pathway → ``{pathway: {level, priority_score, rules, created_at}}``."""
    out: dict[str, dict[str, Any]] = {}
    for p in PATHWAYS:
        ra = db.scalars(select(RiskAssessment).where(RiskAssessment.user_id == user_id, RiskAssessment.pathway == p)
                        .order_by(RiskAssessment.created_at.desc()).limit(1)).first()
        if ra:
            rules = [r.get("id") if isinstance(r, dict) else str(r) for r in (ra.fired_rules or [])]
            out[p] = {"level": ra.level, "priority_score": ra.priority_score, "rules": [r for r in rules if r],
                      "created_at": ra.created_at}
    return out


def build_context_block(db: Session, user: User, max_chars: int = CONTEXT_MAX_CHARS) -> str:
    """Compact (< ``max_chars``) plain-text health context: profile basics, key metrics 7d vs 30d,
    14-day home BP window, open observations (30d), latest level per pathway, medications.
    Every part is best-effort; failures leave that part out."""
    sections: list[str] = []
    profile: Profile | None = user.profile
    if profile:
        bits = []
        if profile.date_of_birth:
            today = datetime.now(timezone.utc).date()
            dob = profile.date_of_birth
            bits.append(f"age {today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))}")
        if profile.sex:
            bits.append(profile.sex)
        known = [k for k in ("known_hypertension", "known_af", "known_chd", "known_pd", "known_dementia", "diabetes")
                 if getattr(profile, k, None)]
        if known:
            bits.append("known: " + ", ".join(k.replace("known_", "") for k in known))
        if bits:
            sections.append("Profile: " + "; ".join(bits))
    try:
        metric_lines, bp = _metric_lines(db, user.id)
    except Exception:
        metric_lines, bp = [], None
    if metric_lines:
        sections.append("Metrics (mean of daily values):\n" + "\n".join(metric_lines))
    try:
        bp_lines = _bp_lines(bp)
    except Exception:
        bp_lines = []
    if bp_lines:
        sections.append("Blood pressure (14d):\n" + "\n".join(bp_lines))
    try:
        obs = _observation_lines(db, user.id)
    except Exception:
        obs = []
    if obs:
        sections.append("Reported observations (30d):\n" + "\n".join(obs))
    try:
        levels = latest_levels(db, user.id)
    except Exception:
        levels = {}
    if levels:
        sections.append("Latest follow-up level per pathway (rule-based, not a diagnosis):\n" + "\n".join(
            f"- {p}: {v['level']}" + (f" (priority {v['priority_score']}/100)" if v["priority_score"] is not None
                                       else "") + (f" rules {','.join(v['rules'][:4])}" if v["rules"] else "")
            for p, v in levels.items()))
    meds = (profile.medications if profile else None) or []
    if meds:
        names = [" ".join(str(m.get(k)) for k in ("name", "dose") if m.get(k)) if isinstance(m, dict) else str(m)
                 for m in meds[:8]]
        sections.append("Medications: " + "; ".join(n for n in names if n))
    block = "\n".join(sections)
    if len(block) > max_chars:
        block = block[: max_chars - 1].rstrip() + "…"
    return block
