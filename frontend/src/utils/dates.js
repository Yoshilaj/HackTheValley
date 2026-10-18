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
