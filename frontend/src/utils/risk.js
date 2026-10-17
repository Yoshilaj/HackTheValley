// Formatting and banding for risk estimates.
// A condition estimate is a % chance of the outcome within `horizon` years,
// with `bands` = [moderate from %, high from %] taken from the guidance for
// its method (e.g. NICE uses 10% for heart attack or stroke). The overall
// index is 0-100 (see riskLevel in hooks/useRiskAssessment.js).

export const formatPct = (n) => `${Number.isInteger(n) ? n : n.toFixed(1)}%`;

export function conditionLevel({ risk, bands }) {
  if (!bands) return "Low";
  if (risk >= bands[1]) return "High";
  if (risk >= bands[0]) return "Moderate";
  return "Low";
}

// How clinicians say a risk out loud: "about 11 in 100" or, for small
// risks, "about 4 in 1,000".
export function naturalFrequency(pct) {
  return pct >= 1 ? { count: Math.round(pct), of: 100 } : { count: Math.max(1, Math.round(pct * 10)), of: 1000 };
}

// "Up from 10.8% last month" / "Down from 16 last month" / "Same as last month".
// format turns a past value into text (e.g. formatPct).
export function changeSince(now, before, format = String) {
  if (before == null) return null;
  if (Math.abs(now - before) < 0.05) return "Same as last month";
  return `${now > before ? "Up" : "Down"} from ${format(before)} last month`;
}

// "2 chats and 3 diagnoses"
export function basisText({ chats, diagnoses } = {}) {
  const parts = [];
  if (chats) parts.push(`${chats} chat${chats === 1 ? "" : "s"}`);
  if (diagnoses) parts.push(`${diagnoses} diagnos${diagnoses === 1 ? "is" : "es"}`);
  return parts.length ? parts.join(" and ") : "your health record";
}
