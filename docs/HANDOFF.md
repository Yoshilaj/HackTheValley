# Morning hand-off (backend, 2026-10-17, integration pass)

Read this first. Details: [BACKEND_PLAN.md](BACKEND_PLAN.md) (build status + "Revision 2026-10-17"),
[specs/booking-v2.md](specs/booking-v2.md) (booking agent v2), [API.md](API.md) (every route),
[README.md](../README.md) (setup).

## Do this first (two lines in your `backend/.env`)

Your `.env` still has the pre-integration values for two settings. Change them by hand:

```bash
EMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5-Q   # the quantized model; dev.db was ingested with it
SCHEDULER_TIMEZONE=America/Toronto
```

`backend/data/dev.db` was rebuilt from scratch with the `-Q` model. With the non-quantized model in
`.env` the server would embed queries with a different provider name than the corpus was built with
(and re-ingest on the next `ingest_corpus` run). New settings to know about are all in
`backend/.env.example` with one-line comments (`WEB_BOOKING_LOW_MEMORY`, `CRISIS_LINE_NUMBER`,
`LOCAL_EMBED_*`, `BOOKING_*`, `SANDBOX_CLINIC_*`, `TWILIO_*`, `ELEVENLABS_AGENT_*`, `RESEND_WEBHOOK_SECRET`).

## What exists now

- **Providers:** Gemini for the LLM (chat answers, extraction, booking-agent tool loop, vision);
  ElevenLabs for STT (word timestamps), TTS (story audio, cached per story) and the phone agent
  (ElevenLabs Agents + Twilio); local fastembed embeddings (`nomic-embed-text-v1.5-Q`, 768-d) and
  an optional cross-encoder reranker (`ms-marco-MiniLM-L-6-v2`). Anthropic / OpenAI / Voyage stay as
  optional alternatives; deterministic fakes for tests.
- **RAG (GymSync design):** parent sections + child chunks, hybrid vector + BM25 search, RRF,
  cross-encoder rerank, **relevance floor** (`RAG_MIN_RERANK_SCORE=-6.0`, cross-encoder only: chat
  `sources` can now be empty), parent expansion, 3000-token packing with `[n]` citations, soft cap of
  2 passages per source. Chat `sources[]` carries `section`; the response now also has `cited`.
  **English-only corpus** (34 sources incl. Hypertension Canada 2020, BP-monitor and smartwatch AF studies, CCS heart failure 2025, CSEP movement/sleep, CCSA alcohol, MDS + Canadian PD guidelines, PD finger tapping, Lancet dementia 2024, CCCDTD5; lay-term query expansion in app/rag/lexicon.py; JSH 2019, Suita and the OMRON press release removed;
  references/acknowledgements/consent/etc. dropped at ingest). Quality is measured by
  `scripts/eval_rag.py` on `tests/fixtures/rag_eval.json` (116 questions, per-tag report) and gated by
  `tests/test_rag_eval.py`. Retrieval needs `EMBEDDING_PROVIDER=local` to match the ingested corpus.
  The answer step is checked by `scripts/eval_answers.py` (real Gemini answer + Gemini judge, two
  calls per question; 2026-10-17: 90% of answers fully supported by their excerpts, 96% correct
  citations, 18/18 unanswerable questions say the library does not cover them).
- **Region:** Canada only, English only. 911 / Health811 (811) / 9-8-8 (`CRISIS_LINE_NUMBER`);
  Framingham (CCS) as the long-term cardio context; America/Toronto.
- **Booking agent v2:** Places discovery near the user → slots (calendar ∩ opening hours) → channel
  detection (web form / e-mail / phone) → **one user approval** → web (Playwright) → e-mail (Resend,
  inbound replies via webhook) → phone (ElevenLabs Agents). **Sandbox by default**: every outbound
  action goes to the `SANDBOX_CLINIC_*` targets or the built-in mock clinic (`/mock-clinic`), never
  to the real clinic; the real clinic is shown and logged ("sandbox: redirected from …").
