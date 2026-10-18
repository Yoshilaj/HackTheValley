# Booking agent v2 — spec (agreed 2026-10-17)

Supersedes the email-only flow in BACKEND_PLAN §C6. Canada only, English only.

## 0. Non-negotiables
- **Sandbox by default.** `BOOKING_TARGET_MODE=sandbox` (default) redirects every outbound action to
  team-controlled targets: email → `SANDBOX_CLINIC_EMAIL` (else `CLINIC_FALLBACK_EMAIL`), phone →
  `SANDBOX_CLINIC_PHONE`, website → `SANDBOX_CLINIC_WEBSITE` (else the built-in mock clinic at
  `BASE_URL/mock-clinic`). The real clinic details are still shown to the user and logged
  ("sandbox: redirected from …"). `live` must be set explicitly. Tests always run sandbox + fakes.
- **One user approval before any outbound contact** (`BOOKING_REQUIRE_APPROVAL=true`). Everything up
  to the final submit / send / dial happens automatically; then status `awaiting_approval` and a
  notification card: clinic, distance, proposed slots, channel plan, exact fields to be shared.
  `POST /api/appointments/{id}/approve` runs the channel; `/decline` cancels.
- **Never asks the user to log in anywhere.** Login, account creation, CAPTCHA, payment fields or a
  required government/health-card number on a website ⇒ that channel is skipped immediately and
  the next one is used (recorded in `channel_attempts` with the reason). The agent never tries to
  get around a CAPTCHA.
