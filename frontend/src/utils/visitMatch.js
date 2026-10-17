import { matchVisit } from "../api/client.js";

// "Describe what you need" in the booking sheet: picks the best opening for the
// user's description. The real pick comes from the backend, which asks Gemini
// to choose from the provider database (the API key stays on the server, never
// in this frontend). Until POST /api/visit-match exists, a keyword matcher
// stands in. Flip USE_BACKEND once the endpoint is live.
const USE_BACKEND = false;

// Words that point to a specialty, for the stand-in matcher.
const SPECIALTY_HINTS = [
  { specialty: "Cardiology", words: ["heart", "blood pressure", "bp", "chest", "palpitation", "hypertension", "dizzy", "dizziness", "cholesterol", "headache"] },
  { specialty: "Endocrinology", words: ["sugar", "glucose", "diabetes", "diabetic", "thyroid", "insulin", "metformin", "weight"] },
  { specialty: "Sleep medicine", words: ["sleep", "insomnia", "snore", "snoring", "tired", "fatigue", "apnea", "awake"] },
];

function localMatch(description, options) {
  const text = description.toLowerCase();
  let best = null;
  for (const o of options) {
    const hint = SPECIALTY_HINTS.find((h) => h.specialty === o.rec.specialty);
    const hits = hint ? hint.words.filter((w) => text.includes(w)) : [];
    // More matching words wins; options are soonest first, so ties keep the earliest.
    if (hits.length && (!best || hits.length > best.hits.length)) best = { option: o, hits };
  }
  if (best) {
    return {
      option: best.option,
      reason: `You mentioned ${best.hits.map((w) => `"${w}"`).join(", ")}, which ${best.option.rec.specialty} looks after. This is the soonest opening that fits your preferences.`,
    };
  }
  return {
    option: options[0],
    reason: "Nothing in your description pointed to a particular specialist, so CareSync picked the soonest opening that fits your preferences.",
  };
}

// options: openings that already fit the user's day/time/distance preferences,
// soonest first, each { id, iso, rec }. Returns { option, reason } or null.
export async function pickVisit({ description, prefs, location, options }) {
  if (!options.length) return null;
  if (!USE_BACKEND) return localMatch(description, options);

  const res = await matchVisit({
    description,
    prefs,
    location,
    candidates: options.map((o) => ({ providerId: o.rec.id, slot: o.iso })),
  });
  const option = options.find((o) => o.rec.id === res?.providerId && o.iso === res?.slot);
  return option ? { option, reason: res.reason ?? "" } : null;
}
