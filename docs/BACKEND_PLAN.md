# Backend Plan — Doctorless Check-up / Health Advocate (HackTheValley)

## Build status (2026-10-17 overnight)

Legend: **Built** = implemented and covered by tests with fake providers. **Built-needs-key** = the real
integration code exists but has never been run against the live service (no keys on the build machine).
**Not built** = not implemented. Full suite: `cd backend && .venv/bin/pytest -q` (green); end-to-end demo test:
`tests/test_e2e_demo.py`; live smoke: `scripts/demo_smoke.py` (10/10 against a local server). Morning notes: `docs/HANDOFF.md`.

| Board item | Status | Notes |
|---|---|---|
| A2/A3 Decision rules (BP, RHY, SUITA, COG, PD, GEN, EMG) | Built | `app/ml/rules.py` + engine; fires / not fired / insufficient tests per rule; demo replay → cardio `[BP-02, RHY-01]` score 70 |
| A2 Story recall task (3-step battery + delay) | Built | `app/ml/story_task.py`, `/api/tasks/story/*`; semantic-recall score is experimental, no validated thresholds |
| A2 3-word recall, finger tapping, sustained vowel | Built | `/api/tasks/word-recall`, `/tapping`, `/vowel`; vowel sessions aggregated per day (median) for PD-03 |
| A2 PD screener | Built | `/api/screener/pd`; one id set (content-bank ids), legacy ids accepted |
| A2 Suita score | Built | `app/ml/suita.py`; context only, never drives the level |
| A4 PD vowel model training | Built | `ml_training/train_pd_vowel.py`; UCI, subject-grouped CV, pooled OOF AUC 0.74 (95% CI 0.53–0.91); served from `backend/models/` |
| A4 HF Hub push | Not built (run) | `push_to_hub` code path exists but was never run (no `HF_TOKEN`); the app loads the local model folder |
| A4 Gated models (ADReSS picture description, mPower tapping) | Not built | access not requested yet (see HANDOFF) |
| B Database (models, SQLite/Postgres, pgvector) | Built | tables created at startup; `CREATE EXTENSION vector` on Postgres (Postgres path untested here) |
| B Alembic migrations | Not built | `init_db` create_all only |
| C1 Metrics, BP, rhythm, daily log, observations | Built | `/api/metrics*`, `/api/logs/daily`, `/api/observations` |
| C1 Apple Health export import | Built | background task, job status `pending → running → done / failed` |
| C1 iOS Shortcut | Built | `docs/shortcut.md` + device tokens; not tried on a real iPhone |
| C1 Document / BP-photo extraction | Built-needs-key | Claude vision/extraction; fake LLM in tests |
| C2 Chat (text) + extraction + red-flag short-circuit | Built | red flags are keyword-based |
| C2 Voice chat (Whisper) | Built-needs-key | OpenAI Whisper path unverified; FakeStt in tests |
| C2 Daily check-in | Built | `/api/chat/checkin` |
| C3 RAG (corpus ingest, retrieval, cited answers) | Built | fake embeddings by default; Voyage path Built-needs-key |
| C3 LLM answers (Anthropic) | Built-needs-key | `AnthropicLlm` unverified against the live API |
| C4 Risk service, dashboard cards, history | Built | `/api/risk/*`, `/api/dashboard` (spec §11.4 shape) |
| C5 Scheduler (APScheduler) + job registry | Built | 5 jobs; start path exercised in the live smoke run |
| C6 Booking agent (tool loop + deterministic fallback) | Built | seed clinics; Claude tool loop Built-needs-key |
| C6 Google Calendar OAuth / FreeBusy / events | Built-needs-key | fake calendar without `GOOGLE_CLIENT_ID/SECRET` |
| C6 Google Places clinic search | Built-needs-key | falls back to `data/seed/clinics_tokyo.json` |
| C6 Clinic confirm link | Built | HMAC link; `/api/admin/appointments/{id}/confirm-links` for demos |
| C6 Resend inbound webhook | Not built | confirm link only |
| C6 Twilio voice call | Not built | stretch |
| C7 Report (Markdown + HTML + public share link) | Built | `/r/{token}`, print CSS |
| C7 PDF rendering (weasyprint) | Not built | `render_pdf` raises; use browser Print → PDF |
| C8 Notifications + e-mail | Built | e-mail via Resend is Built-needs-key; FakeEmail logs otherwise |
| D Auth (signup/login JWT, device tokens, dev X-User-Id) | Built | |
| E Consent, audit log, guardrails, export/delete | Built | `DELETE /api/me` removes rows (incl. OAuth credentials) and stored files; Google revoke endpoint not called |
| E Rate limiting | Built | slowapi-based dependency on chat/uploads/tasks, 30/min default |
| E Field encryption (Fernet) | Built | `FIELD_ENCRYPTION_KEY` required when `DEV_MODE=false` |
| E Private storage bucket (Supabase Storage) | Not built | local disk (`STORAGE_DIR`); `SupabaseStorage` stub |
| F Docker + Render blueprint | Built | `backend/Dockerfile`, `render.yaml` at repo root (`rootDir: backend`); never deployed |
| F CI (GitHub Actions) | Not built | |

