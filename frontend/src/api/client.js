// All backend calls go through here so the API surface is in one place.
// Paths start with /api — Vite proxies them to the Python backend in dev.

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`${options.method || "GET"} ${path} failed: ${res.status}`);
  }
  return res.json();
}

// Planned endpoint: email sign-in or sign-up (newAccount). The backend sends a
// one-time sign-in link or code and returns { user: { email, name? } } once
// verified. Google and Apple go through the backend's OAuth redirects
// (/api/auth/google, /api/auth/apple, with ?signup=1 for a new account).
export function signInWithEmail(email, newAccount = false) {
  return request("/api/auth/email", { method: "POST", body: JSON.stringify({ email, newAccount }) });
}

// Planned endpoint: save the user's edited health profile (see ProfileContext
// for the shape). Returns the saved profile.
export function saveProfile(profile) {
  return request("/api/profile", { method: "PUT", body: JSON.stringify(profile) });
}

export function getHealth() {
  return request("/api/health");
}

// Planned endpoint: the backend scores risk from the user's chat history and
// doctors' diagnoses, checked against reliable medical sources.
// Expected response:
// { score: 0-100 overall index (higher = more risk), previous?: { score }  - last month, for the trend
//   updatedAt?: ISO time, basis?: { chats, diagnoses }, note?: string,
//   suspected?: { condition, specialty, why, bring },   - drives the follow-up flag at 85+
//   conditions?: [{ id, name, outcome,      - per-disease estimates on the dashboard
//     risk: % chance within `horizon` years, horizon: years, typical: % for a healthy person the same age,
//     previous?: last month's %, bands: [moderate from %, high from %], method: e.g. "QRISK3-based estimate",
//     details? }] }
// where details explains the score (every list optional):
//   { summary, chats: [{ quote, date, why?, chatId? }],
//     diagnoses: [{ finding, doctor, specialty?, date }],
//     sources: [{ name, title, excerpt, url? }]  (retrieved medical sources, RAG),
//     tips: [string] }
export function getRiskAssessment() {
  return request("/api/risk-assessment");
}

// Planned endpoint: specialists/doctors/clinics near the user, recommended
// from their chat history. lat/lng are optional (only sent if the user shares location).
// Expected response: [{ id, name, specialty, clinic, distance, distanceKm, nextSlot, reason, slots: [local ISO time] }]
export function getRecommendations({ lat, lng } = {}) {
  const query = lat != null && lng != null ? `?lat=${lat}&lng=${lng}` : "";
  return request(`/api/recommendations${query}`);
}

// Planned endpoint: save a reading the user logged on the Vitals page.
// Body: { vitalId, values: { value } or { sys, dia } for blood pressure, at: ISO time, note? }
export function logReading(body) {
  return request("/api/readings", { method: "POST", body: JSON.stringify(body) });
}

// Planned endpoints: correct or remove a reading the user logged. Body as logReading.
export function updateReading(id, body) {
  return request(`/api/readings/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deleteReading(id) {
  return request(`/api/readings/${encodeURIComponent(id)}`, { method: "DELETE" });
}

// Planned endpoint: send a refill request to the pharmacy and prescriber.
// Body: { medicationIds: [id], pharmacyId, method: "pickup" | "delivery", note? }
export function requestRefill(body) {
  return request("/api/refills", { method: "POST", body: JSON.stringify(body) });
}

// Planned endpoint: withdraw the open refill request for one medication.
export function cancelRefill(medicationId) {
  return request(`/api/refills/${encodeURIComponent(medicationId)}`, { method: "DELETE" });
}

// Planned endpoint: pick the best appointment for a free-text description.
// The backend calls Gemini (GEMINI_API_KEY lives on the server only) with the
// description, the user's preferences and the provider database, and returns
// one of the candidates. Request:
//   { description, prefs: { days, times, maxKm }, location?: { lat, lng },
//     candidates: [{ providerId, slot }] }   - openings that already fit the preferences
// Expected response: { providerId, slot, reason }
export function matchVisit(body) {
  return request("/api/visit-match", { method: "POST", body: JSON.stringify(body) });
}

// Planned endpoint: cancel a booked appointment (id from the appointment shape below).
export function cancelAppointment(appointmentId) {
  return request(`/api/bookings/${encodeURIComponent(appointmentId)}`, { method: "DELETE" });
}

// Planned endpoint: doctors, clinics and hospitals the user can book with.
// lat/lng are optional. Expected response (every field but id/name optional):
// [{ id, type: "doctor" | "clinic" | "hospital" | "lab", name, specialty, clinic, address,
//    phone, website, hours, distance, distanceKm, acceptingNew: bool, languages: [string],
//    glyph, slots: [local ISO time] }]
export function getProviders({ lat, lng } = {}) {
  const query = lat != null && lng != null ? `?lat=${lat}&lng=${lng}` : "";
  return request(`/api/providers${query}`);
}

// Planned endpoint: the user's appointments.
// Expected response: { upcoming: [appointment], past: [appointment] } where an appointment is
// { id, providerId, name, specialty, place, address, date, time, length, status, glyph, note, slot }.
export function getAppointments() {
  return request("/api/appointments");
}

// Planned endpoint: CareSync books a visit with a recommended or directory provider.
// slot is an optional local ISO time ("2025-07-10T11:00"); without it the backend
// takes the next available opening. May return { appointment } in the shape above.
export function bookAppointment(providerId, slot) {
  return request("/api/bookings", {
    method: "POST",
    body: JSON.stringify(slot ? { providerId, slot } : { providerId }),
  });
}

// With attachments this sends multipart/form-data: a "message" field plus one
// "files" field per file. Without, it stays JSON { message }.
export function sendChat(message, files = []) {
  if (files.length === 0) {
    return request("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
  }
  const form = new FormData();
  form.append("message", message);
  files.forEach((f) => form.append("files", f));
  // Empty headers so the browser sets the multipart boundary itself.
  return request("/api/chat", { method: "POST", headers: {}, body: form });
}
