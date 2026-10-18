// All backend calls go through here so the API surface is in one place.
// Paths start with /api: Vite proxies them to the Python backend in dev
// (vite.config.js) and Vercel rewrites them to the deployed API (vercel.json).

// Every page reads and writes through the backend: there is no offline or
// placeholder mode. Guests (no account) only see the sign-in prompts.

// The demo patient created by the backend's `python -m scripts.seed_demo`.
export const DEMO_ACCOUNT = { email: "demo@example.com", password: "demo1234" };

// Sign-in token from /api/auth/login or /signup, sent as a Bearer token.
const TOKEN_KEY = "caresync-token";

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the session lasts until reload */
  }
}

// Fired when the backend rejects the stored token (expired or revoked), so
// SessionContext can send the user back to the landing page.
export const SIGNED_OUT_EVENT = "caresync:signed-out";

// Errors carry the HTTP status, the backend's `detail` message and the whole
// error body (some 409s add fields, e.g. the story task's delay_remaining_s).
export class ApiError extends Error {
  constructor(message, status, detail, body = null) {
    super(message);
    this.status = status;
    this.detail = detail;
    this.body = body;
  }
}

async function send(path, options = {}) {
  const token = getToken();
  const headers = { ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { ...options, headers: { ...headers, ...options.headers } });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    if (res.status === 401 && token) {
      setToken(null);
      window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
    }
    throw new ApiError(`${options.method || "GET"} ${path} failed: ${res.status}`, res.status, body?.detail, body);
  }
  return res;
}

// No answer at all, or a gateway error without the backend's own message: the
// free-tier server was busy, waking up or dropped the connection. Worth one more try.
const GATEWAY = new Set([502, 503, 504]);
export const isTransient = (err) => !(err instanceof ApiError) || (GATEWAY.has(err.status) && !err.detail);

async function retryOnce(fn, delayMs = 3000) {
  try {
    return await fn();
  } catch (err) {
    if (!isTransient(err)) throw err;
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    return fn();
  }
}

async function request(path, options = {}) {
  const res = await send(path, options);
  return res.status === 204 ? null : res.json();
}

const post = (path, body) => request(path, { method: "POST", body: body == null ? undefined : JSON.stringify(body) });
const patch = (path, body) => request(path, { method: "PATCH", body: JSON.stringify(body) });
const del = (path) => request(path, { method: "DELETE" });

// A file the backend serves only to its owner (task audio): fetched with the
// token and returned as a Blob for an object URL.
export async function fetchBlob(path) {
  const res = await send(path);
  return res.blob();
}

// ---- Auth: email + password. Returns { access_token, user_id, email }.
export function login(email, password) {
  return post("/api/auth/login", { email, password });
}

export function signup(email, password, displayName) {
  return post("/api/auth/signup", { email, password, display_name: displayName || undefined });
}

// Sign in with Google. The config says whether it's set up and gives the public
// OAuth client id for Google Identity Services; the credential is the ID token
// Google hands back. Returns { access_token, user_id, email } like login.
export function getGoogleConfig() {
  return request("/api/auth/google/config");
}

export function loginWithGoogle(credential) {
  return post("/api/auth/google", { credential });
}

// End this sign-in on the server so the saved token stops working (all: every
// browser and device). Reads the token when called, so call it before clearing it.
export function logout({ all = false } = {}) {
  return post("/api/auth/logout", { all });
}

// Where the account is signed in: [{ id, method, label, created_at, last_seen_at, current }].
export function getSessions() {
  return request("/api/auth/sessions");
}