---

## Context

A medical-advocate app: it accumulates a user's health data (wearables, voice chat with the AI, manual daily logs, manual blood-pressure readings, doctor's notes), runs company-informed **assessment tasks, device results and decision rules** (plus one experimental trained model) in the cloud for three pathways (cognitive decline / Alzheimer's, stroke risk / AF / heart, Parkinson's), and when a concerning pattern appears it produces an evidence-backed, RAG-cited doctor report and autonomously books an appointment. The AI advocates; it never diagnoses.

Agreed constraints:
- Python + FastAPI backend (skeleton exists in `backend/`), React/Vite frontend owned by the other teammate. 36 hours, 2 people.
- Chatbot + RAG, risk model, wearable ingestion and the booking agent are all **real** and run **server-side in the cloud**.
- The **assessment layer is the main function and comes first**. Models are trained only where a real public dataset matches the task we run (Hugging Face `datasets` + Hub); everything else uses device results, published protocols and transparent rules. The data model is derived from the assessment inputs (A5).
- Plan is organised by the user's task board: Backend → Database → Auth → Security → Deployment (Frontend tasks are listed only where the backend must supply an endpoint).

Existing code to build on, not replace: `backend/app/main.py` (FastAPI + CORS, routers under `/api`), `app/core/config.py` (pydantic-settings), `app/rag/` (`chunking.py` real; `embeddings.py`, `store.py`, `pipeline.py` are stubs), `app/api/routes/chat.py`, `app/schemas/chat.py`, `app/ml/` (empty), `docs/API.md` (contract: write endpoints there first), `tests/test_health.py`.

Honest scope note: this is more than one backend person fits in 36h. Everything is ordered so the demo works at every cut line; cut decisions are called out per task. Decide cuts at hour ~24.

---

## Phase A — Assessment Models and Decision Rules (do this first)

### A0. Principles (from the company research + critique)
Four separate layers, never merged into one "risk score":
1. **Assessment models** produce a *task-specific* result (a recall score, a vowel-phonation classifier output, a device-generated ECG label, a 7-day home BP mean).
2. **Decision rules** decide the follow-up for each finding independently. Each rule has an id, a threshold, a rationale, the source it is based on, and is reviewed on its own. Rules never AND-together independent findings so that one suppresses another.
3. **RAG** explains relevant evidence and limitations. It does not validate the assessment.
4. **Booking agent** executes the agreed follow-up. Emergency red flags (chest pain, FAST stroke signs, syncope, suicidal ideation) are handled by a separate emergency-guidance path and are never sent to the booking agent.

Per-pathway output levels: `insufficient_data | invalid_measurement | no_finding | watch | followup_recommended | emergency`. A missing or failed assessment is never `no_finding`.

Every assessment record carries: `task_type`, `protocol_version`, `language`, `recording_quality` (SNR, duration, clipping), `missing_inputs[]`, `model_version`, `baseline_n_days`.

Honest positioning for the pitch: **"a company-informed prototype that combines specialized health signals with coordinated medical follow-up."** Only the Parkinson's vowel classifier is a model we train, and it is labelled experimental.

### A1. What each company actually does (research summary, 2026-10-17)