- **User notifications A and B (2026-10-17 evening):** (A) a detected sign → ONE e-mail "Please approve:
  appointment request with {clinic}" with the finding ("Why this was flagged"), the clinic, the 3 times, how it
  is contacted, exactly what it receives, and **Approve / Not now buttons that work from the inbox** (signed
  7-day links; GET shows a page, only POST acts) plus a deep link into the app; the in-app bell opens the same
  card as a sheet (`AgentAppointmentSheet`). The `followup_recommended` e-mail is skipped when this one follows.
  (B) clinic confirms → ONE e-mail "Confirmed: {clinic} · {time}" with directions, what to bring, the `.ics`
  attached and **the calendar step: nothing is written to the calendar until the user taps "Add to Google
  Calendar"** (e-mail button or in-app; `CALENDAR_WRITE_REQUIRES_APPROVAL=true`). Without a connected Google
  Calendar the user gets the `.ics` and a pre-filled Google Calendar link instead. Routes in API.md §11
  (`/action`, `/calendar`, `/calendar.ics`, `/calendar-sync`); tests `tests/test_user_notifications.py`.
- Everything from the earlier build (data in, assessments, risk engine, reports, notifications,
  auth, consent, audit, export/delete, rate limits, scheduler) is unchanged.

Tests: full suite green (`cd backend && .venv/bin/pytest -q`).

## Live smoke result (this pass, local server, real Gemini + Places, everything outbound in sandbox)

Run with `LLM_PROVIDER=gemini EMBEDDING_PROVIDER=local RERANKER=cross_encoder BOOKING_TARGET_MODE=sandbox
EMAIL_PROVIDER=fake WEB_BOOKING_ENABLED=true STT_PROVIDER=fake TTS_PROVIDER=fake` (ElevenLabs off to keep the
free credits). `scripts/demo_smoke.py` (updated for v2: a web-confirmed booking counts as the clinic
confirmation; each step prints its time) on a fresh DB: **10/11**. Every booking/report step passed.
Chat failed only on the Gemini quota (see below).

- Risk run → cardio `followup_recommended`, score 70, `[BP-02, RHY-01]` (35.9 s incl. Places discovery,
  read-only detection of 2 real clinic websites and the Gemini tool loop).
- Places returned 5 real Toronto cardiology clinics. The plan for TGH Peter Munk Cardiac Centre was
  `web`. Approve → **confirmed via web in 2.6 s** (mock clinic reference `QW-…`). The agent_log/channel attempt
  shows `target=http://127.0.0.1:<port>/mock-clinic`, `real=https://www.uhn.ca/PMCC`, `redirected=true`.
  The screenshot shows only allowed fields (name, DOB, phone, e-mail, postal code, reason category, slot,
  report link).
- Chat (cross-encoder, local -Q embedder):
  BP threshold → Hypertension Canada 2020 (p. 6) + Canadian Stroke Best Practices (p. 9), correct 135/85.
  Smartwatch AF alert → Apple IRN support ×2, Apple ECG support ×2, CCS AF 2020 "4.2 Investigations".
  Story recall → ASRT JMIR 2022 ×2, Storyteller ADNI4 2024. The answers were grounded, cited and in
  Canadian context, about 3–11 s each.

## Run the demo

```bash
cd backend
# (optional) rebuild the DB: rm -f data/dev.db && .venv/bin/python -m scripts.seed \
#   && .venv/bin/python -m scripts.ingest_corpus --provider local && .venv/bin/python -m scripts.seed_demo
.venv/bin/python -m playwright install --only-shell chromium   # once, for the web booking channel
.venv/bin/uvicorn app.main:app --port 8000                     # BASE_URL must match (http://localhost:8000)
.venv/bin/python -m scripts.demo_smoke                         # 2nd terminal: risk run → approve → web booking → report → chat
```

`data/dev.db` is ready (rebuilt in this pass, no appointments yet). Only the first risk run that
reaches `followup_recommended` creates the report and the booking. To repeat the booking, rebuild the
DB (commented line above, ~3 min for the ingest) and delete `data/uploads/`. Keep
`BOOKING_TARGET_MODE=sandbox`.

## Gemini free tier: plan for it before the demo

