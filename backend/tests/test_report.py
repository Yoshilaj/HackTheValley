"""Clinician report generation (C7). Fakes only; helpers here are reused by the booking tests."""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.auth import create_access_token
from app.db.models import (
    CONSENT_PURPOSES, BpReading, Consent, DeviceRhythmResult, Observation, Profile, ResearchSource,
    RiskAssessment, User,
)
from app.providers.llm import FakeLlm
from app.services import report_service as rs

PRIORITY_EN = "Follow-up priority (not a disease probability)"


def make_user(db, consents=CONSENT_PURPOSES, lat=43.6534, lng=-79.3841, language="en") -> User:
    """A fresh user (unique e-mail) with a downtown-Toronto profile and the given consents. Commits."""
    user = User(email=f"t-{uuid4().hex[:10]}@example.com")
    db.add(user)
    db.flush()
    db.add(Profile(user_id=user.id, display_name="Test Patient", date_of_birth=date(1958, 1, 15), sex="male",
                   lat=lat, lng=lng, language=language, timezone="America/Toronto",
                   medications=[{"name": "Amlodipine", "dose": "5 mg", "schedule": "morning"}]))
    for purpose in consents:
        db.add(Consent(user_id=user.id, purpose=purpose))
    db.commit()
    db.refresh(user)
    return user