| Company / study | What the user does | What is measured | Output and action |
|---|---|---|---|
| **Novoic Storyteller** (Alz & Dem 2024, PMID 39234647; ADNI4) | Listens to a short story, retells it immediately (×2) and after a delay; remote, smartphone, unsupervised, English | Transcribed; **G-match** = proportion of story content recalled (semantic match), plus lexical, temporal, acoustic features | Text-similarity models best: AUC 0.71–0.84 across cohorts (AMYPRED n=101; ADNI4 remote n=426, in-clinic n=57). Aβ+ MCI vs CU: AUC 0.83 in-clinic / 0.92 remote (n=84/46). Trial-screening tool, not a diagnosis |
| **Linus Health DCR** | Clock drawing on tablet + 3-word delayed recall, ~3 min, primary care | *Process* metrics (how the drawing is made: latency, pauses, spatial layout) + recall | Score 0–5 (0–1 impaired, 2–3 borderline, 4–5 not indicative); outperformed MMSE in Bio-Hermes (n=706); FDA-registered |
| **Apple Watch irregular rhythm notification** | Nothing; passive PPG checks when still | Pulse irregularity suggestive of AF, confirmed over multiple readings | Notification → "talk to your doctor" if not already diagnosed. Not continuous, misses AF, not for <22 or diagnosed AF, cannot detect heart attack |
| **Apple Watch ECG app** | 30 s single-lead ECG on demand | Rhythm classification | `Sinus rhythm / AFib (50–150 bpm) / High HR / Low HR / Inconclusive / Poor recording`; exportable PDF |
| **OMRON HCR-7800T + OMRON connect** (Japan) | Morning and evening home BP with simultaneous ECG; device sold on physician instruction (特定保守管理医療機器) | BP, pulse, ECG; app analyses rhythm into 6 result patterns incl. 「心房細動の可能性」 | PDF export for doctor. Nationwide study: 3,820 hypertensive adults ≥60, twice-daily for 3 months → 220 hidden AF (5.8%), detection rising with duration |
| **Cordio HearO** | Daily recording of ~5 fixed sentences on a smartphone, in diagnosed CHF | Speech features vs the patient's **own baseline** (fluid-related changes) | Alert to clinician. Interim: 80% sensitivity (44/55 events), ~3 weeks lead, 253 pts; pivotal DETECT-HF (n=500) pending. Diagnosed population only |
| **StrivePD / Rune Labs** | Online symptom screener (new shaking, stiffness, slowness, clumsiness) → free iPhone+Watch app → passive tracking → possible telehealth specialist evaluation | Tremor (3–7 Hz) and dyskinesia via **Apple Movement Disorder API** (FDA 510(k) 2022); plus Apple Health sleep, mobility, falls; symptom and medication logs | Daily tremor/dyskinesia patterns, monthly reports, clinic portal. API needs an Apple entitlement and is for diagnosed patients only |
| **Voice-based PD detection review** (Bioengineering 2025, PMC12649940, 69 studies) | Mostly **sustained vowel /a/**; also reading, DDK, spontaneous speech | Jitter, shimmer, HNR, MFCC, RPDE, DFA, PPE; or wav2vec2/HuBERT embeddings | Accuracies 68–100% but mostly small, internal validation; <15% external validation; accuracy inflated by oversampling; no screening of healthy populations shown |

### A2. Pathway designs
Detailed protocols, score sheets and rule definitions: `docs/specs/phase-a-assessments.md`.


**Cognitive (Novoic- and Linus-informed)**
- *Primary — Story Recall Task (own implementation, task prototype route).* The app reads a ~100-word story aloud (TTS), user retells it twice immediately and once after a ~5-minute delay inside the chat. Transcribe → `recall_match` = proportion of story content units recovered (embedding-based semantic matching of propositions), plus speech rate, pause ratio, word-finding pauses. Stored per attempt with language and recording quality. Output is a **task score and change vs the user's own baseline**. Claim: task performance; disease-risk thresholds are unvalidated and are not presented as such. Japanese stories are written by us; no Japanese validation exists for any of these companies either.
- *Secondary — 3-word delayed recall + response-latency process metric* inside the daily check-in (Linus-informed, no clock drawing).
- *Optional research model (gated).* If DementiaBank approves access to ADReSS (156 participants, Cookie Theft **picture description**, English), implement the same picture-description task in the app and train a text-feature classifier with participant-level splits. Report AUC on the ADReSS test split only; English-only; labelled experimental. Not a fallback for the primary task.
- *Not doing:* free-conversation cognitive scoring (CogniTalk's proposition; we have no equivalent evidence). Conversation audio is stored and features are logged, but it produces no cognitive level.
- Decision rules: `COG-01` recall_match drops ≥ X% below personal baseline on ≥3 of the last 5 attempts (baseline needs ≥5 attempts, else `insufficient_data`) → `watch`; `COG-02` user- or family-reported functional change (missed appointments, getting lost, repeating questions) → `followup_recommended` regardless of task score.

**Cardio — three explicit modules (Apple/OMRON/HearO-informed)**
- *Module 1 — Rhythm findings (device results, no model).* Store device-generated results as first-class records: Apple irregular-rhythm events, Apple ECG classifications, OMRON connect result labels (from PDF or screen photo via Claude vision), manual irregular-pulse indicator. Rule `RHY-01`: any "possible AF / AFib" result in a user without a known AF diagnosis → `followup_recommended` (mirrors Apple's and OMRON's own instruction). `RHY-02`: ≥2 inconclusive/poor recordings → `invalid_measurement` with re-record guidance. No palpitations requirement.
- *Module 2 — Home BP protocol (OMRON-study-informed).* Protocol: morning (within 1 h of waking, before meds) and evening readings, seated, 2 readings each. Weekly mean of valid readings. Rules follow the Japanese Society of Hypertension home-BP thresholds: `BP-01` 7-day mean ≥135/85 → `watch`; `BP-02` ≥145/90 or ≥2 weeks ≥135/85 → `followup_recommended`; `BP-03` any reading ≥180/110 with symptoms → `emergency`; fewer than 8 valid readings in 7 days → `insufficient_data`.
- *Module 3 — Long-term risk (published calculator, not retrained).* Implement a **named Japanese risk equation** (Suita score for 10-year CHD; inputs age, sex, BP, cholesterol, diabetes, smoking, CKD) as a transparent calculator with its eligibility and population limits stated. Shown as context in the doctor report, never as a trigger on its own. Framingham/Kaggle retraining removed. Wearable HRV and resting HR are **not** inputs to this module; they feed only the personal-baseline change detector below.
- *Not doing:* training an AF waveform model (PhysioNet 2017 is 300 Hz AliveCor; Apple exports 512 Hz; cross-device validation not feasible in 36h). Device results are used instead.

**Parkinson's (StrivePD- and review-informed)**
- *Screener questionnaire* (StrivePD-style, extended with prodromal markers from research criteria): new shaking at rest, stiffness/slowness, clumsiness or small handwriting, reduced smell, acting out dreams, constipation, soft voice. Scored as count of positives; `PD-01` ≥2 motor items or 1 motor + 1 prodromal → `watch` and prompts the tasks below.
- *Passive mobility (Apple Health, no model).* Walking speed, step length, double-support %, walking asymmetry, walking steadiness from Apple Health export/Shortcut. `PD-02`: sustained decline vs personal baseline (≥14 days baseline, else `insufficient_data`) → `watch`. The Apple Movement Disorder API is not usable (entitlement + diagnosed patients only), so this is our passive-motor substitute.
- *Finger-tapping task* (15 s per hand, browser timestamps): interval mean, CV, decrement slope, asymmetry. Task score and personal change only; no trained model unless mPower access arrives (then participant-level split, labelled experimental).
- *Experimental voice classifier — the one model we train.* Task: **standardized sustained vowel /a/ for 5 s** matching the UCI Parkinson's protocol (Little et al.; 195 recordings, 31 people, 23 PD). Features computed with `parselmouth`/Praat: jitter, shimmer, HNR, F0 stats; RPDE/DFA/PPE reimplemented with documented differences from the original extraction. Train gradient boosting with **participant-grouped** CV; feature selection and calibration inside the folds; report AUC with CI. UCI Telemonitoring (42 PD-only, UPDRS) is **not** combined; optional separate severity-regression experiment. Pushed to the HF Hub with a model card stating: 31 subjects, English speakers, sustained vowel only, no external validation, experimental. `PD-03`: classifier probability above threshold on ≥2 separate sessions **and** at least one positive screener/mobility/tapping finding → `watch` (never `followup_recommended` on voice alone). Conversational speech is stored separately and never scored by this model.

**Personal-baseline change detection (HearO-informed, all pathways)**
`app/ml/baseline.py`: per metric/task, rolling 30-day baseline (median, MAD); flag when the last 3–7 days deviate persistently. Requires ≥14 days (`insufficient_data` otherwise). Labelled as "change worth checking", not a disease signal; HearO's evidence is in diagnosed CHF and does not transfer.

### A3. Decision-rule table and evaluation
- `app/ml/rules.py`: each rule = `{id, pathway, inputs, condition, level, rationale, source_ids, review_status}`. Rules evaluated independently; the pathway level is the max over fired rules, with `insufficient_data` reported alongside when inputs are missing.
- Evaluation we can actually do in 36h: unit tests per rule with synthetic series (fires / doesn't fire / insufficient data); a replay harness that runs the rules over the seeded demo user; the PD classifier's grouped-CV AUC on UCI. We state plainly that the rule set as a whole is unvalidated.
- Emergency path: `EMG-*` rules (red-flag symptoms from chat/log, BP-03) return emergency guidance immediately in the chat response and notify the emergency/family contact if consented; they do **not** create a booking.

### A4. Training pipeline (`backend/ml_training/`)
- `train_pd_vowel.py`: HF `datasets` loader for UCI Parkinson's CSV; participant-grouped CV; `sklearn`/`xgboost`; calibration in-fold; `push_to_hub("hackthevalley/pd-vowel-experimental-v1")` with model card. Optional `train_adress.py` (gated) and `train_mpower_tapping.py` (gated).
- Shared feature code in `app/ml/features/` (`vowel.py`, `speech.py`, `tapping.py`, `mobility.py`); serving loads the Hub model at startup.
- Pitch deliverable: dataset/size/AUC table plus the limitations above.

### A5. Data inventory — what we actually receive (drives the DB)

**1. Wearables (Apple Health export.zip / iOS Shortcut; Fitbit stretch)**
- Activity: steps, distance, active energy, exercise minutes
- Heart: resting HR, walking HR average, HRV (SDNN), high/low HR events, **irregular rhythm events**, **ECG recordings + Apple classification label**, VO2max
- Respiratory/oxygen: SpO2, respiratory rate (sleep)
- Sleep: in-bed, asleep, stages, wake count
- **Mobility**: walking speed, step length, double-support %, walking asymmetry, walking steadiness
- BP if a connected cuff syncs to Apple Health; weight/BMI

**2. Voice interaction with the AI**
- Audio → transcript; content → `Observation`s (symptoms, functional changes, medications, mood, sleep complaint)
- Acoustic/linguistic features logged with recording quality (stored, not scored for disease)
- Structured tasks: story recall (×2 immediate + delayed), sustained vowel /a/ 5 s, 3-word delayed recall, optional picture description (gated)

**3. Manual daily logging / check-in**
- Sleep quality, fatigue, mood, stress (1–5); PD screener items; yes/no: tremor, stiffness, fall, memory lapse, confusion, headache, dizziness, palpitations, chest discomfort, breathlessness, vision/speech disturbance; medication adherence; caffeine/alcohol; exercise; finger-tapping when prompted

**4. Manual blood pressure / device results**
- Systolic, diastolic, pulse, irregular-pulse flag, measured_at, session (morning/evening), arm, position, pre/post medication; validity flags
- Device rhythm result records: source (apple_irn | apple_ecg | omron_connect | manual), label, recorded_at, file/PDF/photo url

**5. Static profile**
- Age, sex, height, weight, smoking, diabetes, CKD, known hypertension / AF / heart disease / PD / dementia diagnoses, cholesterol if known, family history, medications, address, language, timezone, emergency/family contact + consent

**6. Doctor's notes / documents**
- PDF/image/audio → diagnoses, medications, follow-ups, lab values → `UploadedDocument.extracted` + `Observation`s

---

## Phase B — Database (derived from A5)

Postgres with `pgvector` (Supabase or Neon free tier), SQLAlchemy 2 + Alembic. One `DATABASE_URL` for local and cloud. One migration.

- **Database Setup**: `app/db/session.py`, `base.py`, Alembic, `CREATE EXTENSION vector`.
- **User Profiles**: `User` (auth fields) + `Profile` (all of A5 §5).
- **Health Data Storage**:
  - `MetricSample` (user_id, type enum covering A5 §1 and §4, value, unit, recorded_at, source enum: apple_health | shortcut | fitbit | manual | omron_photo; unique on user/type/recorded_at/source)
  - `DailyLog` (user_id, date, the A5 §3 scalar fields and flags as columns)
  - `Observation` (user_id, kind: symptom | functional_change | medication | lifestyle | mood; label, severity, onset, free_text, source: chat | log | document, source_id)
  - `AssessmentTask` (user_id, task_type: story_recall | sustained_vowel | word_recall | finger_tap | picture_description | free_speech; protocol_version, language, recording_quality json, raw_url/json, features json, model_scores json)
  - `DeviceRhythmResult` (user_id, source: apple_irn | apple_ecg | omron_connect | manual; label, recorded_at, file_url)
  - `ScreenerResponse` (user_id, screener: pd | cardio; answers json, score, created_at)
  - `UploadedDocument` (user_id, kind, file_url, extracted json)
- **Chat History**: `ChatMessage` (user_id, role, content, audio_url, sources json, created_at)
- **Risk Assessment History**: `RiskAssessment` (user_id, pathway, level enum: insufficient_data | invalid_measurement | no_finding | watch | followup_recommended | emergency, fired_rules json, missing_inputs json, model_versions json, evidence json, explanation, created_at); `Report` (assessment_id, markdown, pdf_url, share_token)
- **Appointment Records**: `Appointment` (user_id, assessment_id, clinic json, proposed_slots, chosen_slot, status, calendar_event_id, channel, agent_log json); `Notification`; `OAuthCredential`
- RAG: `ResearchSource`, `ResearchChunk` (embedding `vector(1024)`)
- Ops: `Job` (name, scheduled_for, started/finished, status, error); `AuditLog` (LLM calls, data access)

---

## Phase C — Backend services (your "Backend" column)

### C1. Health Data Processing + User Health Tracking
`app/services/ingest_service.py`, `app/api/routes/metrics.py`, `uploads.py`, `logs.py`
- `POST /api/metrics` batch push (idempotent). `POST /api/uploads/apple-health` streams `export.xml` with `iterparse` → `MetricSample`s; parses `electrocardiograms/*.csv` → `EcgRecording`. Runs as a background job.
- iOS Shortcut documented in `docs/shortcut.md`: "Find Health Samples (today) → POST /api/metrics" — the live-wearable path with no native app.
- `POST /api/logs/daily` (A5 §3), `POST /api/metrics/bp` (A5 §4), `POST /api/uploads/bp-photo` (Claude vision → same path), `POST /api/uploads/document` (doctor note / audio → extraction).
- `GET /api/metrics/summary?days=30` → daily aggregates + baseline stats (feeds Frontend "Health Data Visualization").
- `app/ml/features/`: `speech.py`, `tapping.py`, `cardio.py`, `baseline.py` — shared with training.

### C2. Chat bot (text + voice → health tracking data)
`app/services/chat_service.py`
- `POST /api/chat` (text) and `POST /api/chat/voice` (audio → Whisper transcript → same pipeline; also stores acoustic features as an `AssessmentTask(free_speech)`).
- Prompt = recent messages + compact health-context block (7-day vs 30-day metrics, open observations, latest risk per pathway) + RAG hits. System prompt forbids diagnosis, requires citations, speaks as advocate.
- After each user turn: Haiku structured-extraction call → `Observation`s. This is "user types/speaks → goes into health tracking data".
- `GET /api/chat/checkin` returns adaptive daily questions (asks about what's missing or what a pathway on `watch` needs). `GET /api/chat/history`.
- Task prompts in chat: `GET /api/tasks/story`, `POST /api/tasks/speech`, `POST /api/tasks/tapping`, `GET /api/tasks/history`.

### C3. RAG
Replace stubs in `app/rag/`: `embeddings.py` → Voyage AI (`voyage-3`) with batching, keep `embed(list[str])` signature; `store.py` → pgvector `add`/`search(vector, k, pathway)`; `ingest.py` + `scripts/ingest_corpus.py` for ~15 curated sources (AHA/ESC hypertension & AF guidelines, Apple Heart Study, JSH home-BP guideline, Suita score paper, OMRON HCR-7800T AF study, Novoic Storyteller + Linus DCR papers, 2025 voice-PD systematic review, MDS PD criteria, insomnia guideline). `pipeline.py` → retrieve k=6 → Claude Sonnet answer with `[n]` citations → `{answer, sources[{id,title,url,snippet}]}`. Update `docs/API.md`.

### C4. AI Risk Assessment Model (serving)
`app/services/risk_service.py`, `app/ml/models.py`
- Startup: download models from HF Hub, cache in memory.
- `assess_user(user_id)`: build pathway inputs from DB → run assessment models/calculators → evaluate decision rules independently (`app/ml/rules.py`) → `RiskAssessment` rows with `fired_rules`, `missing_inputs` and `evidence[]` (metric ids, task ids, device-result ids, observation ids, research chunk ids). Sonnet writes `explanation` from evidence only, including limitations.
- `GET /api/risk/latest`, `GET /api/risk/history`, `POST /api/risk/run` (demo trigger).
- Level transition → Notification; `followup_recommended` → Report + booking agent (after consent check). `emergency` → immediate emergency guidance + contact notification, **never** the booking agent. `insufficient_data` → check-in asks for the missing input.

### C5. Background Tasks / Cloud AI
`app/jobs/scheduler.py`: APScheduler in-process. Nightly `assess_all_users`, hourly `process_pending_uploads`, `appointment_followups`. Every run recorded in `Job`. No Celery/Redis.

### C6. AI Agent for booking appointment (tool calling + workflow orchestration)
`app/agents/booking_agent.py`: Claude Sonnet tool-use loop.
- Tools: `find_clinics` (Google Places, specialty by pathway), `get_free_slots` (Google Calendar FreeBusy), `send_booking_request` (Resend email with report share link + proposed slots), `create_calendar_event` (tentative), `notify_user`.
- Orchestration: assessment → report → agent → `Appointment(status=requested)` → clinic confirm link (`POST /api/appointments/{id}/confirm`) or Resend inbound webhook → `confirmed`, calendar event finalised. Every tool call appended to `agent_log` (Frontend "Medical Appointment Interface" shows it).
- `GET /api/appointments`, `POST /api/appointments/{id}/cancel`, Google OAuth `GET /api/integrations/google/start|callback`.
- Cut line: seed 3 clinics instead of Places; confirm link instead of inbound webhook. Calendar + email stay real. Twilio voice call is stretch.

### C7. Report generation
`app/services/report_service.py`: Sonnet composes clinician-facing Markdown (reason for referral, observed changes with numbers/dates, model scores with dataset provenance, patient-reported context, medications, references) → PDF (`weasyprint`) → `Report` + public `GET /r/{share_token}`.

### C8. Notification
`Notification` rows + `GET /api/notifications`, `POST /api/notifications/{id}/read`; email to user via Resend on level change and booking updates. `GET /api/dashboard` — one call for the Home page (latest risk per pathway, metric summaries, pending appointments, unread notifications).

---

## Phase D — Auth
- **Sign Up / Login**: email + password, `passlib[bcrypt]`, `POST /api/auth/signup`, `/login`. 
- **Session Management**: JWT access token (`python-jose`), 7-day expiry, `Authorization: Bearer`. `get_current_user` dependency in `app/core/auth.py`. Shortcut uses a long-lived device token (`POST /api/auth/device-token`).
- **User Authorization**: every query scoped by `user_id` from the token; report share links use unguessable tokens; admin-only `POST /api/risk/run` behind an `ADMIN_SECRET`.

## Phase E — Security
- **Data Encryption**: TLS via host; Postgres encryption at rest (Supabase/Neon default); uploaded files in private storage bucket with signed URLs; OAuth tokens encrypted with `cryptography.Fernet` (`FIELD_ENCRYPTION_KEY`).
- **API Security**: rate limit `/api/chat*` and uploads (`slowapi`), file-type/size validation, CORS locked to `FRONTEND_ORIGIN`, secrets only via env.
- **User Consent Management**: `Consent` table (purpose: data_processing | ai_assessment | calendar_access | share_with_clinic; granted_at, revoked_at); consent checked before assessment and before the agent emails anyone. `POST /api/consent`.
- **Health Data Access Controls**: user-scoped queries, `AuditLog` on every read of health rows and every LLM call; report access only via token.
- **Medical AI Safety Guardrails**: system prompts forbid diagnosis/treatment instructions; output schema carries a mandatory disclaimer; red-flag detector (chest pain, stroke FAST symptoms, syncope, suicidal ideation) is the `EMG-*` rule path in A3 and short-circuits to emergency guidance, bypassing booking; explanations may only reference evidence rows and must state limitations; model cards state cohort limits.
- **Data Deletion / Privacy Controls**: `DELETE /api/me` cascades all rows + storage objects + revokes OAuth; `GET /api/me/export` (JSON dump).

## Phase F — Deployment & Infrastructure
- **Backend Deployment**: Render/Railway web service from `backend/` (Dockerfile; `uvicorn`, 1 worker so APScheduler runs once). Deploy in the first 3 hours so everything after is tested in the cloud.
- **Database Hosting**: Supabase (Postgres + pgvector + Storage) or Neon + local-disk uploads.
- **Frontend Deployment**: Vercel/Netlify; `VITE_API_URL` points at backend; CORS updated.
- **Environment Variables / Secrets**: `DATABASE_URL`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `OPENAI_API_KEY` (Whisper), `HF_TOKEN`, `GOOGLE_CLIENT_ID/SECRET`, `GOOGLE_MAPS_API_KEY`, `RESEND_API_KEY`, `JWT_SECRET`, `FIELD_ENCRYPTION_KEY`, `ADMIN_SECRET`, `BASE_URL`, storage keys. All in `.env.example`.
- **CI/CD (optional)**: GitHub Action running `pytest` + `ruff`; host auto-deploys on push to `main`.

---

## Execution order (36h, one backend person)

| Hours | Work | Demoable result |
|---|---|---|
| 0–1 | Request DementiaBank/ADReSS + mPower access (gated extras); download UCI Parkinson's; create HF org; cloud DB + deploy skeleton (F) | `/api/health` live in cloud |
| 1–6 | **A4**: train PD sustained-vowel classifier (grouped CV) → Hub; feature code in `app/ml/features/`; story-recall scorer; Suita calculator; decision-rule table skeleton | Experimental model on Hub, rule table with tests |
| 6–8 | **B**: migration from A5 inventory; seed demo user | DB live |
| 8–13 | **C1**: metrics push, BP entry, daily log, Apple Health parser, Shortcut doc | Real wearable data in cloud |
| 13–17 | **C3 + C2**: RAG corpus, chat with extraction, voice chat | Cited chat that creates observations |
| 17–21 | **C4 + C5**: serving models, fusion rules, scheduler, `/api/risk/run` | Risk levels with evidence |
| 21–24 | **C7 + C8**: report PDF, dashboard, notifications | Doctor report link |
| 24–29 | **C6**: booking agent (Calendar + email real) | Appointment requested + calendar event |
| 29–32 | **D + E**: JWT auth, consent, guardrails, delete/export | — |
| 32–35 | **Gated extras** (ADReSS picture-description model, mPower tapping) only if access arrived; seed planted-trend demo data | Full chain on demo user |
| 35–36 | Tests, `docs/API.md`, pitch metrics table | — |

Cut order if behind: gated extras → delayed-recall condition of story task → Apple Health zip (keep Shortcut + manual) → Places → auth signup (keep seeded users + JWT).

## Verification (end-to-end)
1. `pytest` green (rule unit tests fire / don't fire / insufficient_data); training script reproduces the reported grouped-CV AUC from `data/raw/`.
2. `POST /api/chat/voice` with a sample recording → transcript, acoustic features stored, an `Observation` created from "I've woken up tired three days in a row".
3. Shortcut run on iPhone → rows in `MetricSample`; `GET /api/metrics/summary` reflects them.
4. Tapping + story-recall tasks → `AssessmentTask` rows with features and model scores.
5. Seed demo user with planted trend (rising home BP + an OMRON 'possible AF' result) → `POST /api/risk/run` → `RiskAssessment(level=followup_recommended, fired_rules=[BP-02, RHY-01])` with evidence + citations → report PDF at `/r/{token}` → `Appointment(requested)`, tentative Google Calendar event, booking email in clinic inbox, user notification.
6. Clinic confirm link → `confirmed`, calendar event finalised.
7. All of the above against the deployed cloud URL; `Job` table shows the nightly run registered.

## After approval
- Copy this plan into the repo as `docs/BACKEND_PLAN.md` so the team sees it, and save a memory pointer so it is recalled in later sessions.
- Next discussion: the risk-assessment model in detail (dataset access status, feature sets, fusion thresholds, evaluation).

---

## Revision 2026-10-17 (agreed with the user)

- **Region: Canada only, English only** (aging-population prevention framing). Emergency guidance
  911 / Health Link 811 / 9-8-8 crisis line; timezone America/Toronto; seed clinics in Toronto.
  The Suita score is Japan-specific: for Canada the long-term context calculator is the Framingham
  Risk Score as used by the Canadian Cardiovascular Society; Suita kept in code, off for CA.
  Home BP thresholds 135/85 match Hypertension Canada.
- **Providers:** Gemini (LLM: chat, extraction, booking agent, vision), ElevenLabs (STT with word
  timestamps + TTS for story audio, cached per story), local embeddings. Anthropic/OpenAI/Voyage
  remain as optional alternatives; fakes for tests.
- **RAG rebuilt on the GymSync design:** parent sections + ~380-word child chunks, local
  `nomic-embed-text-v1.5` (768-d, fastembed/ONNX, quantized), hybrid search (vector + keyword,
  Postgres FTS / in-process BM25 on SQLite), reciprocal rank fusion, cross-encoder rerank
  (ms-marco-MiniLM), parent expansion, token-budget packing with [n] citations, query cache keyed by
  corpus version. Per-user memory RAG is cut.
- **RAG quality pass (2026-10-17):** English-only corpus (JSH 2019, Suita, OMRON press release out of
  the RAG; the rules engine still cites them as references), back matter dropped at ingest,
  cross-encoder relevance floor −6.0, 3000-token budget, soft per-source cap, `cited` in chat
  responses, labelled eval set + gated quality test (`scripts/eval_rag.py`, `tests/test_rag_eval.py`).
- **Booking agent v2:** Places discovery near the user + calendar ∩ opening hours + channels
  web form → email (with inbound replies) → AI phone call; login/CAPTCHA ⇒ skip to next channel
  without asking the user; one approval before contact; sandbox targets by default.
  Spec: `docs/specs/booking-v2.md`. Build order: email, phone, web (against the mock clinic).