Observed live: **gemini-3.5-flash free tier = 5 requests/min and 20 requests/day** (per project and
model; `gemini-3.5-flash-lite` has its own, larger bucket). A booking run's tool loop uses several
calls and hit the limit mid-loop. It fell back to the deterministic planner (booking still completed).
After that, chat returned the polite "Sorry — I can't generate an answer right now" (now a 200 with
sources, not a 500). Before the demo, do one of:
- enable billing on the Google AI Studio project (paid Tier 1, which raises the limits by orders of magnitude), or
- set `GEMINI_MODEL=gemini-3.5-flash-lite` (answers were good in the smoke and fast, 2.5–4 s).

## Keys to obtain (put them in `backend/.env` yourself, then set the matching switch)

| Key(s) | Where to get it | Switch / note |
|---|---|---|
| `GEMINI_API_KEY` | aistudio.google.com → Get API key (enable billing for demo-day limits) | `LLM_PROVIDER=gemini`; optional `GEMINI_MODEL`, `GEMINI_CHEAP_MODEL` |
| `ELEVENLABS_API_KEY` | elevenlabs.io → Developers → API Keys; enable **Text to Speech, Speech to Text and Agents / Conversational AI** permissions | `STT_PROVIDER=elevenlabs`, `TTS_PROVIDER=elevenlabs`; the phone channel needs the Agents permission |
| `ELEVENLABS_AGENT_ID`, `ELEVENLABS_AGENT_PHONE_NUMBER_ID`, `ELEVENLABS_WEBHOOK_SECRET` | `.venv/bin/python -m scripts.setup_voice_agent --import-number` prints the ids; when it creates the post-call webhook it prints the secret **once**. Paste it into `.env` (the script never writes files) | needs a public HTTPS `BASE_URL` |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` | console.twilio.com → Account info + a (trial) number. **Trial accounts can only call Verified Caller IDs**: verify your own phone (Phone Numbers → Verified Caller IDs) | only used for the number import; set `SANDBOX_CLINIC_PHONE` to your verified phone (E.164) |
| `GOOGLE_MAPS_API_KEY` | Google Cloud console → enable **Places API (New)** and **Geocoding API** → API key (restrict it to those two APIs) | clinic discovery near the user; without it the fictional Toronto seed clinics are used |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google Cloud console → OAuth client (Web), enable Calendar API, redirect URI `{BASE_URL}/api/integrations/google/callback`; for "Sign in with Google" add the frontend origins (`http://localhost:5173`, `http://localhost`, the Vercel URL) under Authorized JavaScript origins and publish the consent screen | Sign in with Google button + One Tap (client id only) and FreeBusy + events after the `calendar_access` consent; without it, email/password sign-in and opening-hours slots only |
| `RESEND_API_KEY`, `RESEND_FROM_EMAIL` | resend.com → API Keys + a verified sending domain | `EMAIL_PROVIDER=auto`; `SANDBOX_CLINIC_EMAIL` / `CLINIC_FALLBACK_EMAIL` = your inbox |
| `BOOKING_REPLY_DOMAIN`, `RESEND_WEBHOOK_SECRET` | Resend → Domains → enable **receiving** on a (sub)domain; Webhooks → `{BASE_URL}/api/webhooks/resend/inbound` for `email.received` → signing secret (`whsec_…`) | clinic replies by e-mail; unsigned webhooks are accepted only with `DEV_MODE=true` |
| `BASE_URL` | your deployment URL or a tunnel (`ngrok http 8000`), **public HTTPS** | required for the ElevenLabs / Resend webhooks; the mock clinic and share links use it too |
| `HF_TOKEN` | huggingface.co → Settings → Access tokens (write) | only for `ml_training/train_pd_vowel.py --push-to-hub` |
| `JWT_SECRET`, `ADMIN_SECRET`, `FIELD_ENCRYPTION_KEY` | generate (commands in `.env.example`) | required when `DEV_MODE=false` |

`GET /api/health` shows which provider each integration uses.

## Deploying to Render (512 MB free instance)

