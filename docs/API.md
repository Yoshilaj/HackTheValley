# API reference

Base URL (dev): `http://localhost:8000`. From the frontend call `/api/...` (the Vite proxy forwards it).
Interactive schema: `GET /docs` (Swagger) and `GET /openapi.json`. This file is checked against `app.openapi()`
(74 operations, 2026-10-17); add new endpoints here when you build them.

## Conventions

**Auth** column:

| Value | Meaning |
|---|---|
| `user` | One of: `Authorization: Bearer <JWT>` (from signup/login, 7 days); `X-Device-Token: <token>` (iOS Shortcut, from `POST /api/auth/device-token`); `X-User-Id: <id>` (**dev mode only**). Missing/invalid → 401. Other users' rows → 404. |
| `admin` | `X-Admin-Secret: <ADMIN_SECRET>`. Missing/wrong → 403. |
| `dev/admin` | Open when `DEV_MODE=true`, else `admin`. |
| `public` | No auth. |

**Errors** are JSON `{"detail": ...}` (FastAPI). Extra cases:
- `422` validation error (`detail` = list of field errors).
- `429` rate limit (`/api/chat`, `/api/chat/voice`, `/api/uploads/*`, `/api/tasks/*`; default 30/minute per user, else per IP; `Retry-After` header). Settings `RATE_LIMIT_ENABLED`, `RATE_LIMIT_DEFAULT`.
- `503 {"detail": "VOYAGE_API_KEY ... not set", "error": "provider_not_configured"}` when a real provider is selected (e.g. `LLM_PROVIDER=anthropic`) without its key.