def headers_for(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


FRS = {
    "name": "10-year cardiovascular disease risk (Framingham Risk Score)", "calculator": "framingham_2008",
    "kind": "population_estimate", "status": "ok", "value_pct": 24.3, "value_display": "24.3", "category": "high",
    "inputs_used": ["age", "sex", "smoker", "diabetes", "bp", "bp_treated", "total_chol", "hdl"],
    "missing_inputs": [], "what_if": [{"change": "stop smoking", "value_pct": 15.1, "value_display": "15.1"}],
    "source_id": "src_framingham2008", "guideline_source_id": "src_ccs_lipids_2021",
    "note": "BP input is the 7-day home systolic mean, used as-is.",
}
#: Japan-only calculator (kept for region JP)
SUITA = {
    "name": "10-year coronary heart disease risk (Suita score)", "kind": "population_estimate", "status": "ok",
    "points": 52, "value_pct": 9.0, "value_display": "9", "inputs_used": ["age", "sex", "bp", "ldl", "hdl", "smoking"],
    "missing_inputs": [], "what_if": [{"change": "stop smoking", "value_pct": 5.0}], "source_id": "src_suita2014",
    "note": "BP input is the 7-day home mean.",
}


def make_assessment(db, user: User, level: str = "followup_recommended", pathway: str = "cardio",
                    with_data: bool = True) -> RiskAssessment:
    """RiskAssessment with BP-02 + RHY-01 fired, a Framingham probability, BP readings, a rhythm result,
    an observation and a ResearchSource row for src_jsh2019. Commits."""
    now = datetime.now(timezone.utc)
    if with_data:
        for d in range(7):
            for session, hours_ago in (("morning", 12), ("evening", 2)):
                db.add(BpReading(user_id=user.id, session=session, systolic=146, diastolic=92, pulse=72,
                                 measured_at=now - timedelta(days=d, hours=hours_ago), source="omron_photo"))
        db.add(BpReading(user_id=user.id, session="morning", systolic=199, diastolic=60, measured_at=now - timedelta(days=1),
                         valid=False, invalid_reason="user marked error"))
        db.add(DeviceRhythmResult(user_id=user.id, source="omron_connect", label="possible_af",
                                  raw_label="Possible AFib", recorded_at=now - timedelta(days=3)))
        db.add(Observation(user_id=user.id, kind="symptom", label="palpitations at night", severity="mild",
                           onset="1 week ago", source="chat", created_at=now - timedelta(days=2)))
    db.merge(ResearchSource(id="src_jsh2019", title="JSH Guidelines for the Management of Hypertension 2019",
                            authors="Umemura S, et al.", year=2019, url="https://example.org/jsh2019", kind="guideline"))
    a = RiskAssessment(
        user_id=user.id, pathway=pathway, level=level, priority_score=70 if level == "followup_recommended" else 100,
        fired_rules=[
            {"id": "BP-02", "version": "1", "level": "followup_recommended",
             "summary_text": "7-day home blood pressure averaged 146/92",
             "evidence": [{"type": "bp_window", "ref": "bpw_1", "summary": "7-day mean 146/92 over 7 days"}],
             "sources": ["src_jsh2019"]},
            {"id": "RHY-01", "version": "1", "level": "followup_recommended",
             "summary_text": "OMRON recorded a possible atrial-fibrillation result",
             "evidence": [{"type": "rhythm", "ref": "drr_1", "summary": "Possible AFib (OMRON Connect)"}],
             "sources": ["src_omron_connect"]},
        ],
        missing_inputs=[], data_quality={"valid_bp_days": 7, "invalid_readings": 1, "headline": {"en": "x"}},
        model_versions={"rules": {"BP-02": "1", "RHY-01": "1"}, "models": {}, "engine": "phase-a"},
        evidence=[], probabilities=[FRS],
        preventive_measures=[{"text": "Reduce salt to under 6 g/day", "source_id": "src_jsh2019"}],
        explanation="deterministic explanation", created_at=now,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@pytest.fixture()
def cardio(db):
    user = make_user(db)
    return user, make_assessment(db, user)


def test_report_contains_required_labels_and_no_diagnosis_language(db, cardio):
    user, a = cardio
    report = rs.generate_report(db, user, a, FakeLlm(), "en")
    assert report.id and report.share_token and report.pdf_url is None
    for doc in (report.markdown, report.html):
        assert PRIORITY_EN in doc
        assert "10-year cardiovascular disease risk (Framingham Risk Score)" in doc and "CCS high risk" in doc
        assert "JSH Guidelines for the Management of Hypertension 2019" in doc  # reference from ResearchSource row
        assert "BP-02" in doc and "RHY-01" in doc
        assert "146" in doc and "possible_af" in doc
        assert "palpitations at night" in doc
        assert "Amlodipine" in doc
        assert "you have" not in doc.lower()
        assert "diagnosed with" not in doc.lower()
    assert "## Summary for the clinician" in report.markdown
    assert "## References" in report.markdown
    assert "src_omron_connect" in report.markdown  # unknown id still listed as a reference
    assert "@page" in report.html and "@media print" in report.html
    assert "70 / 100" in report.markdown


def test_template_summary_used_for_fake_placeholder(db, cardio):
    user, a = cardio
    data = rs.build_report_data(db, user, a, "en")
    text, source = rs.write_summary(db, user, data, FakeLlm())
    assert source == "template"
    assert "not a disease probability" in text and "Framingham Risk Score" in text and "CCS high risk" in text
    assert "146/92" in text


def test_llm_summary_used_when_clean_and_rejected_when_diagnostic(db, cardio):
    user, a = cardio
    clean = ("A 68-year-old male recorded a 7-day home blood-pressure mean of 146/92 mmHg and an OMRON result "
             "labelled possible AFib; follow-up rules BP-02 and RHY-01 fired.")
    report = rs.generate_report(db, user, a, FakeLlm(canned_answer=clean), "en")
    assert clean in report.markdown and "AI-drafted" in report.html

    data = rs.build_report_data(db, user, a, "en")
    bad = FakeLlm(canned_answer="Based on these readings the patient is diagnosed with hypertension and atrial fibrillation.")
    _, source = rs.write_summary(db, user, data, bad)
    assert source == "template"
    bad2 = FakeLlm(canned_answer="You have high blood pressure and should see a doctor immediately about it.")
    assert rs.write_summary(db, user, data, bad2)[1] == "template"


def test_llm_failure_falls_back(db, cardio):
    user, a = cardio

    class Boom(FakeLlm):
        def complete(self, *args, **kwargs):
            raise RuntimeError("api down")

    report = rs.generate_report(db, user, a, Boom(), "en")
    assert "not a disease probability" in report.markdown


def test_llm_only_sees_report_json(db, cardio):
    user, a = cardio
    seen = {}

    class Spy(FakeLlm):
        def complete(self, system, messages, *args, **kwargs):
            seen["system"], seen["user"] = system, messages[-1]["content"]
            return super().complete(system, messages, *args, **kwargs)

    rs.generate_report(db, user, a, Spy(), "en")
    assert "Use ONLY facts present in the JSON" in seen["system"]
    assert "Do NOT diagnose" in seen["system"]
    assert '"BP-02"' in seen["user"] and '"n_readings": 15' in seen["user"]
    assert '"readings": [' not in seen["user"]  # individual readings are not sent to the LLM


def test_japanese_labels(db):
    user = make_user(db, language="ja")
    a = make_assessment(db, user)
    report = rs.generate_report(db, user, a, FakeLlm())
    assert 'lang="ja"' in report.html
    assert "受診の優先度（病気の確率ではありません）" in report.markdown
    assert "心血管疾患" in report.html and "CCS 高リスク" in report.html
    assert "あなたは" not in report.markdown


def test_regenerate_keeps_share_token(db, cardio):
    user, a = cardio
    first = rs.generate_report(db, user, a, FakeLlm(), "en")
    again = rs.regenerate_report(db, user, a, FakeLlm(), "ja")
    assert again.id == first.id and again.share_token == first.share_token
    assert "受診の優先度" in again.markdown


def test_render_pdf_is_optional_hook(db, cardio):
    user, a = cardio
    with pytest.raises(NotImplementedError):
        rs.render_pdf(rs.generate_report(db, user, a, FakeLlm(), "en"))


def test_report_for_other_users_assessment_rejected(db, cardio):
    _, a = cardio
    other = make_user(db)
    with pytest.raises(PermissionError):
        rs.generate_report(db, other, a, FakeLlm())


# ---------------------------------------------------------------- routes

def test_share_route_public_and_404(client, db, cardio):
    user, a = cardio
    report = rs.generate_report(db, user, a, FakeLlm(), "en")
    res = client.get(f"/r/{report.share_token}")
    assert res.status_code == 200
    assert "Shared by the patient for clinical review" in res.text
    assert PRIORITY_EN in res.text
    assert res.headers["x-robots-tag"].startswith("noindex")
    assert client.get("/r/does-not-exist").status_code == 404


def test_report_routes(client, db, cardio):
    user, a = cardio
    h = headers_for(user)
    report = rs.generate_report(db, user, a, FakeLlm(), "en")

    listed = client.get("/api/reports", headers=h).json()
    assert [r["id"] for r in listed] == [report.id]
    assert listed[0]["pathway"] == "cardio" and listed[0]["share_url"].endswith("/r/" + report.share_token)

    one = client.get(f"/api/reports/{report.id}", headers=h).json()
    assert PRIORITY_EN in one["markdown"]
    html = client.get(f"/api/reports/{report.id}/html", headers=h)
    assert html.status_code == 200 and "<html" in html.text
    assert "Shared by the patient" not in html.text

    regen = client.post(f"/api/reports/{a.id}/regenerate?language=en", headers=h).json()
    assert regen["id"] == report.id

    other = headers_for(make_user(db))
    assert client.get(f"/api/reports/{report.id}", headers=other).status_code == 404
    assert client.post(f"/api/reports/{a.id}/regenerate", headers=other).status_code == 404
    assert client.get("/api/reports").status_code == 401


def test_suita_entry_still_labelled_for_japan(db):
    user = make_user(db)
    a = make_assessment(db, user, with_data=False)
    a.probabilities = [SUITA]
    db.commit()
    md = rs.generate_report(db, user, a, FakeLlm(), "en").markdown
    assert rs.SUITA_LABEL["en"] in md and "Framingham" not in md.split("## References")[0]


# ---------------------------------------------------------------- PD-03B voice stability (spec §5.5)

def _voice_feature(feature, task, direction, med, scale, k, recent, n_adverse, status="adverse", rates=None, placeholder=False):
    thr = med + k * scale if direction == "up" else med - k * scale
    return {"type": "voice_trend_feature", "ref": "vv_0",
            "summary": f"{feature}: {n_adverse} of last 5 sessions shifted",
            "value": {"feature": feature, "label": feature, "direction": direction, "task_type": task, "n_baseline": 10,
                      "status": status, "baseline_median": med, "baseline_mad": scale, "scale": scale, "scale_note": None,
                      "k": k, "k_placeholder": placeholder, "no_change_alert_rates": rates, "threshold": thr,
                      "recent": [{"id": f"vv_{i}", "date": f"2026-10-{10 + i}", "value": v} for i, v in enumerate(recent)],
                      "n_adverse": n_adverse, "n_recent": 5}}


VOICE_EVIDENCE = [
    _voice_feature("MDVP:Shimmer", "sustained_vowel", "up", 0.03, 0.0101, 3.0, [0.03, 0.028, 0.08, 0.07, 0.075], 3,
                   rates={"k_table": 3.0, "single": 0.06, "two_of_three": 0.024}),
    _voice_feature("NHR", "sustained_vowel", "up", 0.012, 0.0069, 3.0, [0.012, 0.011, 0.012, 0.013, 0.012], 0, "stable",
                   rates={"k_table": 3.0, "single": 0.085, "two_of_three": 0.031}),
    _voice_feature("speech_rate", "story_recall", "down", 130.0, 13.0, 2.0, [131, 129, 95, 96, 94], 3, placeholder=True),
]


def make_pd_assessment(db, user: User, fired: bool) -> RiskAssessment:
    rule = {"id": "PD-03B", "version": "1", "level": "watch",
            "summary_text": "Voice measurements have shifted from your usual range alongside another finding — a change worth checking",
            "evidence": VOICE_EVIDENCE, "sources": ["src_skodda2013", "src_wright2025", "src_bottalico2018", "src_barsties2025"]}
    pd04 = {"id": "PD-04", "version": "1", "level": "watch", "summary_text": "Finger-tapping results have shifted",
            "evidence": [{"type": "daily_series", "ref": "tap_count", "summary": "tap count: 7 of last 7 days below"}],
            "sources": ["src_hearo"]}
    message = {"en": {"title": "Change in voice measurements", "body": "Some measurements of your voice have changed."},
               "ja": {"title": "声の測定値の変化", "body": "声のいくつかの測定値が変化しています。"}}
    a = RiskAssessment(
        user_id=user.id, pathway="parkinsons", level="watch" if fired else "no_finding", priority_score=35 if fired else 0,
        fired_rules=[pd04, rule] if fired else [], missing_inputs=[],
        data_quality={"voice_baseline_sessions": {"sustained_vowel": 10, "story_recall": 10}, "voice_baseline_needed": 10,
                      "voice_device": "iPhone", "voice_sessions_excluded_noise": 1, "headline": {"en": "x"},
                      "informational": [{"rule_id": "PD-03B", "kind": "note", "summary": "Change in voice measurements — …",
                                         "evidence": VOICE_EVIDENCE, "message": message}]},
        model_versions={"rules": {"PD-03B": "1"}, "models": {"pd_vowel": "v1"}, "engine": "phase-a"},
        evidence=[], probabilities=[{"name": "experimental voice-model score", "kind": "experimental_model_score",
                                     "detail_only": True, "value": 0.2, "threshold": 0.6}],
        preventive_measures=[], explanation="x", created_at=datetime.now(timezone.utc))
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def test_pd03b_report_table_citations_and_limitation(db):
    user = make_user(db)
    a = make_pd_assessment(db, user, fired=True)
    data = rs.build_report_data(db, user, a, "en")
    v = data.voice_stability
    assert v["fired"] and v["device"] == "iPhone" and v["sessions_excluded_noise"] == 1
    assert [f["feature"] for f in v["features"]] == ["MDVP:Shimmer", "NHR", "speech_rate"]
    assert v["features"][0]["no_change_alert_rate"] == 0.024 and v["features"][2]["k_placeholder"] is True
    assert v["features"][2]["recent_values"] == [131, 129, 95, 96, 94]
    ref_ids = [r["id"] for r in data.references]
    assert {"src_skodda2013", "src_wright2025", "src_bottalico2018", "src_barsties2025", "src_uci_telemonitoring"} <= set(ref_ids)
    assert v["refs"] == [n for n, rid in enumerate(ref_ids, start=1) if rid in v["sources"]]
    report = rs.generate_report(db, user, a, FakeLlm(), "en")
    md = report.markdown
    assert "### Voice measures vs personal baseline (experimental, PD-03B) [" in md
    assert "**Voice measurements have shifted from your usual range alongside another finding" in md
    assert "| MDVP:Shimmer | sustained_vowel | higher | 0.03 (0.0101) | 0.0603 (K=3) | 0.03, 0.028, 0.08, 0.07, 0.075 | 3/5 | shifted (expected alert rate with no true change 2%) |" in md
    assert "| NHR | sustained_vowel | higher |" in md and "| 0/5 | within range (expected alert rate with no true change 3%) |" in md
    assert "(K=2*)" in md and "K not yet calibrated" in md
    assert "Device: iPhone · baseline sessions: sustained_vowel 10/10, story_recall 10/10" in md
    assert "**Change in voice measurements** — Some measurements of your voice have changed." in md
    assert rs.LABELS["en"]["voice"] in report.html and "0.0603 (K=3)" in report.html and "flagrow" in report.html
    for doc in (md, report.html):
        assert "voice stability, not progression" in doc and "Progression of voice and speech impairment" in doc
        assert "PD-04" in doc and "you have" not in doc.lower().replace("whether you have parkinson", "")
    # §5.4: the v1 model score stays in the experimental-model section with its caveat
    assert "Sustained-vowel voice model (experimental)" in md and "not validated for diagnosis" in md


def test_pd03b_report_display_only_and_japanese(db):
    user = make_user(db, language="ja")
    a = make_pd_assessment(db, user, fired=False)
    report = rs.generate_report(db, user, a, FakeLlm())
    md = report.markdown
    assert "### 声の指標と個人ベースラインの比較（実験的、PD-03B）" in md
    assert "**声の測定値の変化** — 声のいくつかの測定値が変化しています。" in md
    assert "| MDVP:Shimmer | sustained_vowel | 上昇 |" in md and "変化あり (真の変化がない場合の予想警告率 2%)" in md
    assert "PD-03Bは進行ではなく「声の安定性」を示します" in md
    assert "Voice measurements have shifted" not in md  # not fired → no fired summary line
    assert "Skodda S" in md  # citations still listed for the display-only table
    assert "あなたは" not in md


def test_report_without_voice_evidence_has_no_voice_section(db, cardio):
    user, a = cardio
    data = rs.build_report_data(db, user, a, "en")
    assert data.voice_stability is None and "PD-03B" not in rs.render_markdown(data)
