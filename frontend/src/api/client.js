// All backend calls go through here so the API surface is in one place.
// Paths start with /api: Vite proxies them to the Python backend in dev
// (vite.config.js) and Vercel rewrites them to the deployed API (vercel.json).

// One switch for the whole app. Off: everything runs on placeholder data kept
// in this browser. On: sign-in, profile, risk, chat, notifications,
// appointments and bookings, the clinic directory and recommendations,
// medications (with refills and dose tick-offs) and blood pressure / resting
// heart rate use the backend. Set VITE_USE_BACKEND=true in
// frontend/.env.local (and in Vercel's settings).
export const USE_BACKEND = import.meta.env.VITE_USE_BACKEND === "true";

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

// Errors carry the HTTP status and the backend's `detail` message, if any.
export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
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
    throw new ApiError(`${options.method || "GET"} ${path} failed: ${res.status}`, res.status, body?.detail);
  }
  return res;
}

async function request(path, options = {}) {
  const res = await send(path, options);
  return res.status === 204 ? null : res.json();
}

const post = (path, body) => request(path, { method: "POST", body: body == null ? undefined : JSON.stringify(body) });

// ---- Auth: email + password. Returns { access_token, user_id }.
export function login(email, password) {
  return post("/api/auth/login", { email, password });
}

export function signup(email, password, displayName) {
  return post("/api/auth/signup", { email, password, display_name: displayName || undefined });
}

// Sign in with Google. The config says whether it's set up and gives the public
// OAuth client id for Google Identity Services; the credential is the ID token
// Google hands back. Returns { access_token, user_id } like login.
export function getGoogleConfig() {
  return request("/api/auth/google/config");
}

export function loginWithGoogle(credential) {
  return post("/api/auth/google", { credential });
}

export function getHealth() {
  return request("/api/health");
}

// ---- Profile: GET returns the backend's profile (first_name, last_name,
// date_of_birth, emergency_contact, ...); PUT changes only the fields sent.
export function getProfile() {
  return request("/api/profile");
}

export function saveProfile(fields) {
  return request("/api/profile", { method: "PUT", body: JSON.stringify(fields) });
}

// ---- Risk: one card per pathway (cardio, cognitive, parkinsons, general),
// each { level, priority_score 0-100, headline, drivers, probabilities, trend,
// preventive_measures, next_action }. useRiskAssessment maps them for the UI.
export function getRiskCards() {
  return request("/api/risk/latest");
}

// ---- Chat, stored per user as separate conversations.
// sendChat -> { answer, sources: [{ n, title, url, ... }], red_flags, conversation_id }.
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

// ---- Measurements: home blood pressure ({ readings, summary }) and daily
// metrics such as resting heart rate ({ metrics: { resting_hr: { daily, last_value } } }).
export function getBloodPressure() {
  return request("/api/metrics/bp");
}

export function logBloodPressure({ systolic, diastolic, pulse, measuredAt }) {
  return post("/api/metrics/bp", { readings: [{ systolic, diastolic, pulse, measured_at: measuredAt }] });
}

export function getMetricsSummary() {
  return request("/api/metrics/summary");
}

export function logMetric(type, value, recordedAt) {
  return post("/api/metrics", { source: "manual", samples: [{ type, value, recorded_at: recordedAt }] });
}

// ---- Medications (the profile's list, also used by the risk assessment and chat):
// [{ id, name, dose, frequency, purpose, prescriber, refills, times, days_left,
//    take_with, side_effects, interactions, glyph }].
export function getMedications() {
  return request("/api/medications");
}

// Open refill requests: [{ id, medication_id, pharmacy_id, method, note, status, created_at }].
export function getRefills() {
  return request("/api/refills");
}

// Ask for refills: { medicationIds: [id], pharmacyId, method: "pickup" | "delivery", note? }.
export function requestRefill({ medicationIds, pharmacyId, method, note }) {
  return post("/api/refills", { medication_ids: medicationIds, pharmacy_id: pharmacyId, method, note: note || undefined });
}

// Withdraw the open refill request for one medication.
export function cancelRefill(medicationId) {
  return request(`/api/refills/${encodeURIComponent(medicationId)}`, { method: "DELETE" });
}

// Doses ticked off on a day ("YYYY-MM-DD"): { day, taken: ["<medication id>-<time>"] }.
export function getDoses(day) {
  return request(`/api/doses/${day}`);
}

// Replace the day's ticked-off doses.
export function saveDoses(day, taken) {
  return request(`/api/doses/${day}`, { method: "PUT", body: JSON.stringify({ taken }) });
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
  return request(`/api/bookings/${encodeURIComponent(id)}`, { method: "DELETE" });
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