- **Minimal disclosure.** Shared fields (`ALLOWED_FIELDS`): the identity / contact details a Canadian
  clinic routinely asks when booking, collected at onboarding (`Profile`, `GET
  /api/profile/booking-readiness`): legal first + last name, date of birth, sex (as on the health
  card), phone, e-mail, address (street, city, province, postal code), family doctor (name, clinic,
  phone), whether the patient already has a referral, preferred language — plus a generic reason
  category per pathway, preferred slots, report share link. Never raw measurements, conditions,
  medications, diagnoses, insurance details or health-card / OHIP / government-ID numbers — **no such
  field exists anywhere** (Profile, PatientInfo, forms); the agent says the patient will bring the
  health card to the visit. Per channel: the **phone** agent volunteers only name, reason, slots and
  callback number and gives the other identity details **only when the clinic asks** (never invents
  one; an empty value or anything outside the list → "the patient will provide it at the visit / you
  can reach the patient at the callback number"); the **e-mail** carries name, DOB, phone, e-mail,
  postal code (full address only without a postal code; never sex / family doctor); the **web** form
  fills only the fields it asks for (address parts only when required or without a postal code). The
  approval card lists `shared_fields` and `missing_profile_fields` (required onboarding fields still
  empty, so the user sees what the agent cannot answer). The phone agent always says it is an AI
  assistant calling on behalf of the patient.
- Consent gates unchanged: `share_with_clinic` required; `calendar_access` for FreeBusy/events.

## 1. Flow
1. Trigger: `followup_recommended` hook (unchanged entry point `run_booking_agent`).
2. **Discovery** (`app/services/booking/discovery.py`): geocode `Profile.address`/`postal_code`
   (Google Geocoding API) when lat/lng missing → Places API (New) Text Search, `regionCode=CA`,
   location bias 15 km circle, field mask incl. `regularOpeningHours`, `websiteUri`,
   `nationalPhoneNumber`, `internationalPhoneNumber`, `businessStatus`, `rating`, `location`.
   Query per pathway — cognitive: memory clinic / neurologist / geriatrician; cardio: cardiologist;
   pd: neurologist (movement disorders). Then fallbacks: family doctor / walk-in clinic, general
   hospital. Rank: specialty match, operational, distance, rating, has opening hours. Keep top 5 in
   `Appointment.candidates`. Note in the user card that Canadian specialists usually need a
   family-doctor referral; the report link supports that.
   Without a Maps key: fictional Toronto seed clinics (`data/seed/clinics_canada.json`, example.com
   emails, 555 numbers, mock-clinic website).
3. **Slots** (`slots.py`): FreeBusy (user calendar) ∩ clinic opening hours (clinic timezone),
   30-min slots, ≥24 h ahead, next 14 days, 3 spread-out proposals. No calendar → opening hours only.
   Clinic timezone (`app/integrations/timezones.py`, one fallback chain `slots.clinic_timezone` used
   by slots, orchestrator, inbound, phone): `Clinic.timezone` (set at discovery from the province in
   Places `formattedAddress` — BC Vancouver, AB Edmonton, SK Regina, MB Winnipeg, ON/QC Toronto,
   NB/NS/PE Halifax, NL St_Johns, YT Whitehorse, NT Yellowknife, NU Iqaluit; postal-code letter as a
   fallback — checked against `utcOffsetMinutes`, which wins on a mismatch), else from the offset
   alone, else the user's timezone, else America/Toronto.
4. **Channel detection** (`channels/detect.py`): fetch website (httpx, ≤5 same-domain pages, 10 s,
   robots.txt respected; SSRF guard: ≤3 manual same-site redirects, every hop's host resolved and
   refused unless all addresses are public IPv4/IPv6, http(s) only, body streamed ≤1 MB; only
   `BASE_URL/mock-clinic/…` in-process) → `web_form` (a form with booking semantics: name + e-mail /
   phone field AND a date / time choice for the visit or an explicit "Book / Request an appointment"
   submit / booking action — not search / contact / newsletter forms; or a known widget we can drive) |
   `web_blocked` (login / account / CAPTCHA / health-card / payment) | none. Extract email
   (mailto / text). Plan = [web if web_form] + [email if address] + [phone if number].
5. **Approval** (above).
6. **Execute** channels in order until one is `requested` or `confirmed`:
   - **web** (`channels/web.py`): Playwright headless Chromium (sync API in its own thread, hard
     timeout 60 s; 15 s per navigation, 5 s per action). Target: `sandbox.resolve_target("web")`
     (sandbox → `SANDBOX_CLINIC_WEBSITE`, else the mock clinic; seed clinics keep their variant);
     start at the detected `form_url` when it lies under the target, else the target, following a
     "Book online" link (≤ 2 hops). In sandbox every request to another host is aborted (live: no
     off-site navigation; sub-resources only from public IPs). Pages are fetched by the route
     handler with redirects off, so every redirect hop is checked too; a redirected request leaving
     the allowed hosts (`page.on("request")` + `redirected_from`) aborts the run before submit.
     `submitted` is set under a lock only if the watchdog has not cancelled the run, so a timed-out
     run can never submit after `execute` returned. Each loaded page is re-checked with `detect.analyze_html`: login /
     CAPTCHA / required health card / payment ⇒ `skipped(<reason>)` at once, never bypassed → next
     channel. Form fields (label, name, id, type, required, options) → `llm.extract` with a strict
     enum schema (it sees field descriptions only, never patient values) maps each field to
     `name | first_name | last_name | date_of_birth | phone | email | postal_code | address |
     reason_category | preferred_slots | report_url | sex | street | city | province |
     family_doctor_name | family_doctor_clinic | family_doctor_phone | has_referral (yes/no select) |
     preferred_language | consent | unknown | password | government_id | payment` (legal first / last
     name from the profile, else split from the name; family-doctor fields detected before the
     "someone else's details" rule, a doctor's fax / e-mail stays unknown); keyword rules are used with FakeLlm / no LLM / LLM errors and always win for password,
     health-card / government-ID and payment fields; "consent" is only a clear contact / privacy
     checkbox (never fees, marketing). Abort (`skipped`) when: any password or payment field; a
     required government-ID field; a required field mapped `unknown` ("required field outside the
     allow-list: <label>") or without a shared value; a required time choice with no option near
     the approved slots. Slot choice: an option equal to an approved slot (approval order) wins,
     else the closest option on the same local day within 2 h. Only `PatientInfo.shared_fields()`
     values are typed (address withheld when a postal code exists); a free-text notes field gets
     "Referral report (secure link): <url>"; optional unknown / blocked fields stay empty. Fill →
     full-page screenshot (`Storage.save(user_id, "booking_web", …)`) → submit → screenshot → the
     confirmation page is read (`llm.extract`: status / reference / start; regex + `<time
     datetime>` fallback; a reference the page does not show is ignored) → `confirmed` (slot,
     `external_ref` = reference number) | `requested` (pending / unclear) | `failed` (form rejected).
     Paths of the screenshots, pages visited, filled field keys and left-empty labels are in
     `channel_attempts` / `agent_log` details. The message body (`AppointmentMessage`, channel `web`,
     `external_id=web:<reference>`) lists the filled values.
   - **email** (`channels/email.py`): Resend send. From `booking@<BOOKING_REPLY_DOMAIN>` (else
     `RESEND_FROM_EMAIL`), Reply-To `book+<reply_token>@<BOOKING_REPLY_DOMAIN>`; status `requested`.
     Inbound: `POST /api/webhooks/resend/inbound` (Svix signature with `RESEND_WEBHOOK_SECRET`),
     matched on the metadata BEFORE the body is fetched / parsed (HTML ≤200 KB, stdlib parser). Only
     the reply token changes state. Without a token, a sender equal to the clinic address of exactly
     one open appointment (never the shared sandbox / fallback inbox) → `needs_review`: message
     stored as `unverified`, the user is asked to review it, no state change; else `unmatched`.
     LLM classifies → `confirmed` (only when the slot is one of the approved ones ± 5 min; any other
     time = counter-offer — clinic text never writes the calendar at an unapproved time) |
     `counter_offer` (auto-accepted only when it arrived by e-mail, the slot is free in the user's
     calendar and within opening hours, AND the acceptance e-mail was accepted by the provider; else
     ask the user) | `needs_info` (status `needs_user_info`, notification with the question; answer
     via `POST /api/appointments/{id}/user-info` → agent replies) | `declined` (next
     candidate/channel). Replies always go to the appointment's e-mail thread (`clinic.requests.email`)
     whatever `appt.channel` is (e.g. after a follow-up call). Every message stored in
     `AppointmentMessage` (idempotent on `external_id`). Confirm links stay: GET shows the slot + a
     "Confirm this time" POST button and never changes state (mail scanners); POST confirms. The
     HMAC token is bound to appointment id + current clinic id + slot index (+ issue time, 30 days),
     so links for an earlier clinic stop working after fall-through.
   - **phone** (`channels/phone.py`, provider `app/providers/voice.py`): ElevenLabs Agents outbound
     call via an imported Twilio number (`POST /v1/convai/twilio/outbound-call` with
     `ELEVENLABS_AGENT_ID`, `ELEVENLABS_AGENT_PHONE_NUMBER_ID`). Target: sandbox → always
     `SANDBOX_CLINIC_PHONE` (E.164; the real clinic number is never dialled), live → the clinic's
     number normalised to `+1…` (non-Canadian numbers skipped). Dynamic variables (only):
     patient name, callback phone, generic reason category, clinic name, 3 slots as spoken
     clinic-local times + their ISO values, today's date, appointment id, and the onboarding identity
     fields answered only on request (`patient_first_name`, `patient_last_name`, `date_of_birth`
     spoken + `date_of_birth_iso`, `patient_sex`, `patient_phone` + `_spoken`, `patient_email` +
     `_spoken` "r dot macleod at example dot com", `home_address`, `street`, `city`, `province`,
     `postal_code` + `_spoken` "M 5 H, 2 N 2", `family_doctor_name` / `_clinic` / `_phone`,
     `has_referral` yes/no, `preferred_language`; `""` when missing). The prompt: answer these only
     when asked, never volunteer, spell e-mail / postal code clearly on request, never invent; health
     card / medications / history / insurance → the patient provides them at the visit or the clinic
     calls the patient back; no health details beyond the reason category. The report link is not read
     aloud; the agent may offer to e-mail a summary and records the clinic's address. The agent says it
     is an AI assistant calling on behalf of the patient, books only one of the 3 slots (a different
     time is recorded as a counter-offer, not accepted), never shares medical details, asks whether a
     family-doctor referral is required when one is mentioned, leaves no voicemail (hangs up), asks
     for a confirmation number. Conversation id → `Appointment.external_ref`; status `requested`.
     Post-call webhook `POST /api/webhooks/elevenlabs/post-call` (`ElevenLabs-Signature: t=…,v0=…`,
     HMAC-SHA256 with `ELEVENLABS_WEBHOOK_SECRET`, 30 min tolerance) → transcript stored as inbound
     `AppointmentMessage` (ElevenLabs audio tags such as `[happy]` / `[slow]` stripped from the
     assistant's lines; the raw webhook payload is not persisted) (idempotent on the conversation id) → data-collection fields `outcome`
     (confirmed | counter_offer | needs_info | declined | no_answer | voicemail | other),
     `booked_slot` (ISO), `requested_info`, `summary`, `confirmation_number`, `referral_required`,
     `clinic_email` → same `apply_outcome` handling as e-mail. A missing / unparseable slot is read
     from the transcript (`llm.extract` via `inbound.classify_reply`, keyword fallback); a
     "confirmed" without any usable time becomes `needs_info`. `no_answer` / `voicemail` /
     `call_initiation_failure` → phone attempt `failed`: if another channel's request to the clinic
     is still open the appointment keeps waiting on it, else the next candidate runs (existing
     `next_candidate`). Fallback poll: `phone.poll_conversation(db, appt)` (`GET
     /v1/convai/conversations/{id}`). `scripts/setup_voice_agent.py` creates/updates the agent
     (prompt, first message, data collection, evaluation criteria, voicemail/end-call tools), the
     workspace post-call webhook, and with `--import-number` imports the Twilio number.
     Limitation: answers to a clinic's phone question / counter-offer are recorded and the user is
     notified, but no call-back to the clinic is placed (`orchestrator._reply` has no phone thread).
     Therefore a phone counter-offer (or a phone "confirmed" at an unapproved time) is NEVER accepted
     by the assistant: status `needs_user_info`, the question asks the user to call the clinic back;
     `accept: true` then means the user booked it themselves (`via counter_offer_booked_by_patient`).
     If an e-mail thread exists, the user's acceptance is e-mailed and `confirmed` only once sent.
7. **Finish**: calendar event (tentative on request, confirmed on confirmation), user
   notification, full `agent_log`, `channel_attempts`.

## 2. Statuses
**Clinic messages after confirmation:** a question or a new time moves a `confirmed` appointment to
`needs_user_info` (never auto-accepted; `pending_question.after_confirmation=true`, `previous_slot`);
the user's answer or a declined new time restores `confirmed` with the original slot. A cancellation
or any other text only notifies the user (state unchanged); a re-confirmation is ignored.

`proposed → awaiting_approval → requested → (needs_user_info ↔ requested) → confirmed | declined |
failed | cancelled`. `processing` is transient: approve / decline / user-info / clinic confirm POST
first claim the expected status atomically (`UPDATE … WHERE id = :id AND status = :expected`,
rowcount checked, committed before any side effect; SQLite + Postgres) — a concurrent second request
gets 409 and nothing runs twice; on an unexpected error the status is put back. Follow-up job:
reminder/next channel after 3 days without reply, `failed` after 10. A web form is never re-submitted
as a reminder (duplicate booking): the user is notified instead ("no_reply_web_form").

## 3. Mock clinic (`/mock-clinic`)
`app/api/routes/mock_clinic.py` + `app/templates/mock_clinic/*.html.j2`: a fictional small Toronto
clinic ("Queen West Family & Memory Clinic", 416-555-0142, `frontdesk@queenwestclinic.example.com`,
Mon–Thu 8–7, Fri 8–5, Sat 9–1, lunch 12–1) served by the backend itself, not under `/api`. Pages:
home (services, hours, contact, "Book online"), booking form (first / last name, date of birth,
phone, e-mail, postal code, reason dropdown with generic categories, preferred date-and-time select
of the open 30-min slots for the next 14 days — ~15 % pre-taken, booked slots disappear —, optional
notes, required contact-consent checkbox), confirmation page with reference number `QW-XXXXXX` and
`<time datetime>`. Variants, each linking only inside its own prefix so detection classifies it
alone (tested against the live pages):

| Path | Detection | Web channel |
|---|---|---|
| `/mock-clinic` | `web_form` (`form_url` …/book) | fills + submits → `confirmed` |
| `/mock-clinic/email-only` | `none` + e-mail found (mailto) | skipped ("no booking form found") → e-mail |
| `/mock-clinic/login` | `web_blocked` "login required" (sign-in / create-account wall) | skipped → e-mail / phone |
| `/mock-clinic/captcha` | `web_blocked` "captcha" (static placeholder iframe, nothing to solve; POST always rejected) | skipped → next |
| `/mock-clinic/health-card` | `web_blocked` "health-card number required" | skipped → next |

Submissions: in memory, mirrored to `<STORAGE_DIR>/mock_clinic/bookings.json` (no DB table; a health
card number is never stored, only `health_card_provided`; `consent` = the contact checkbox), listed by
`GET /mock-clinic/admin/bookings` (dev mode or `X-Admin-Secret`). Seed clinics point at these variants
(`{BASE_URL}/mock-clinic…`).

**Front desk** `GET /mock-clinic/admin` (dev mode, `X-Admin-Secret`, or `?key=<ADMIN_SECRET>`,
constant-time compare): the clinic staff's live view for demos — bookings newest first (received,
reference, patient, date of birth, phone, e-mail, postal code, reason, appointment, consent, health
card provided / not provided), re-polled every 3 s from `GET /mock-clinic/admin/feed` (same auth,
display-formatted rows, no notes / user agent), new rows flash. Values are rendered with
`textContent` / Jinja autoescape. "Clear demo bookings" = `POST /mock-clinic/admin/clear` (same auth +
`X-Front-Desk: 1`, which the page's fetch sends and a cross-site form cannot).

### Live demo: watching the web channel (2026-10-17)
- `WEB_BOOKING_HEADED=true` (default false) opens a visible Chromium window; `WEB_BOOKING_SLOW_MO_MS`
  (default 0) delays every Playwright action. Headed mode drops the single-process low-memory flags
  (they can break a headed browser) and needs the full Chromium build
  (`python -m playwright install chromium`, not only `--only-shell`). Headed runs outline + tint each
  field (inline style, restored after 1.5 s) just before it is filled and keep the window open 5 s on
  the result page. Every safety check is unchanged (request guard, walls, allow-list, submit lock);
  the hard timeout becomes 60 s + fields × slow_mo × 4 (+ 5 s hold when headed), capped at 5 min —
  set from an estimate of 12 fields and raised once the form has been read; action / navigation
  timeouts grow by 2 × slow_mo. Defaults (headless, full speed, 60 s) are unchanged.
- ngrok: an `https://….ngrok-free.dev` BASE_URL shows a browser warning page to browser user agents.
  In sandbox mode the browser context sends `ngrok-skip-browser-warning: 1` (`context_options`). It is
  set on the context, not per routed request, because the redirect follow-up after the form POST
  (303 → `/confirmation/{ref}`) bypasses Playwright route handlers; in sandbox the guard aborts every
  request to another origin, so only the target receives it (an off-target redirect follow-up is
  flagged and stops the run before submit). Live clinic runs never send it. Website *detection* is
  unaffected (the mock clinic under BASE_URL is fetched in-process).
- `scripts/test_booking.py --channel phone|web|email` (default phone; `scripts/test_phone_call.py`
  is a thin wrapper for `--channel phone`) runs ONE booking with only that channel: sandbox only, seed
  clinics only (no Places), FakeLlm planning, explicit `yes` before anything is sent.
  - `web`: forces the sandbox target to `BASE_URL/mock-clinic` (and detection to read it, so any seed
    clinic gets the standard form), checks that `BASE_URL/mock-clinic` answers (the browser loads it
    through BASE_URL, so the backend — uvicorn on :8000 behind ngrok — must be running), prints where
    to watch (`BASE_URL/mock-clinic/admin`) and the result (status, reference, slot, screenshot
    paths). `--headed` / `--slow-mo MS` (0–5000) override the two settings for that run.
  - `email`: needs `SANDBOX_CLINIC_EMAIL`; prints the reply token and Reply-To
    (`book+<token>@BOOKING_REPLY_DOMAIN`), you reply from the sandbox inbox, the script polls the
    appointment status for up to 15 min (warns when `BOOKING_REPLY_DOMAIN` / `RESEND_API_KEY` are unset).
  - `phone`: as before (needs `SANDBOX_CLINIC_PHONE` + ElevenLabs agent; waits 7 min for the webhook,
    then polls ElevenLabs once).
- Tests: `tests/test_booking_watch.py` (option plumbing, watchdog extension, header scope incl. a real
  Chromium run behind a header-recording ASGI wrapper, the headed code path run headless, front-desk
  auth / content / escaping / clear, script arguments and guards).

### Web channel deployment (stage 3, 2026-10-17)
- Deps: `playwright==1.64.0` (+ `python -m playwright install --with-deps --only-shell chromium`).
  `configured()` = `WEB_BOOKING_ENABLED` and a Playwright Chromium build in the browsers path; a
  launch failure at run time → `skipped("browser unavailable: …")` → next channel.
- Dockerfile: the `playwright` package is in requirements.txt; `--build-arg WEB_BOOKING=1` adds the
  Chromium headless shell only (`PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`). Default `WEB_BOOKING=0`
  (Render 512 MB image, see render.yaml).
- Memory (measured on macOS, one mock-clinic booking, RSS of the node driver + Chromium processes):
  multi-process Chromium ~480 MB peak; single-process (`--single-process --no-zygote`, default,
  `WEB_BOOKING_LOW_MEMORY=false` (`settings.web_booking_low_memory`) to opt out) ~340 MB peak (driver ~150 MB + Chromium ~190 MB); a run
  takes 1.5–5 s. The API process itself uses ~120 MB+ (more with local RAG models), so on Render's
  512 MB free tier set `WEB_BOOKING_ENABLED=false` (bookings go by e-mail / phone) or use ≥ 1 GB.
- Tests: `tests/test_booking_web.py` (unit + headless Chromium against a live uvicorn on a random
  port, skipped when Chromium cannot launch), `tests/test_mock_clinic.py`. Other booking tests pin
  the web channel off (`tests/booking_helpers.py::no_web`).

## 4. Keys
Google Maps key with Places API (New) + Geocoding API; Google OAuth (Calendar); Gemini; Resend +
own domain with inbound enabled; Twilio account + number; ElevenLabs key with Agents (ConvAI)
permission in addition to TTS/STT.

### Keys table (as implemented, stage 1)

| Env var | Needed for | Without it |
|---|---|---|
| `GOOGLE_MAPS_API_KEY` (Places API (New) + Geocoding API enabled) | discovery near the user, geocoding `address`/`postal_code` | fictional Toronto seed clinics (`data/seed/clinics_canada.json`) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` + user OAuth + `calendar_access` consent | FreeBusy, tentative / confirmed events | slots from opening hours only, no calendar events |
| `GEMINI_API_KEY` (`LLM_PROVIDER=gemini`) or `ANTHROPIC_API_KEY` | tool-calling planner, reply classification | deterministic planner + keyword/date reply parser |
| `RESEND_API_KEY`, `RESEND_FROM_EMAIL` | sending clinic e-mail; fetching inbound bodies (`GET /emails/receiving/{id}`) | FakeEmail (dev); inbound needs `text`/`html` in the payload |
| `BOOKING_REPLY_DOMAIN` (Resend receiving domain) | From `booking@<domain>`, Reply-To `book+<token>@<domain>` | Reply-To = patient's e-mail; inbound matched by sender only |
| `RESEND_WEBHOOK_SECRET` (`whsec_…`) | Svix verification of `POST /api/webhooks/resend/inbound` | accepted unsigned only with `DEV_MODE=true`, else 503 |
| `SANDBOX_CLINIC_EMAIL` / `SANDBOX_CLINIC_PHONE` / `SANDBOX_CLINIC_WEBSITE` | sandbox targets | e-mail: only reserved test domains (example.com …) are used as-is, else channel skipped; phone: skipped; web: `BASE_URL/mock-clinic` |
| `ELEVENLABS_API_KEY` (Agents / ConvAI permission), `ELEVENLABS_AGENT_ID`, `ELEVENLABS_AGENT_PHONE_NUMBER_ID` (from `scripts/setup_voice_agent.py [--import-number]`), `ELEVENLABS_WEBHOOK_SECRET` (printed once by the script when it creates the webhook; paste it into `.env` yourself), `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_PHONE_NUMBER` (only for the number import), `SANDBOX_CLINIC_PHONE` (sandbox) | phone channel (stage 2) | phone skipped ("not configured" / "sandbox: SANDBOX_CLINIC_PHONE not set"); webhook 503 without the secret unless `DEV_MODE=true` |
| `WEB_BOOKING_ENABLED` + Playwright/Chromium installed | web channel (stage 3) | web skipped ("not configured") |

### Implementation notes (stage 1, 2026-10-17)

- Code: `app/services/booking/` (discovery, slots, sandbox, channels/{base,detect,email,phone,web},
  orchestrator, inbound, followups); `app/services/booking_agent.py` re-exports the API.
- Approval scope (lead decision): **one approval covers the top 3 ranked usable candidates** shown on
  the card (`approval.candidates`, each with channel plan + slots; same shared fields). When every
  channel of a clinic fails or it declines, the next of those 3 is contacted without asking again —
  only with the slots shown on the card (if any is now < 24 h away: new card with new times,
  `awaiting_approval`, nothing sent) and only on the channels the card listed as usable for it
  (others are recorded as "not on the approved channel plan"); beyond them a new card
  (`awaiting_approval`) is shown. No candidates left → `failed`. The card / notification text is built
  from structured data only; the planner's `notify_user` text is kept as `approval.agent_note`
  ("AI-generated note (not verified)").
- Website detection runs only when it can matter (web channel configured, or the clinic has no
  known e-mail). Detection always reads the REAL clinic website read-only (GET, robots.txt, limits
  above), also in sandbox mode; only execution is redirected. Only pages under `BASE_URL/mock-clinic`
  (the mock clinic) and `BASE_URL/robots.txt` are fetched in-process. Logged as `agent_log` type `detection`; attempts carry `target` + `real`.
- Notification kinds added: `appointment_approval_needed`, `appointment_needs_info`.
- `needs_user_info` question lives in `Appointment.approval.pending_question`; answers are kept in
  `approval.answered_questions`. Answers containing health-card / OHIP numbers are rejected (422):
  the words health card / OHIP / HCN / health number, the Ontario 4-3-3 grouping (± 2-letter version
  code), 10 digits + version code, or 10 digits starting 0/1; plain NANP phone numbers pass.
- Inbound: Resend `email.received` carries metadata only; the body is fetched from
  `GET https://api.resend.com/emails/receiving/{email_id}`. Idempotent on `email_id`.
- Follow-up job: one follow-up per clinic (next untried channel, else a reminder) 3 days after the
  last contact; `failed` 10 days after the first contact with the current clinic.

### Phone channel setup (stage 2, 2026-10-17)

1. Twilio: create an account and buy (or use the trial) number → `TWILIO_ACCOUNT_SID`,
   `TWILIO_AUTH_TOKEN` (Console → Account info), `TWILIO_PHONE_NUMBER` (E.164). A **trial account
   can only call verified numbers** (Console → Phone Numbers → Verified Caller IDs) and plays a trial
   preamble — fine for the sandbox demo: verify your own phone and set `SANDBOX_CLINIC_PHONE` to it.
2. ElevenLabs: API key with the **Agents / Conversational AI (ConvAI)** permission (in addition to
   TTS/STT) → `ELEVENLABS_API_KEY`.
3. Public HTTPS `BASE_URL` (deployment or tunnel) so ElevenLabs can reach
   `{BASE_URL}/api/webhooks/elevenlabs/post-call`.
4. `cd backend && .venv/bin/python -m scripts.setup_voice_agent --import-number` → prints
   `ELEVENLABS_AGENT_ID` and `ELEVENLABS_AGENT_PHONE_NUMBER_ID`; a newly created webhook's signing
   secret is printed ONCE ("paste this into backend/.env as ELEVENLABS_WEBHOOK_SECRET (it cannot be
   shown again)"); the script never writes `.env`. Re-running updates the agent in place and reuses
   the webhook / number (an existing webhook's secret cannot be read back: delete it and re-run).
5. Keep `BOOKING_TARGET_MODE=sandbox` for the demo; `live` dials real clinics.

### Onboarding identity fields (2026-10-17)

- `Profile` gained `first_name`, `last_name` (legal; `display_name` stays the preferred name),
  `street`, `city`, `contact_email` (optional, else `User.email`), `family_doctor` (JSON `{name,
  clinic, phone}`), `has_referral` (bool); `sex` accepts `prefer_not`. Existing `address`,
  `postal_code`, `province`, `phone`, `language`, `emergency_contact` are reused.
- Migration: `init_db()` (backend start) runs `create_all` and then `add_missing_columns()` —
  `ALTER TABLE … ADD COLUMN` (Postgres: `IF NOT EXISTS`) for every model column missing from an
  existing table, limited to plain nullable columns (no PK / FK / unique / server default; anything
  else is logged as schema drift). Idempotent; existing rows get NULL.
- `GET /api/profile/booking-readiness` → `{ready, missing, recommended, fields_shared_with_clinics,
  never_shared}`; the approval card carries `missing_profile_fields`.