`render.yaml` (repo root) is set for 512 MB: `LLM_PROVIDER=gemini`, `STT/TTS_PROVIDER=elevenlabs`,
`EMBEDDING_PROVIDER=local` with the `-Q` model, `RERANKER=identity`, `WEB_BOOKING_ENABLED=false`,
`BOOKING_TARGET_MODE=sandbox`, `DEV_MODE=false`, `SCHEDULER_ENABLED=true`, `SCHEDULER_TIMEZONE=America/Toronto`,
`FASTEMBED_CACHE_PATH=/opt/fastembed`; every key is `sync: false` (entered in the dashboard).
Memory budget, as measured: API ~120 MB + -Q embedder ~285 MB once loaded. The cross-encoder (+140 MB)
and a Playwright booking run (~340 MB peak) do not fit, so on Render bookings go by e-mail or phone and
ranking is plain RRF. `backend/Dockerfile` bakes the embedder into the image at build time (no
download on cold start). `--build-arg WITH_RERANKER=1` also bakes the reranker, and
`--build-arg WEB_BOOKING=1` adds Chromium (default 0). Use both only on an instance with 1 GB or more,
then set `RERANKER=cross_encoder` and `WEB_BOOKING_ENABLED=true`. Ingest the corpus into the
Postgres DB from your laptop (`DATABASE_URL=… EMBEDDING_PROVIDER=local .venv/bin/python -m
scripts.ingest_corpus --provider local`) instead of on the 512 MB box. Nothing has been deployed yet.

## Known limitations and risks (say these out loud in the pitch)

- **Phone channel:** when the clinic asks a question or counter-offers on the call, the answer is
  recorded and the user is notified, but **no call-back to the clinic is placed** (`orchestrator._reply`
  has no phone thread). **There is no automatic polling of call results.** The outcome arrives only through the
  post-call webhook (`phone.poll_conversation` exists but no job calls it). The **ElevenLabs
  webhook signature format** (`ElevenLabs-Signature: t=…,v0=…`, HMAC-SHA256) is implemented from the
  docs: verify it once on a real delivery. The phone channel has never placed a real call in this pass.
- **Gemini free-tier quota** (above): with the default model, the tool loop and chat both degrade once it is spent.
- **Website detection reads real clinic sites** (GET only, robots.txt, at most 5 pages) even in sandbox,
  and it is heuristic. In the smoke, a hospital site (uhn.ca) was classified `web_form`; in live mode that
  form would have to be checked. Execution was redirected to the mock clinic as designed.
- **RAG:** eval (2026-10-17, cross-encoder): hit@k 1.0, hit@1 0.857, abstain on off-topic 5/6. Weak
  spots: AF questions rank the stroke guideline above the CCS AF guideline; "who should have their
  cholesterol checked" misses the CCS lipids Screening section (MiniLM reranker); "reset my iPhone
  passcode" leaks an Apple Watch page. No sleep, insomnia, MDS PD criteria or English OMRON AF source.
  With `RERANKER=identity` (Render default) there is no relevance floor.
- **Vowel model is weak:** pooled out-of-fold AUC 0.74, 95% CI 0.53–0.91 (32 subjects, of whom 8 are healthy). It is experimental and
  can never raise a level on its own.
- **Red flags are keyword-based** (EN + legacy JA list in `app/services/guardrails.py`): paraphrases slip
  through and negations can trigger it. French is not covered. The chat prompt sometimes adds the 9-8-8 line
  unprompted (seen on the story-recall question).
- **BP thresholds:** 135/85 home threshold matches Hypertension Canada; the 145/90 single-window cut-off and
  the morning rules come from JSH 2019. Rules are `draft` (not clinically reviewed).
- **Scores are not validated:** the rule set and the story-recall score are unvalidated. Framingham uses the 7-day home SBP
  in place of office SBP.
- **Infrastructure:** the **Postgres + pgvector path is untested** (SQLite only; FTS lexical search on
  Postgres never run). There is no Alembic: delete `dev.db` after schema changes. Uploads are on local disk. Rate limits are
  in-memory (1 worker). Deleting an account does not call Google's token revoke.
- **Not built:** PDF rendering (use browser Print → PDF from `/r/{token}`), HF Hub push run, CI.

## Suggested order for the remaining hours