Times are ISO 8601 (UTC `Z` from the DB; slots carry the user's offset). `language` is `ja | en` and defaults to the profile language.

Enumerations used below:
- `level` ∈ `insufficient_data | invalid_measurement | no_finding | watch | followup_recommended | emergency`
- `pathway` ∈ `cardio | cognitive | parkinsons | general`
- consent `purpose` ∈ `data_processing | ai_assessment | calendar_access | share_with_clinic | emergency_contact`
- `Observation.kind` ∈ `symptom | functional_change | medication | lifestyle | mood | red_flag`

---

## 1. Auth

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| POST | /api/auth/signup | public | `{ email, password (8–128), display_name? }` | 201 `Token`; 409 email taken |
| POST | /api/auth/login | public | `{ email, password }` | `Token`; 401 bad credentials |
| POST | /api/auth/device-token | user | `{ label? = "iOS Shortcut" }` | 201 `{ id, token, label }` — `token` is shown once (only its hash is stored) |
| GET | /api/auth/me | user | – | `{ id, email, created_at }` |

`Token` = `{ access_token, token_type: "bearer", user_id }`. Demo account (after seeding): `demo@example.com` / `demo1234`.

## 2. Profile & consent

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | /api/profile | user | – | `Profile` (created empty on first read) |
| PUT | /api/profile | user | partial `Profile` (only fields sent are changed) | `Profile`; 422 on invalid onboarding values (see below) |
| GET | /api/profile/booking-readiness | user | – | `{ ready, missing: [field…], recommended: [field…], fields_shared_with_clinics: [field…], never_shared: [field…] }` — for the onboarding / approval UI |
| GET | /api/consent | user | – | `Consent[]` |
| POST | /api/consent | user | `{ purpose, granted = true }` (false revokes) | `Consent[]` |
| GET | /api/me/export | user | – | `{ user, <table>: rows[] }` for every user-owned table (`profiles, consents, metric_samples, bp_readings, …, appointments, reports, audit_logs`); OAuth tokens redacted |
| DELETE | /api/me | user | – | `{ deleted: true, counts: { <table>: n, files: n }, files_deleted }` — hard-deletes every row (incl. OAuth credentials) and every stored file |

`Profile` = `{ user_id, display_name (preferred name), first_name, last_name (legal name, as on the health card), date_of_birth, age (computed), sex (male|female|other|prefer_not — as on the health card), height_cm, weight_kg, smoking_status (never|former|current), diabetes, ckd, egfr, ldl_mg_dl, hdl_mg_dl, total_chol_mg_dl, known_hypertension, known_af, known_chd, known_pd, known_dementia, family_history {}, medications [{name, dose,…}], address (free-form; set to "street, city" when only the parts are sent), street, city, province (AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT), postal_code ("A1A 1A1"), phone, contact_email (optional; clinics get this, else the account e-mail), lat, lng, language ("en" — also the patient's preferred language for clinics), timezone ("America/Toronto"), family_doctor {name, clinic, phone}, has_referral (bool, optional), emergency_contact {name, relation, phone, email}, updated_at }`.
Onboarding (collected at sign-up, used by the booking agent): `first_name, last_name, date_of_birth, sex, phone, street, city, province, postal_code` are required for `ready` (e-mail comes from the account); `family_doctor`, `has_referral` are `recommended`. PUT validation: `date_of_birth` in the past (≥ 1900); `postal_code` a Canadian code (normalised to upper case `A1A 1A1`; `k1a-0b1` → `K1A 0B1`); `province` a code above (case-insensitive); `phone` / `family_doctor.phone` E.164-ish (`+1 416-555-0142`, `(416) 555-0142`, `+44 20 …`; formatting is kept); `contact_email` an e-mail (lower-cased). Changing the address clears `lat` / `lng` (re-geocoded at the next clinic search). **There is no health-card / OHIP / government-ID field** and none is ever collected — the patient brings the health card to the visit.
`fields_shared_with_clinics` = `first_name, last_name, date_of_birth, sex, phone, email, street, city, province, postal_code, family_doctor, has_referral, language, reason_category, preferred_slots, report_url`; `never_shared` = `health_card_number, medical_history, conditions, measurements, medications, diagnoses, insurance`.
`Consent` = `{ id, purpose, granted_at, revoked_at, active }`. Consent gates: `ai_assessment` (follow-up hooks), `share_with_clinic` (booking agent: any clinic contact, checked again at approval), `calendar_access` (FreeBusy + events), `emergency_contact` (e-mail the contact on emergencies).

## 3. Metrics & logs

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| POST | /api/metrics | user | `{ source?: shortcut\|apple_health\|manual\|fitbit\|omron_photo, samples: [{ type, value, unit?, recorded_at, source?, meta? }] }` | `{ inserted, updated, duplicates, rejected: [{ index, reason }] }` — idempotent on (type, recorded_at, source) |
| GET | /api/metrics/summary?days=30 | user | – | `{ days, as_of, metrics: { <type>: { unit, aggregation (sum\|mean), daily: [{date, value}], baseline_30d: {median, mad, n_days}\|null, last_value: {value, recorded_at, source}, n_days } }, bp: BpSummary }` |
| POST | /api/metrics/bp | user | `{ readings: [{ systolic, diastolic, pulse?, measured_at, session? (morning\|evening\|other), reading_no?, irregular_pulse_flag?, arm?, position?, pre_medication?, user_marked_error? }] }` | 201 `BpReading[]` = `{ id, session, reading_no, systolic, diastolic, pulse, measured_at, …, valid, invalid_reason, source }` |
| GET | /api/metrics/bp?days=14 | user | – | `{ readings: BpReading[], summary: BpSummary }` |
| POST | /api/metrics/rhythm | user | `{ source: apple_irn\|apple_ecg\|omron_connect\|manual_irregular_pulse, label (raw device string, e.g. "心房細動の可能性"), recorded_at, file_url?, meta? }` | 201 `{ id, source, label (normalised: sinus_rhythm\|possible_af\|afib\|high_hr\|low_hr\|inconclusive\|poor_recording\|irregular_rhythm_notification\|other), raw_label, recorded_at, file_url, meta }` |
| GET | /api/metrics/rhythm?days=90 | user | – | `RhythmResult[]` |
| POST | /api/logs/daily | user | `{ date, sleep_quality?, fatigue?, mood?, stress? (1–5), flags?: {name: bool}, medication_adherence?, caffeine_alcohol?, exercise_minutes?, notes? }` | `DailyLog + { observations: Observation[], emergency_guidance: string \| null }` (upsert by date; true flags become Observations — existing ones keep their `created_at`, ones whose flag is now false are deleted; red-flag flags **or red-flag text in `notes`** trigger the emergency path: `emergency_guidance` text, emergency notification (throttled 24 h per observation), event risk assessment) |
| GET | /api/logs/daily?days=30 | user | – | `DailyLog[]` (each with `observations`) |

`BpSummary` = `{ days, per_day: [{ date, morning?: {systolic, diastolic, n}, evening?: {…}, day_mean: {systolic, diastolic} }], window_7d: { start, end, day_mean, morning, evening (each {systolic, diastolic, n_days, sufficient}), method, jsh? }, n_readings, n_invalid, n_irregular_pulse_flags }`.
Metric types and units: `METRIC_TYPES` in `backend/app/schemas/metrics.py` (steps, resting_hr, hrv_sdnn_ms, sleep_asleep_minutes, walking_speed_mps, step_length_m, double_support_pct, walking_asymmetry_pct, …). iOS Shortcut setup: [docs/shortcut.md](shortcut.md).

## 4. Uploads

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| POST | /api/uploads/apple-health | user | multipart `file` (export.zip or export.xml), `since?` (date) | 202 `{ job_id, upload_id, status: "pending" }` — parsed in a background task |
| GET | /api/uploads/jobs/{job_id} | user | – | `Job` (below) with `name: "apple_health_import"`, `status: pending\|running\|done\|failed`, `result: { parse: counts, metrics: {inserted,…}, bp_readings, rhythm_results }` |
| POST | /api/uploads/document | user | multipart `file` + `kind` ∈ `doctor_note\|prescription\|lab\|bp_photo\|ecg_pdf\|audio` | 201 `Upload`; 415 unsupported type; 413 too large |
| POST | /api/uploads/bp-photo | user | multipart `file` (JPEG/PNG/GIF/WebP of a BP monitor display) | 201 `{ document: Upload, bp_readings: BpReading[] }` (LLM vision extraction) |
| GET | /api/uploads | user | – | `Upload[]` |

`Upload` = `{ id, kind, status (done\|failed\|pending_transcription\|transcribed), extracted: { filename, media_type, data, created, transcript? }, created_at }`. Audio documents are transcribed by the `process_pending_uploads` job (every 10 min).

## 5. Observations

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | /api/observations?kind=&days=30 | user | – | `Observation[]` |
| POST | /api/observations | user | `{ kind, label, severity?, onset?, free_text?, source? }` | 201 `Observation + { emergency_guidance: string \| null }` (a red-flag kind/label or red-flag text in `free_text` takes the emergency path; detected text labels add `red_flag` Observations) |
| DELETE | /api/observations/{id} | user | – | 204 |

`Observation` = `{ id, kind, label, severity, onset, free_text, source (chat\|log\|document\|screener), source_id, created_at }`.

## 6. Screener (Parkinson's, spec §5.1)

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | /api/screener/pd?language= | user | – | `{ id: "pd", version, language, period, response_type: "yes_no", items: [{ id, type (motor\|nonmotor), text }] }` |
| POST | /api/screener/pd | user | `{ answers: { <item id>: bool } }` | 201 `{ id, screener, score, motor_count, nonmotor_count, pd01_would_fire, next_tasks (["sustained_vowel","finger_tap"] when PD-01 would fire), created_at }`; 422 unknown item |

Item ids (canonical, from `app/content/screener_pd.json`): `tremor_rest, stiffness_slowness, clumsiness_handwriting, soft_voice` (motor), `smell_loss, dream_enactment, constipation, falls` (non-motor). Legacy ids (`pd_rest_tremor`, …) are still accepted.

## 7. Assessment tasks (spec §1, §1.4, §5.3, §5.4)

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| POST | /api/tasks/story/start?language= | user | – | 201 `StoryStatus`; 409 when no unused stories remain |
| GET | /api/tasks/story/{task_id} | user | – | `StoryStatus` |
| GET | /api/tasks/story/{task_id}/audio/{step} | user | `step` ∈ `story1_listen\|story2_listen` | audio bytes (`audio/mpeg`; `audio/wav` with fake TTS); 400 for other steps |
| POST | /api/tasks/story/{task_id}/step | user | multipart: `step` (= `current_step`), `audio` file (recall/fluency), `transcript` (text fallback), `force_delay` bool (demo, `delay` step), `played_s` float (listen steps) | `StoryStatus`; 409 `{detail, current_step}` out of order; 409 `{detail, delay_remaining_s}` before the delay elapsed |
| POST | /api/tasks/vowel | user | multipart: `audio` (≈5 s "ahh"), `repetition` 1–10 | 201 `{ task_id, repetition_index, valid, status (ok\|invalid_measurement), recording_quality {duration_s, voiced_s, snr_db, clip_frac, ok, reasons}, reasons[], message, tips[], experimental_score {probability, threshold, above_threshold, version, label, detail_only: true, calibration_note} \| null }`; 422 undecodable audio |
| POST | /api/tasks/tapping | user | `{ left_ms: number[], right_ms: number[], duration_s? = 15 }` (pointer-down timestamps) | 201 `{ task_id, valid, reason, features {left{…}, right{…}, asymmetry, iti_asymmetry, tap_count, mean_iti_ms, iti_cv, decrement_slope_ms_per_tap, valid, reason}, message }` |
| POST | /api/tasks/word-recall/start | user | `{ language? }` (or `?language=`) | 201 `{ task_id, set_id, language, words[3], instruction, recall_prompt, presented_at }` |
| POST | /api/tasks/word-recall/{task_id} | user | `{ response_text, latency_ms? }` | `{ task_id, score (0–3), max_score, recalled[], missed[], latency_ms, valid }`; 409 already scored |
| GET | /api/tasks/history?task_type=&limit=50 | user | – | `{ items: [{ id, task_type, created_at, status (in_progress\|complete), score, valid, invalid_reason, language, protocol_version, model_scores }] }` |

`StoryStatus` = `{ task_id, session_id, language, protocol_version, steps[7], step_index, current_step (null when finished), finished, instruction, instructions {en, ja}, audio_url (listen steps), delay_remaining_s, max_recall_s (180), result }`.
Steps: `story1_listen → story1_recall → story2_listen → story2_recall → fluency_60s → delay → story1_delayed_recall` (delay ≈ 3 min after story 1 recall).
`result` = `{ score (experimental semantic-recall mean 0–100), score_name, valid, status, invalid_reasons[], per_recall {immediate_1, immediate_2, delayed}, content_unit_fraction_mean, story_ids, message }`.
`model_scores.pd_vowel` is marked `detail_only: true, label: "experimental voice-model score"`: show it only in a detail view, never on the main gauge. For PD-03 the risk engine counts one vowel session per calendar day (median of that day's repetitions).

## 8. Chat & RAG (C2, C3)

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| POST | /api/chat | user | `{ message (1–4000), language? }` | `ChatTurn` |
| POST | /api/chat/voice | user | multipart `file` (audio/*, ≤25 MB), `language?` | `ChatTurn + { transcript, task_id }` (`task_id` = `AssessmentTask(free_speech)`); 422 no speech recognised, 415 not audio |
| GET | /api/chat/history?limit=50 | user | – | `[{ id, role (user\|assistant), content, audio_url, sources, created_at }]` oldest first |
| GET | /api/chat/checkin?language= | user | – | `[{ id, text, type: scale\|bool\|text, reason, pathway?, sessions?, task? (story_recall\|sustained_vowel) }]` |
| POST | /api/chat/checkin | user | `{ answers: { <question id>: value }, date?, language? }` | `{ date, daily_log_id, observations: Observation[], red_flags, emergency_guidance: str\|null, ignored: [id] }` |
| POST | /api/ingest | dev/admin | `{ text, source (ResearchSource id), title?, pathway_tags?, url?, year? }` | `{ chunks_added, source_id }` |
| POST | /api/ingest/corpus?force=false&limit_chunks_per_source= | dev/admin | – | `{ sources, ingested, skipped, failed, total_chunks, total_parents, pruned: [id], per_source: [{ id, status, loader, chunks, parents }] }` (corpus: `backend/data/raw/research/sources.json`; English only; `pruned` is always `[]` here — removing a source is done with `scripts/ingest_corpus.py`) |
| GET | /api/sources?pathway= | public | – | `[{ id, title, authors, year, url, doi, pathway_tags, kind, chunk_count }]` |

`ChatTurn` = `{ answer, sources: SourceRef[], cited: int[], observations_created: Observation[], red_flags: str[], assessment_hint, message_id, language }`. `cited` = the `n` values that actually appear as `[n]` in `answer` (a subset of `sources[].n`, ascending; `[1-3]` and `[1, 3]` forms are expanded). `sources` lists every passage the model was given, cited or not; show `cited` ones as references and the rest (if at all) as "also consulted". History rows (`GET /api/chat/history`) carry `sources` but not `cited`: derive it from the `[n]` markers in `content`.
`SourceRef` = `{ n, id, title, year, url, snippet, chunk_id, section, score }`; `n` matches the `[n]` markers in `answer`. `section` = heading path of the parent section the passage comes from (e.g. `"Abstract > Results"`, `"4.2 Investigations"`, `"p. 6"` for PDFs; `null` for legacy rows); show it next to the title so two passages from one source are distinguishable. Normally at most 2 passages per source (`app/rag/packing.py:MAX_PER_SOURCE`); a source can fill more slots only when no other source has a relevant passage, so the same `id` can appear several times with different `section`s. `score` = cross-encoder logit (can be negative) or RRF score with `RERANKER=identity`; for ordering/debugging only. **`sources` can be empty on a normal (non-red-flag) turn**: with the cross-encoder, passages scoring below `RAG_MIN_RERANK_SCORE` (default −6.0) are dropped, and when nothing in the English research library is relevant ("how much sleep do I need?") the answer says so without citations; render a "no research sources for this answer" state rather than an empty list.
Red flags (`chest_pain, chest_pressure, speech_difficulty, vision_disturbance, fainting, face_droop, arm_weakness, severe_sudden_headache, suicidal_ideation`; keyword detection) short-circuit the turn: `answer` is emergency guidance (Canada: 911, Health811 nurse line 811, 9-8-8 Suicide Crisis Helpline; `settings.emergency_number` / `health_line_number` / `crisis_line_number`), an `emergency` notification is created (+ e-mail to the emergency contact with consent), `Observation(kind=red_flag, severity=urgent)` rows are written, `sources` is empty, no LLM call, and the next risk run puts `general` at `emergency` with no booking.
`assessment_hint` ∈ `emergency | story_recall | pd_screener | bp_and_rhythm_check | null`.
Check-in question ids: `sleep_quality, fatigue, mood` (scale 1–5); `bp_reminder, palpitations, dizziness, breathlessness, tremor, stiffness, memory_lapse, task_story_recall, task_sustained_vowel` (bool); `functional_change` (text).
Every answer ends with the disclaimer (`DISCLAIMER_JA/EN`, `app/services/guardrails.py`).

## 9. Risk & dashboard (C4, spec §10–§11)

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | /api/dashboard?language= | user | – | `{ pathways: PathwayCard[4] (cardio, cognitive, parkinsons, general), metrics_summary, appointments (active), notifications: { unread, recent[5] } }` — assesses on demand when there is no assessment yet |
| GET | /api/risk/latest?language= | user | – | `PathwayCard[]` |
| GET | /api/risk/history?weeks=8 | user | – | `[{ week (Monday), pathway, level, priority_score, assessment_id }]` oldest first |
| POST | /api/risk/run[?user_id=] | user (or admin with `?user_id=`) | – | `{ user_id, trigger: "manual", assessments: RiskAssessment[4] }` |
| GET | /api/risk/{assessment_id} | user | – | `RiskAssessment` |

`RiskAssessment` = `{ id, pathway, level, priority_score (0–100\|null), fired_rules: [{ id, version, level, summary_text, evidence, sources, persistence_windows }], missing_inputs, data_quality, model_versions, evidence, probabilities, preventive_measures, explanation, created_at }`.

What a risk run does: one row per pathway; level change → `risk_level_change` notification; newly `followup_recommended` → `followup_recommended` notification (+ e-mail) and, with `ai_assessment` consent, the report + booking agent (§10, §12); `emergency` → `emergency` notification and **no** report/booking for any pathway in that run; `insufficient_data` → `data_needed` (at most once per 7 days per pathway). The level comes only from fired rules; no LLM is called.

`PathwayCard` (spec §11.4):

```json
{
  "pathway": "cardio",
  "level": "followup_recommended",
  "priority_score": 70,
  "score_kind": "follow_up_priority",
  "score_label": "Follow-up priority (not a disease probability)",
  "headline": "Two findings suggest a check-up is worth arranging",
  "drivers": [
    {"rule_id": "BP-02", "text": "7-day home blood pressure averaged 146/92", "evidence_ids": ["bpw_…"]},
    {"rule_id": "RHY-01", "text": "OMRON recorded a possible atrial-fibrillation result on Oct 14", "evidence_ids": ["drr_…"]}
  ],
  "missing_inputs": [],
  "data_quality": {"valid_bp_days": 7, "invalid_readings": 1},
  "trend": [{"week": "2026-09-21", "priority_score": 0}, {"week": "2026-09-28", "priority_score": 25}, {"week": "2026-10-05", "priority_score": 60}, {"week": "2026-10-12", "priority_score": 70}],
  "probabilities": [
    {"name": "10-year cardiovascular disease risk (Framingham Risk Score)", "calculator": "framingham_2008",
     "value_pct": 49.8, "value_display": "49.8", "category": "high", "kind": "population_estimate",
     "inputs_used": ["age", "sex", "smoker", "diabetes", "bp", "bp_treated", "total_chol", "hdl"], "missing_inputs": [],
     "what_if": [{"change": "stop smoking", "value_pct": 30.1, "value_display": "30.1", "category": "high"},
                 {"change": "systolic BP 120 mmHg", "value_pct": 37.5, "value_display": "37.5", "category": "high"}],
     "source_id": "src_framingham2008", "guideline_source_id": "src_ccs_lipids_2021"}
  ],
  "long_term_risk": {"calculator": "framingham_2008", "status": "ok", "percent_10y": 49.8, "display": "49.8",
     "category": "high", "inputs": {"age": 68, "sex": "male", "smoker": true, "diabetes": false,
       "systolic_bp_mmhg": 146.4, "bp_treated": false, "total_chol_mmol_l": 5.8, "hdl_mmol_l": 1.15,
       "family_history_premature_cvd": false},
     "inputs_used": ["…"], "missing_inputs": [], "what_if": [{"change": "stop smoking", "percent_10y": 30.1, "display": "30.1", "category": "high"}],
     "source_id": "src_framingham2008", "guideline_source_id": "src_ccs_lipids_2021", "note": "BP input is the 7-day home systolic mean …"},
  "preventive_measures": [
    {"text": "Eat less salt and follow a DASH-style diet (more vegetables, fruit and fibre, less saturated fat) (to discuss with your doctor)", "source_id": "src_hypertension_canada_2020"},
    {"text": "Bring your device's rhythm recordings to the visit (to discuss with your doctor)", "source_id": "src_ccs_af_2020"}
  ],
  "next_action": {"type": "appointment_requested", "appointment_id": "apt_…"},
  "disclaimer": "This is not a diagnosis. The priority score reflects which follow-up rules fired, not the chance that you have a condition."
}
```

`long_term_risk` (added 2026-10-17, additive): the cardio card's long-term context estimate, `null` on the other cards. Canada uses `calculator: "framingham_2008"` (D'Agostino 2008 general CVD, CCS 2021 usage: doubled for a first-degree relative with premature CVD at age 30–59 without diabetes; `category` = CCS `low` <10% · `intermediate` 10–19.9% · `high` ≥20%; cholesterol inputs in mmol/L). `calculator: "suita"` only when `DEFAULT_COUNTRY=JP`. `status` ∈ `ok | insufficient_data | not_applicable` (ages 30–74, no known CHD). It never changes the level (rule `FRS-01` is informational). `probabilities[]` keeps the same entry for existing clients.

`next_action.type` ∈ `appointment_approval_needed | appointment_requested | appointment_needs_info | appointment_confirmed | none | collect_data | emergency_guidance`. `trend` = 8 weekly points. UI rules: gauge colour by level; always render `score_label` under the number; `probabilities[]` in their own labelled block; `insufficient_data` → progress indicator instead of a number; `emergency` → emergency banner, no gauge.

## 10. Reports (C7)

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | /api/reports?limit=50 | user | – | `ReportSummary[]` newest first |
| GET | /api/reports/{id} | user | – | `ReportSummary + { markdown }` |
| GET | /api/reports/{id}/html | user | – | print-ready HTML (A4 `@page` CSS; browser Print → PDF) |
| POST | /api/reports/{assessment_id}/regenerate?language= | user | – | `ReportSummary + { markdown }` (re-renders in place; share link unchanged) |
| GET | /r/{share_token} | public (no `/api` prefix) | – | HTML with banner "Shared by the patient for clinical review"; `X-Robots-Tag: noindex`; 404 unknown token **or when the owner has no active `share_with_clinic` consent** (revoking it nulls all the user's share tokens) |

`ReportSummary` = `{ id, assessment_id, pathway, level, share_url (string \| null — null until a `share_with_clinic` consent exists and after it is revoked; regenerate assigns one once consent exists), html_url, pdf_url (always null: PDF rendering not built), created_at }`.
Sections: clinician summary (LLM paragraph from structured data only; template fallback), reason for referral (fired rules), observed changes (7-day home BP, device rhythm results, task scores vs baseline), patient-reported context, medications, "Follow-up priority (not a disease probability)", "10-year cardiovascular disease risk (Framingham Risk Score)" with the CCS category and inputs, model scores with dataset provenance, data provenance, preventive measures, references.

## 11. Appointments (booking agent v2 — docs/specs/booking-v2.md)

Flow: `followup_recommended` → the agent plans (discovery, slots, channel detection) and **stops at
`awaiting_approval`** with an `appointment_approval_needed` notification → the user approves → channels
run in order **web → email → phone** (blocked / unconfigured channels are skipped instantly and recorded) →
`requested` (or `confirmed`) → clinic reply (confirm link, inbound e-mail, phone post-call webhook) →
`confirmed` | `needs_user_info` | next candidate clinic | `failed`. **One approval covers the top 3 ranked
candidates** shown on the card: falling through to candidate 2 or 3 (every channel failed, or the clinic
declined) needs no new approval — but only with the times and channels shown on that card: when a
backup's approved times are less than 24 h away, a new card with new times is shown (`awaiting_approval`);
channels that were not usable on the card are never run (`channel_attempts[].reason = "not on the approved
channel plan"`). Only beyond those 3 a new card is shown. `approve`, `decline`, `user-info` and the clinic
confirm POST first claim the status atomically (`UPDATE … WHERE status = <expected>` → transient
`processing`), so a second concurrent request gets 409 and nothing runs twice.
Sandbox by default (`BOOKING_TARGET_MODE=sandbox`): every outbound contact goes to the `SANDBOX_*` targets;
the real clinic details stay visible in `approval.channel_plan[].real` and `channel_attempts[].real`. Website
**detection** always reads the clinic's real site read-only (GET, robots.txt, ≤5 pages, 10 s, ≤1 MB per page,
≤3 same-site redirects, public IPs only — private / loopback / link-local / reserved / multicast addresses are
refused at every hop; only `BASE_URL/mock-clinic/…` in-process), logged as `agent_log` entries of type
`detection`; only execution is redirected. `web_form` needs booking semantics (a name + e-mail / phone field
AND a date / time choice for the visit or an explicit "Book / Request an appointment" submit); search,
contact, newsletter forms are `none`.

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | /api/appointments?status=&limit=50 | user | – | `Appointment[]` newest first |
| GET | /api/appointments/{id} | user | – | `Appointment + { candidates, agent_log }` |
| GET | /api/appointments/{id}/messages | user | – | `AppointmentMessage[]` oldest first (every e-mail / call / web submission, out + in) |
| POST | /api/appointments/{id}/approve | user | – | `Appointment + {…}` → runs the channel plan for candidate 1, falling through to candidates 2 and 3 of the same card without asking again: `requested` / `confirmed`; `awaiting_approval` (new card) when none of the 3 could be reached; `failed` when no candidate is left. 404 not yours, 409 when not `awaiting_approval` or `share_with_clinic` consent revoked |
| POST | /api/appointments/{id}/decline | user | – | `Appointment + {…}` status `cancelled`, `approval.status = "declined"`; nothing sent. 409 when not `awaiting_approval` |
| POST | /api/appointments/{id}/user-info | user | `{ answer?: str ≤1000, accept?: bool }` | answer to `pending_question`: counter-offer → `accept` required. With an e-mail thread (`pending_question.reply_path = "email"`): true → acceptance e-mailed, `confirmed` **only if the e-mail was accepted by the provider** (else stays `needs_user_info` with `send_failed: true`); false → reply + `requested`. Without a reply path (counter-offer made on the phone call): the question asks the user to call the clinic back; `accept: true` means the user booked it themselves → `confirmed` (`via = counter_offer_booked_by_patient`), false → `requested`. Question → `answer` required → agent replies in the e-mail thread (no thread: recorded + the user is told to contact the clinic) → `requested`. 409 when not `needs_user_info` (also for a concurrent second answer), 422 missing field or an answer containing a health-card / OHIP number (context words health card / OHIP / HCN, the Ontario 4-3-3 grouping ± version code; plain phone numbers are fine) |
| POST | /api/appointments/{id}/cancel | user | – | `Appointment + {…}` (status `cancelled`, calendar event deleted, clinic told by e-mail when a request was e-mailed) |
| GET | /api/appointments/{id}/confirm?slot=0..2&token= | public (HMAC token from the clinic e-mail) | – | **never changes state** (mail scanners prefetch links): small HTML page with the clinic, the slot and a "Confirm this time" button (`<form method="post">` to the same URL); "Already confirmed" when that slot is confirmed; same error codes as POST |
| POST | /api/appointments/{id}/confirm?slot=0..2&token= | public (same token) | – | confirms the slot → small HTML page; 200 confirmed (repeat of the same slot is idempotent), 403 bad or expired token (`<unix ts>.<hmac>`, HMAC over appointment id + the **current clinic id** + slot index + issue time, valid 30 days, key `APPOINTMENT_TOKEN_SECRET` or derived from `JWT_SECRET`; links of an earlier clinic stop working after fall-through), 400 bad slot, 404 unknown, 409 when status is not `requested` / `needs_user_info` (e.g. still `awaiting_approval`, cancelled, failed) or another slot is confirmed |
| GET | /api/appointments/{id}/calendar-sync | user | – | the calendar step of a **confirmed** appointment: `{ status: pending\|added\|skipped\|none, mode: google\|manual, event_id?, at?, add_url?, google_url?, ics_url? }`. `mode=google` = Google Calendar connected + `calendar_access` consent (the app's "Add" writes the event); `mode=manual` = the user adds it from `ics_url` (signed .ics download) or `google_url` (pre-filled Google Calendar "new event" link, no OAuth). `status=none` while not confirmed |
| POST | /api/appointments/{id}/calendar-sync | user | `{ decision: add \| skip }` (default `add`) | **the user's calendar approval** (notification B). `add` → creates (or confirms) the Google Calendar event, `approval.calendar = { status: "added", event_id, at, by }`; 409 when not confirmed or when Google Calendar is not connected (the app then offers the .ics). `skip` → `status: "skipped"` (the prompt stops; the .ics stays available). Returns the sync status above |
| GET | /api/appointments/{id}/action?do=approve\|decline&token= | public (signed link from the approval e-mail) | – | **never changes state**: HTML page with the clinic, the times, how it is contacted, what it receives and one button (`<form method="post">` to the same URL); "Already handled" when the card is no longer `awaiting_approval`; 403 "This link has expired" (bad / expired token), 404 unknown |
| POST | /api/appointments/{id}/action?do=approve\|decline&token= | public (same token) | – | the e-mail button: `approve` runs the channel plan exactly like `/approve` (`approval.approved_by = "user:email_link"`), `decline` cancels; HTML result page with a link into the app. 403 bad / expired token, 409 when not `awaiting_approval` |
| GET | /api/appointments/{id}/calendar?token= | public (signed link from the confirmation e-mail) | – | **never changes state**: "Add it to your calendar?" page — `mode=google`: a button that POSTs to the same URL + the .ics link; `mode=manual`: the pre-filled Google Calendar link + the .ics link; "Already in your calendar" when added |
| POST | /api/appointments/{id}/calendar?token= | public (same token) | – | writes the confirmed event to Google Calendar (= `calendar-sync add`, `by: "user:email_link"`); HTML result page. 403 bad token, 409 not confirmed / Google not connected |
| GET | /api/appointments/{id}/calendar.ics?token= | public (same token) | – | the invitation (`text/calendar`, attachment; UTC times, `UID <id>@caresync`, 1-hour `VALARM`); 409 when there is no confirmed time |
| POST | /api/appointments/run/{assessment_id} | admin | – | `Appointment + {…}` — manual trigger (plans → `awaiting_approval`); 409 without `share_with_clinic` consent or for an emergency |
| POST | /api/webhooks/resend/inbound | public, Svix-signed | Resend `email.received` event (raw body) | `{ status: processed\|needs_review\|duplicate\|unmatched\|ignored, email_id?, appointment_id?, matched_by?: reply_token\|sender_review, outcome?, action?, appointment_status? }`. Matching uses the metadata only, before any body is fetched or parsed: **only the reply token** (`book+<token>@…` in to / cc / received_for) can change an appointment. Without a token, a sender equal to the clinic address of exactly ONE open appointment (never the shared `SANDBOX_CLINIC_EMAIL` / `CLINIC_FALLBACK_EMAIL`) → `needs_review`: the message is stored (`classification.outcome = "unverified"`), the user gets an `appointment_needs_info` "please review" notification, the status is unchanged; anything else → `unmatched` (nothing stored). HTML bodies are capped at 200 KB and converted with the stdlib HTML parser. 401 bad / missing / stale (>5 min) signature when `RESEND_WEBHOOK_SECRET` is set; 503 when the secret is unset and `DEV_MODE=false`; 400 invalid JSON. Body fetched from `GET https://api.resend.com/emails/receiving/{email_id}` (the webhook carries metadata only); idempotent on `email_id` |
| POST | /api/webhooks/elevenlabs/post-call | public, HMAC-signed | ElevenLabs Agents webhook (raw body): `post_call_transcription` (or `call_initiation_failure`; `post_call_audio` and other types → `ignored`) | `{ status: processed\|duplicate\|unmatched\|ignored, conversation_id?, appointment_id?, matched_by?: external_ref\|appointment_id, outcome?: confirmed\|counter_offer\|needs_info\|declined\|no_answer\|voicemail\|other, action?, appointment_status? }`. Header `ElevenLabs-Signature: t=<unix>,v0=<hex HMAC-SHA256(secret, "<t>.<raw body>")>`; 401 bad / missing / stale (>30 min) signature when `ELEVENLABS_WEBHOOK_SECRET` is set; 503 when unset and `DEV_MODE=false`; 400 invalid JSON. Matched by `Appointment.external_ref` = conversation id (fallback: the `appointment_id` dynamic variable, only if that conversation is a phone attempt for the current clinic). Transcript stored as an inbound `AppointmentMessage` (`channel=phone`, `external_id=phone:<conversation_id>:result`, idempotent). Outcomes from `analysis.data_collection_results` → same handling as e-mail replies; `no_answer` / `voicemail` / `call_initiation_failure` → phone attempt `failed` → next channel / next candidate (or keep waiting when another channel's request to the clinic is still open) |

`Appointment` = `{ id, assessment_id, status, channel (web\|email\|phone\|null), clinic, proposed_slots: [{start, end}] ×3, chosen_slot, calendar_event_id, approval, channel_attempts, external_ref, pending_question, created_at, updated_at }`.
- `status` ∈ `proposed | awaiting_approval | processing | requested | needs_user_info | confirmed | declined | cancelled | failed` (`processing` = transient, while an approve / answer / confirm runs).
- `clinic` = `{ id, name, specialty (cardiology\|neurology\|memory_clinic\|geriatrics\|family_medicine\|walk_in\|hospital), kind (specialist\|family\|walk_in\|hospital), address, lat, lng, phone, email, website, opening_hours: {"mon": [["09:00","17:00"]], …}, timezone, rating, business_status, distance_km, source (seed\|google_places), fictional, extra: { detected_email?, form_url?, … }, request?: { to, real_to, redirected, subject, email_id, accepted, reply_to, report_url, sent_at }, requests?: { <channel>: request } }` (phone request = `{ channel: "phone", to (E.164 dialled), real_to, redirected, conversation_id, call_sid, provider, accepted, error, sent_at, dynamic_variables, reminder? }`).
- `approval` (the approval card) = `{ status: pending\|approved\|declined, requested_at, approved_at?, clinic: {id, name, specialty, kind, address, distance_km, rating, phone, email, website, source, fictional}, channel_plan: [{ channel, available, reason, target, real, redirected }] (web, email, phone), slots: [{start, end, label (clinic tz), label_user}], shared_fields: { name (legal "First Last", else display_name), first_name, last_name, date_of_birth, sex, phone, email, postal_code, address (full one-line), street, city, province, family_doctor_name, family_doctor_clinic, family_doctor_phone, has_referral, preferred_language, reason_category, preferred_slots, report_url } (only fields with a value; identity fields are given by the phone agent only when the clinic asks, the e-mail carries name + DOB + phone + e-mail + postal code, the web form fills only what it asks for), missing_profile_fields: [field…] (required onboarding fields still empty — the agent cannot answer these and says the patient will provide them; show "complete your profile"), referral_note (specialists: "Canadian specialists usually need a family-doctor referral…"), report_url, finding ({ pathway, pathway_name, headline, points: [rule summary_text ≤3], priority_score, assessment_id } — why the agent ran, structured data from the risk engine; shown as "Why this was flagged"), calendar? ({ status: pending\|added\|skipped, mode: google\|manual, requested_at?, event_id?, at?, by?: user\|user:email_link\|auto } — the calendar step after confirmation, see `calendar-sync`), approved_by? (user \| user:email_link \| auto), agent_note? ({ label: "AI-generated note (not verified)", text } — the planner's free text, never used as the card / notification body, which is built from structured data only), reapproval_reason?, target_mode: sandbox\|live, candidates: [{ rank, clinic_id, summary, clinic, channel_plan, slots, detection, referral_note }] (≤3, all covered by this approval), candidate_ids, active_rank, pending_question?, answered_questions? }`. The top-level `clinic` / `channel_plan` / `slots` always describe the active candidate.
- `pending_question` (while `needs_user_info`) = `{ type: counter_offer\|question, question, slot? {start,end}, reason?, channel (email\|phone — where the clinic's message arrived), reply_path (email\|null — how the assistant can answer), send_failed?, asked_at }`.
- `channel_attempts[]` = `{ ts, clinic_id, channel, status: requested\|confirmed\|skipped\|failed\|declined, reason ("not configured", "login required", "captcha", "sandbox: SANDBOX_CLINIC_PHONE not set", …), external_ref, target (what was contacted), real (the clinic's real detail), redirected, reminder? }`.
- `candidates[]` (detail only) = up to 5 ranked clinic dicts. `agent_log[]` = `{ ts, type (note\|tool\|discovery\|approval_requested\|approved\|channel\|calendar_event\|clinic_reply\|needs_user_info\|user_info\|reply\|confirmed\|next_candidate\|reminder\|next_channel\|failed\|cancelled\|calendar_update…), tool?, input?, output?, is_error?, mode? (llm\|deterministic) }`.

`AppointmentMessage` = `{ id, direction: out\|in, channel, sender, recipient, subject, body, classification?: { outcome: confirmed\|counter_offer\|needs_info\|declined\|other, slot, requested_info, summary, method: llm\|heuristic }, created_at }`.

**User e-mails and their buttons** (`app/services/user_emails.py`, layout `app/providers/email.py:email_shell`, body template `app/templates/user_email.html.j2`): `appointment_approval_needed` → "Please approve: appointment request with {clinic}" (the finding, the clinic, the 3 times in the user's zone, the channel plan, exactly what the clinic receives, missing profile fields, backups, **Approve request / Not now** buttons + "Review in CareSync" deep link `FRONTEND_ORIGIN/appointments?appointment=<id>`); `appointment_requested` → "Request sent to {clinic}" (what happens next); `appointment_confirmed` → "Confirmed: {clinic} · {time}" (when / where + directions, what to bring, the report link, **the calendar step**, `.ics` attached). The buttons are signed links (`booking/common.py:user_action_token`): HMAC over appointment id + action + a nonce that changes with the decision (`approval.requested_at` for approve / decline — a new card invalidates the old buttons; the confirmed slot start for the calendar), valid `USER_ACTION_LINK_DAYS` (7) resp. 60 days, GET shows a page, only POST acts. One e-mail per finding: when the booking agent will produce an approval card in the same run, the `followup_recommended` notification is in-app only (`payload.email = { sent: false, reason: "covered_by_approval_email" }`).
**Calendar approval** (`CALENDAR_WRITE_REQUIRES_APPROVAL=true`, default): the agent never writes to the user's calendar on its own — no tentative hold at request time, and on confirmation `approval.calendar.status = "pending"`; the event is created only by `calendar-sync add` / the e-mail button. `false` restores the old behaviour (tentative hold on request, automatic confirm; `approval.calendar.by = "auto"`). FreeBusy reads for slot planning are unchanged.

Minimal disclosure: a clinic only ever receives `approval.shared_fields` (identity / contact details from onboarding, a generic reason category per pathway — never measurements, diagnoses, medications or health-card numbers) plus 3 slots with confirm links.
Phone call dynamic variables (`clinic.requests.phone.dynamic_variables`, all strings, `""` when missing): `appointment_id, clinic_name, patient_name, callback_phone ("not provided" when missing), reason_category, preferred_slots, clinic_timezone, today, reminder, slot_1..3, slot_1..3_iso` plus the identity fields the agent answers **only when asked**: `patient_first_name, patient_last_name, date_of_birth ("January 15, 1958"), date_of_birth_iso, patient_sex (male|female|other|"prefers not to say"), patient_phone, patient_phone_spoken ("4 1 6, 5 5 5, 0 1 4 2"), patient_email, patient_email_spoken ("r dot macleod at example dot com"), home_address, street, city, province, postal_code, postal_code_spoken ("M 5 H, 2 N 2"), family_doctor_name, family_doctor_clinic, family_doctor_phone, has_referral (yes|no|""), preferred_language`. Anything else (health card, medications, history, insurance) → "the patient will provide it at the visit / call the patient back". Stored phone transcripts have ElevenLabs audio tags (`[happy]`, `[slow]`, …) removed from the assistant's lines. Clinic e-mail: From `booking@<BOOKING_REPLY_DOMAIN>` (else `RESEND_FROM_EMAIL`), Reply-To `book+<token>@<BOOKING_REPLY_DOMAIN>` (else the patient's e-mail); in sandbox the subject starts with `[SANDBOX]` and the body names the real address it was redirected from. Clinic replies: confirmed → `confirmed` + calendar event confirmed **only when the time is one of the approved slots (± 5 min)**; a "confirmed" at any other time is handled as a counter-offer. Counter-offer → auto-accepted only when it arrived by e-mail, is free in the user's calendar and inside opening hours AND the acceptance e-mail was actually sent (the reply always goes to the e-mail thread, even after a follow-up call); a phone counter-offer is never accepted by the assistant (the user is asked to call back); else `needs_user_info`. Question → `needs_user_info` + `appointment_needs_info`; declined → next approved candidate (beyond the 3: new card). Slots use the clinic's time zone (`clinic.timezone`, from the address province / Places `utcOffsetMinutes`, else the user's zone). Follow-up reminders never re-submit a web form (the user is notified instead).
Job `appointment_followups` (daily 09:00, `requested` only): consent revoked → `cancelled`; 3 days after the last contact without a reply → one follow-up per clinic (next untried channel, else a reminder on the same channel); 10 days after the first contact → `failed` + `appointment_failed`. Result `{ checked, reminded, next_channel, failed, cancelled }`.

## 12. Integrations (Google)

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | /api/integrations/google/start | user | – | `{ auth_url, state, fake }` — redirect the browser to `auth_url` (Calendar events + FreeBusy scopes) |
| GET | /api/integrations/google/callback?code=&state= | public (user from signed `state`) | – | 303 → `FRONTEND_ORIGIN/settings?integration=google&status=connected`; 400 bad/expired state |
| DELETE | /api/integrations/google | user | – | 204 (stored tokens deleted; Google revoke endpoint not called) |
| GET | /api/integrations/status | user | – | `{ google: { configured, connected, fake, scopes, expires_at, calendar_consent }, places: { configured, source (google_places\|seed) }, email: { provider, clinic_fallback_email } }` |

## 13. Notifications

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | /api/notifications?unread=true&limit=50 | user | – | `Notification[]` newest first |
| GET | /api/notifications/unread-count | user | – | `{ unread }` |
| POST | /api/notifications/{id}/read | user | – | `Notification` |
| POST | /api/notifications/read-all | user | – | `{ updated }` |

`Notification` = `{ id, kind, title, body, payload, read_at, created_at }`; `kind` ∈ `risk_level_change | followup_recommended | emergency | appointment_approval_needed | appointment_requested | appointment_needs_info | appointment_confirmed | appointment_failed | data_needed | import_finished | weekly_summary`. `appointment_approval_needed` payload: `{ appointment_id, clinic, channel_plan, slots, shared_fields, missing_profile_fields, finding, candidates: [{rank, clinic, channel_plan, slots}] }`; `appointment_confirmed` payload: `{ appointment_id, chosen_slot, slot_label, clinic, calendar: <calendar-sync status> }` (its body ends with "Add it to your calendar? Nothing is added until you say so." while `calendar.status = pending`); `appointment_needs_info` payload: `{ appointment_id, question }`. `payload.email` = `{ sent, reason?, result? }` (`result.attachments` lists attached files, e.g. the `.ics`; `reason: covered_by_approval_email` when the approval e-mail of the same run carries the finding); emergency payloads add `emergency_number`, `contact_email`. Every appointment notification carries `payload.appointment_id`: the frontend bell opens `/appointments?appointment=<id>` (the appointment sheet: approval card, confirmation + calendar step, or status).

User e-mails share one layout (`render_email` / `email_shell`: CareSync header, title, buttons, the Canadian 911 / 811 disclaimer; English only). The approval and confirmation e-mails are described in §11.

## 14. Jobs & admin

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | /api/jobs?limit=50&name= | admin | – | `Job[]` |
| POST | /api/jobs/{name}/run | admin | – | `Job` (runs synchronously; 404 unknown, 409 already running) |
| GET | /api/jobs/scheduler | admin | – | `{ running, timezone, jobs: [{ name, schedule, next_run }] }` |
| POST | /api/admin/seed-demo?run_assessment=false | dev/admin | – | `{ user_id, email, seed: { skipped, metrics, bp, logs }, assessments: [{pathway, level, priority_score}] }` — demo user + planted 35-day trend (idempotent) |
| GET | /api/admin/appointments/{id}/confirm-links | dev/admin | – | `{ appointment_id, status, links: [{ slot, start, url }] }` — the clinic's confirm links (bound to the current clinic; GET shows the page, POST confirms), to play the clinic in a demo |

`Job` = `{ id, name, status (pending\|running\|done\|failed), scheduled_for, started_at, finished_at, error, result, created_at }`.
Registered jobs (APScheduler, `SCHEDULER_ENABLED=true`, timezone `SCHEDULER_TIMEZONE`): `nightly_risk_assessment` (03:00), `process_pending_uploads` (every 10 min), `appointment_followups` (09:00), `weekly_summary_notifications` (Mon 09:00), `cleanup_expired_jobs` (04:30).

## 14a. Mock clinic website (booking-v2 §3)

Fictional clinic site served by the backend for the website booking channel (not under `/api`; HTML, `X-Robots-Tag: noindex`). Variants: `` (standard), `/email-only`, `/login`, `/captcha`, `/health-card`, each with its own `…/book`.

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| GET | /mock-clinic{variant} | public | – | home page (services, hours, contact, "Book online" / mailto for email-only) |
| GET | /mock-clinic{variant}/book | public | – | booking form (standard, captcha, health-card), e-mail instructions (email-only), sign-in wall (login) |
| POST | /mock-clinic{variant}/book | public | form: `first_name, last_name, date_of_birth (YYYY-MM-DD), phone, email, postal_code, reason, appointment_time (ISO from the select), consent=yes, notes?, health_card? (health-card variant)` | 303 → `/mock-clinic/confirmation/{ref}`; 422 form re-rendered with errors (captcha variant: always "CAPTCHA verification failed"); 401 login variant; 405 email-only |
| GET | /mock-clinic/confirmation/{ref} | public | – | "Your appointment is booked", reference `QW-XXXXXX`, `<time datetime=ISO>`; 404 unknown |
| GET | /mock-clinic/login/register | public | – | create-account form (password) |
| GET | /mock-clinic/widget/captcha | public | – | static CAPTCHA placeholder (iframe content) |
| GET | /mock-clinic/admin/bookings | dev/admin | – | `{ count, bookings: [{ reference, variant, status, submitted_at, first_name, last_name, date_of_birth, phone, email, postal_code, reason, slot_start, slot_end, notes, health_card_provided, consent, user_agent }] }` newest first |
| GET | /mock-clinic/admin | dev/admin, or `?key=<ADMIN_SECRET>` | – | HTML "front desk" page: bookings newest first (received, reference, patient, date of birth, phone, e-mail, postal code, reason, appointment, consent, health card provided / not provided — never the number), polls `/mock-clinic/admin/feed` every 3 s and highlights new rows; "Clear demo bookings" button |
| GET | /mock-clinic/admin/feed | dev/admin, or `?key=` | – | `{ count, rows: [{ reference, received, name, dob, phone, email, postal_code, reason, slot, consent ("yes"\|"no"), health_card ("provided"\|"not provided"), variant }] }` newest first, display-formatted (Toronto time); no notes / user agent |
| POST | /mock-clinic/admin/clear | dev/admin, or `?key=`; header `X-Front-Desk: 1` | – | `{ cleared: n }` — deletes every mock-clinic booking (memory + `bookings.json`); 400 without the header (blocks plain cross-site form posts), 403 bad / missing secret outside dev mode |

`dev/admin` here = `DEV_MODE=true`, or the `X-Admin-Secret` header; `?key=` is compared in constant time
(handy for opening the front desk in a teammate's browser on a non-dev deployment; it lands in browser
history / proxy logs, so use it only with a demo secret).

## 15. Health

| Method | Path | Auth | Response |
|---|---|---|---|
| GET | /api/health | public | `{ status: "ok", version, providers: { llm, embeddings, stt, email, calendar }, db: "sqlite"\|"postgres", scheduler: "running"\|"disabled" }` |

`providers` values: `llm` anthropic\|fake, `embeddings` voyage\|fake, `stt` openai\|fake, `email` resend\|fake, `calendar` google\|fake. No secrets are returned.
