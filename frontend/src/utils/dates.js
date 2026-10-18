// Small date helpers shared across pages.

// Age in whole years today, from an ISO date of birth ("1974-03-03").
// null when there's no usable date (e.g. a new account that hasn't added one).
export function ageFrom(iso) {
  const born = new Date(`${iso}T00:00`);
  if (!iso || Number.isNaN(born.getTime())) return null;
  const now = new Date();
  let age = now.getFullYear() - born.getFullYear();
  const beforeBirthday =
    now.getMonth() < born.getMonth() || (now.getMonth() === born.getMonth() && now.getDate() < born.getDate());
  return beforeBirthday ? age - 1 : age;
}

// "today at 6:12 PM" / "yesterday at 9:00 AM" / "Oct 3 at 9:00 AM"
export function relativeDay(at) {
  const d = new Date(at);
  const startOfToday = new Date().setHours(0, 0, 0, 0);
  const day =
    d >= startOfToday
      ? "today"
      : d >= startOfToday - 24 * 60 * 60 * 1000
        ? "yesterday"
        : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return `${day} at ${d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`;
}

export const capitalize = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

// "Oct 14" from "2026-10-14" (a local calendar day) or any ISO time; axis ticks and tooltips.
export function shortDate(iso) {
  if (!iso) return "";
  const d = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(`${iso}T12:00`) : new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// "Wk of Oct 13" from a Monday ("2026-10-13"), for the weekly risk points.
export const weekLabel = (monday) => `Wk of ${shortDate(monday)}`;

// "in 6 h" / "in 2 days" / "tomorrow 3:00 AM"-style note for a scheduled time (ISO).
export function untilText(iso) {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  const mins = Math.round((t - Date.now()) / 60000);
  if (mins <= 0) return "any moment now";
  if (mins < 60) return `in ${mins} min`;
  const hours = Math.round(mins / 60);
  if (hours < 36) return `in ${hours} h`;
  return `in ${Math.round(hours / 24)} days`;
}

// Value for <input type="datetime-local"> in local time: "2026-10-17T21:05".
export function toLocalInput(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// "Just now" / "5m ago" / "3h ago" / "Yesterday" / "Oct 14", for notification times (ms).
export function timeAgo(ts) {
  const mins = Math.floor((Date.now() - ts) / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  if (hours < 48) return "Yesterday";
  return new Date(ts).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