1. Edit the two `.env` lines above. Decide on the Gemini quota (billing, or `GEMINI_MODEL=gemini-3.5-flash-lite`).
2. Run the demo locally (commands above) and read one chat answer and one report.
3. Phone channel: Twilio trial + verified caller ID, a tunnel for `BASE_URL`, `setup_voice_agent
   --import-number`, paste the printed ids and secret into `.env`, set `SANDBOX_CLINIC_PHONE` to your
   phone and do one sandbox call. This also checks the webhook signature format.
4. E-mail channel: Resend domain with receiving, `BOOKING_REPLY_DOMAIN`, `RESEND_WEBHOOK_SECRET`,
   `SANDBOX_CLINIC_EMAIL` = your inbox. Reply to the booking e-mail once.
5. Deploy: Neon or Supabase Postgres, Render from `render.yaml`, ingest from the laptop, then
   `BASE_URL=https://… ADMIN_SECRET=… .venv/bin/python -m scripts.demo_smoke`. This is the first Postgres run.
6. Send the two dataset requests (below) if they have not gone out yet.

## Dataset access requests still to send

1. **DementiaBank / ADReSS** (TalkBank): request membership on the DementiaBank page at talkbank.org (academic use, no redistribution). This would enable a picture-description model (English, Cookie Theft task).
2. **mPower** (Sage Bionetworks, Synapse): create a Synapse account, become a certified user and accept the data-use agreement for the mPower project. This would enable a tapping classifier.
3. **UCI Parkinson's:** nothing to send. It is a direct download and is already used for the vowel model.

## Review fixes applied (2026-10-17)

1. Red flags in daily logs (flags or `notes` text) and in `POST /observations` take the shared emergency path `app/services/emergency.py:handle_red_flags` (guidance + `notify_emergency` + event assessment). Chat and check-in use it too. The responses gained `emergency_guidance`.
2. Emergency notifications record `observation_ids`. `assess_user` notifies only on a transition into emergency or on new trigger ids, and never repeats the same ids within 24 h.
3. Chat, check-in, log and observation emergencies create `RiskAssessment(level=emergency)` immediately (`trigger='event'`) without a second notification.
4. `process_appointment_followups` cancels a requested appointment and notifies the user when `share_with_clinic` was revoked. No reminder is sent.
5. `GET /r/{token}` requires an active `share_with_clinic` consent. Revoking it nulls the user's share tokens. A report generated without that consent has `share_token=None` and `share_url: null`.
6. The clinic report shows only observations relevant to its pathway. It never includes `suicidal_ideation` or red-flag free text, and free text is cut to 200 characters.
7. Prior assessments are loaded by time window (60 days, cap 500), and persistence uses the latest assessment in each weekly window.
8. A re-posted daily log keeps its observations (and their `created_at`) and deletes only those whose flag is now false. A check-in sends only the flags answered in that submission.
9. Production with no e-mail configured fails at startup. `FakeEmail` outside dev mode returns `accepted=False`.
10. `notify_emergency(commit=False)` is used inside `assess_user`, so it no longer commits mid-run.
11. `facts_for_llm` drops the patient name. The clinic e-mail note is a fixed per-pathway template; the LLM's `note` is only a slot-preference enum.
12. The confirm link requires `status == 'requested'` (409 otherwise). The token is `ts.sig` with a 30-day expiry, keyed by `APPOINTMENT_TOKEN_SECRET` or a key derived from `JWT_SECRET`.
13. `insufficient_data` / `invalid_measurement` come only from gate rules (BP-00/RHY-02, COG-01, GEN-01; PD only when no input at all). Other missing inputs go to `data_quality.missing_optional_inputs` (spec §8 updated).
14. Spec §6 now says "1.5 × scaled MAD (MAD × 1.4826)", matching the code.
15. The OAuth callback URL-encodes `error` in the redirect.
16. The Apple Health upload limit is enforced while streaming, and partial files are removed.
17. `tests/test_admin.py` seeds a fresh user, not the shared demo user.

Regression tests: `backend/tests/test_review_fixes.py`. `DELETE /api/me` is unchanged and has a TODO for the Google token revoke.