export function revokeSession(id) {
  return request(`/api/auth/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function getHealth() {
  return request("/api/health");
}

// ---- Device tokens: a long-lived token an iOS Shortcut sends as X-Device-Token
// to push Apple Watch data (POST /api/metrics). The raw token is returned once.
export function createDeviceToken(label) {
  return post("/api/auth/device-token", { label });
}

// [{ id, label, created_at, last_used_at }] (never the tokens themselves).
export function getDeviceTokens() {
  return request("/api/auth/device-tokens");
}

export function revokeDeviceToken(id) {
  return del(`/api/auth/device-tokens/${encodeURIComponent(id)}`);
}

// ---- Profile: GET returns the backend's profile (first_name, last_name,
// date_of_birth, emergency_contact, ...); PUT changes only the fields sent.
export function getProfile() {
  return request("/api/profile");
}

export function saveProfile(fields) {
  return request("/api/profile", { method: "PUT", body: JSON.stringify(fields) });
}

// The first-run onboarding was finished (idempotent); returns the profile with onboarded_at set.
export function completeOnboarding() {
  return post("/api/profile/onboarding-complete");
}

// ---- Risk: one card per pathway (cardio, cognitive, parkinsons, general),
// each { level, priority_score 0-100, headline, drivers, probabilities, trend,
// preventive_measures, next_action }. useRiskAssessment maps them for the UI.
export function getRiskCards() {
  return request("/api/risk/latest");
}

// Weekly points per pathway, oldest first: [{ week (Monday, YYYY-MM-DD), pathway, level, priority_score, assessment_id }].
export function getRiskHistory(weeks = 8) {
  return request(`/api/risk/history?weeks=${weeks}`);
}

// When the assessment last ran and when the scheduled runs are next due:
// { last_assessed_at, last_trigger (manual | nightly | weekly | event), scheduler_running,
//   next_runs: { nightly_risk_assessment, weekly_summary_notifications } } (ISO times or null).
export function getRiskStatus() {
  return request("/api/risk/status");
}

// ---- Chat, stored per user as separate conversations.
// sendChat -> { answer, sources: [{ n, title, url, ... }], conversation_id }.
// Pass the conversation's server id to continue it, or newConversation to start
// one (the reply carries its new id). The backend takes text only; attachments
// stay on this device.
export function sendChat(message, { conversationId, newConversation = false } = {}) {
  return post("/api/chat", { message, conversation_id: conversationId ?? undefined, new_conversation: newConversation });
}

// sendChat, streamed: onEvent gets each event while the answer is written -
// { type: "status", stage: "searching" | "thinking" }, { type: "thought", text }
// (the model's thinking), { type: "delta", text } (more of the answer) and
// { type: "answer", answer, sources } (the final text). Resolves to the "done"
// event, which has the same fields as sendChat's reply.
export async function streamChat(message, { conversationId, newConversation = false } = {}, onEvent = () => {}) {
  const path = "/api/chat/stream";
  const res = await send(path, {
    method: "POST",
    body: JSON.stringify({ message, conversation_id: conversationId ?? undefined, new_conversation: newConversation }),
  });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line);
      if (event.type === "error") throw new ApiError(`POST ${path} failed: ${event.status}`, event.status, event.detail);
      if (event.type === "done") return event;
      onEvent(event);
    }
    if (done) throw new ApiError(`POST ${path} ended early`, 0);
  }
}

// The signed-in user's conversations, newest first: [{ id, title, created_at, updated_at, message_count }].
export function getConversations() {
  return request("/api/chat/conversations");
}

// One conversation's messages, oldest first: [{ id, role, content, sources, created_at }].
export function getConversationMessages(id) {
  return request(`/api/chat/conversations/${encodeURIComponent(id)}/messages`);
}

// Upload a file to the user's record. kind: doctor_note | prescription | lab |
// bp_photo | ecg_pdf | audio. Accepts PDF, JPEG, PNG, GIF, WebP, plain text and
// audio (up to 20 MB). Returns { id, kind, status, extracted: { data: { summary, ... } } }.
export function uploadDocument(file, kind) {
  const form = new FormData();
  form.append("file", file);
  form.append("kind", kind);
  return request("/api/uploads/document", { method: "POST", body: form });
}

export function deleteConversation(id) {
  return request(`/api/chat/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
}

// ---- Notifications: [{ id, kind, title, body, payload, read_at, created_at }].
export function getNotifications() {
  return request("/api/notifications");
}

export function markNotificationRead(id) {
  return post(`/api/notifications/${encodeURIComponent(id)}/read`);
}

export function markAllNotificationsRead() {
  return post("/api/notifications/read-all");
}

// ---- Appointments the backend's booking agent arranges after a follow-up
// finding: [{ id, status, clinic, proposed_slots, chosen_slot, ... }]. The user
// approves, declines or cancels them.
export function getAppointments() {
  return request("/api/appointments");
}

export function getAppointment(id) {
  return request(`/api/appointments/${encodeURIComponent(id)}`);
}

// channel (optional): "web" | "email" | "phone" - contact the clinic only that
// way; it must be one the approval card lists as available.
export function approveAppointment(id, channel) {
  return post(`/api/appointments/${encodeURIComponent(id)}/approve`, channel ? { channel } : null);
}

// The high-risk pop-up: the booking agent's request for a pathway ("cardio" |
// "cognitive" | "parkinsons"), reused if one is active. Comes back
// awaiting_approval with approval.channel_plan: [{ channel, available, reason,
// target }] for web, email and phone. Errors: 404 no assessment for that
// pathway, 403 sharing with clinics not allowed yet, 409 emergency.
export function startAutoBooking(pathway) {
  return post("/api/appointments/auto", { pathway });
}

// Consent: purpose "share_with_clinic" lets the booking agent send the clinic
// your contact details and the report link.
export function grantConsent(purpose) {
  return post("/api/consent", { purpose, granted: true });
}

// "Not now" on the approval card: the request is cancelled, nothing is sent.
export function declineAppointment(id) {
  return post(`/api/appointments/${encodeURIComponent(id)}/decline`);
}

export function cancelAppointment(id) {
  return post(`/api/appointments/${encodeURIComponent(id)}/cancel`);
}

// Calendar step of a confirmed appointment. CareSync never writes to the user's
// calendar without this: { status: pending | added | skipped | none,
// mode: google (connected: "add" writes the event) | manual (use ics_url / google_url),
// event_id?, add_url?, google_url?, ics_url? }.
export function getCalendarSync(id) {
  return request(`/api/appointments/${encodeURIComponent(id)}/calendar-sync`);
}

export function decideCalendarSync(id, decision) {
  return post(`/api/appointments/${encodeURIComponent(id)}/calendar-sync`, { decision });
}

// ---- Measurements: home blood pressure ({ readings, summary: { per_day, window_7d } })
// and daily metrics such as resting heart rate
// ({ metrics: { resting_hr: { unit, daily: [{ date, value }], baseline_30d, last_value } } }).
export function getBloodPressure(days = 30) {
  return request(`/api/metrics/bp?days=${days}`);
}

// One session's cuff readings (spec §2): readings [{ systolic, diastolic, pulse? }]
// numbered in order and stamped a minute apart from measuredAt (ISO time).
// session: "morning" | "evening" | "other" (the backend derives it from the local
// hour when omitted); irregularPulse is the cuff's irregular-heartbeat symbol.
export function logBloodPressure({ readings, measuredAt, session, irregularPulse = false, arm, position }) {
  const t0 = new Date(measuredAt).getTime();
  return post("/api/metrics/bp", {
    readings: readings.map((r, i) => ({
      systolic: r.systolic,
      diastolic: r.diastolic,
      pulse: r.pulse ?? null,
      measured_at: new Date(t0 + i * 60_000).toISOString(),
      session: session ?? null,
      reading_no: i + 1,
      irregular_pulse_flag: Boolean(irregularPulse),
      arm: arm ?? null,
      position: position ?? null,
      source: "manual",
    })),
  });
}

export function getMetricsSummary(days = 30) {
  return request(`/api/metrics/summary?days=${days}`);
}

// type: a backend metric type (resting_hr, weight_kg, body_temp_c, spo2_pct,
// blood_glucose_mmol_l, sleep_asleep_minutes, ...); recordedAt: ISO time.
export function logMetric(type, value, recordedAt) {
  return post("/api/metrics", { source: "manual", samples: [{ type, value, recorded_at: recordedAt }] });
}

// A rhythm result from a watch or cuff (spec §4): source apple_irn | apple_ecg |
// omron_connect | manual_irregular_pulse; label is the device's own wording
// ("Atrial Fibrillation", "Sinus Rhythm", "Inconclusive", ...), normalised server-side.
export function logRhythmResult({ source, label, recordedAt }) {
  return post("/api/metrics/rhythm", { source, label, recorded_at: recordedAt });
}

export function getRhythmResults() {
  return request("/api/metrics/rhythm");
}

// ---- Daily check-in (one row per day; a second save on the same day updates it).
// Scales are 1-5; flags are the backend's check-in vocabulary (the backend check-in vocabulary).
// Returns the row plus the observations it raised.
export function getDailyLogs(days = 30) {
  return request(`/api/logs/daily?days=${days}`);
}

export function saveDailyLog(body) {
  return post("/api/logs/daily", body);
}

// ---- Finger-tapping task (spec §5.3, keyboard protocol): 30 s per hand of
// alternating `s` / `;` presses, each press as { key, down_ms, up_ms } from performance.now().
// Returns { task_id, valid, reason, features, message }.
export function submitTapping({ left, right, durationS = 30, keys = ["s", ";"] }) {
  return post("/api/tasks/tapping", {
    input_method: "keyboard",
    duration_s: durationS,
    keys,
    left: { events: left },
    right: { events: right },
  });
}

// Past assessment tasks, newest first: { items: [{ id, task_type, created_at, status,
// score, valid, invalid_reason, model_scores, summary }] }. `summary` holds a few numbers
// per task for charts (tapping: tap_count, iti_cv per hand; vowel: snr_db, probability;
// story: score and per-recall scores).
export function getTaskHistory(taskType, limit = 20) {
  const q = new URLSearchParams({ limit: String(limit), ...(taskType ? { task_type: taskType } : {}) });
  return request(`/api/tasks/history?${q}`);
}

// ---- Sustained vowel (spec §5.4): one repetition of "ahh" as recorded in the
// browser (webm/opus, mp4 or ogg; the backend decodes by file extension).
// Returns { task_id, repetition_index, valid, status, recording_quality, reasons, message, tips, experimental_score }.
export function submitVowel(blob, repetition, ext = "webm") {
  const form = new FormData();
  form.append("audio", blob, `vowel-${repetition}.${ext}`);
  form.append("repetition", String(repetition));
  return retryOnce(() => request("/api/tasks/vowel", { method: "POST", body: form }));
}

// ---- Story recall battery (spec §1): start -> status with the current step;
// each step is submitted with its recording (recall and fluency steps), how long
// the story was played (listen steps) or nothing (the delay, once it has elapsed).
// Status: { task_id, steps, step_index, current_step, finished, instruction, audio_url,
//           delay_remaining_s, max_recall_s, result }.
export function startStory(language) {
  const q = language ? `?language=${encodeURIComponent(language)}` : "";
  return post(`/api/tasks/story/start${q}`);
}

export function getStory(taskId) {
  return request(`/api/tasks/story/${encodeURIComponent(taskId)}`);
}

// The story's audio (listen steps), as a Blob for an object URL.
export function getStoryAudio(audioUrl) {
  return fetchBlob(audioUrl);
}

export function submitStoryStep(taskId, { step, blob, ext = "webm", playedS, forceDelay = false }) {
  const form = new FormData();
  form.append("step", step);
  if (blob) form.append("audio", blob, `${step}.${ext}`);
  if (playedS != null) form.append("played_s", String(playedS));
  if (forceDelay) form.append("force_delay", "true");
  // A retry after a step that did save gets a 409 (step already done), which the task resolves by re-reading.
  return retryOnce(() => request(`/api/tasks/story/${encodeURIComponent(taskId)}/step`, { method: "POST", body: form }));
}

// ---- Apple Health export (export.zip or export.xml): parsed in the background
// into daily metrics (walking speed, step length, sleep...). Returns { job_id, upload_id };
// poll getUploadJob until status is "done".
export function uploadAppleHealth(file) {
  const form = new FormData();
  form.append("file", file);
  return request("/api/uploads/apple-health", { method: "POST", body: form });
}

export function getUploadJob(jobId) {
  return request(`/api/uploads/jobs/${encodeURIComponent(jobId)}`);
}

// Run the assessment on everything saved so far and return the fresh cards.
export function runRiskAssessment() {
  return post("/api/risk/run");
}

// ---- Clinics and visits you book yourself. Clinics come nearest first (to the
// shared location, else the profile address), each with open times worked out
// from its opening hours: [{ id, type, name, specialty, clinic, address, phone,
// website, hours, distance, distance_km, glyph, slots: [local ISO time], next_slot }].
const near = ({ lat, lng } = {}) => (lat != null && lng != null ? `?lat=${lat}&lng=${lng}` : "");

export function getProviders(location) {
  return request(`/api/providers${near(location)}`);
}

// The nearest clinic for each follow-up finding, as above plus { reason }.
export function getRecommendations(location) {
  return request(`/api/recommendations${near(location)}`);
}

// Your upcoming bookings: [{ id, provider_id, provider: { name, specialty, clinic,
// address, phone, glyph, distance }, slot, slot_label, note, status, created_at }].
export function getBookings() {
  return request("/api/bookings");
}

// Book with a provider at a time (slot: local ISO time, or omit for next available).
// provider: details to remember when it isn't in the backend's directory.
export function bookAppointment({ providerId, slot, note, provider }) {
  return post("/api/bookings", { provider_id: providerId, slot: slot ?? undefined, note: note || undefined, provider });
}

export function cancelBooking(id) {
  return del(`/api/bookings/${encodeURIComponent(id)}`);
}

// ---------------------------------------------------------------------------
// Planned endpoints the backend doesn't have yet. These features run in this
// browser until the endpoints exist (each has its own switch).

// Pick the best appointment for a free-text description. The backend calls
// Gemini (GEMINI_API_KEY lives on the server only). Request:
//   { description, prefs: { days, times, maxKm }, location?: { lat, lng }, candidates: [{ providerId, slot }] }
// Expected response: { providerId, slot, reason }
export function matchVisit(body) {
  return post("/api/visit-match", body);
}
