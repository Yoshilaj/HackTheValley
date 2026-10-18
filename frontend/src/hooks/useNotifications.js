import { useEffect, useState } from "react";
import { USE_BACKEND, getNotifications, markAllNotificationsRead, markNotificationRead } from "../api/client.js";
import { useSession } from "../context/SessionContext.jsx";
import { notifications as seed } from "../data/placeholder.js";
import { timeAgo } from "../utils/dates.js";

// The bell's notifications: [{ id, kind, icon?, title, body, time, unread, to }].
// Backend off: the placeholder list, read state kept in memory.
// Backend on (signed in): GET /api/notifications, re-checked every minute;
// marking read is saved with /api/notifications/{id}/read and /read-all.

const REFRESH_MS = 60_000;

// Backend kind -> the icon it gets and the page it opens. Appointment notifications
// open that appointment's sheet (approval card, confirmation + calendar step).
function kindOf(kind, payload) {
  if (kind.startsWith("appointment") || kind.startsWith("booking")) {
    const id = payload?.appointment_id;
    return { kind: "appointment", to: id ? `/appointments?appointment=${encodeURIComponent(id)}` : "/appointments" };
  }
  if (kind.includes("medication") || kind.includes("refill")) return { kind: "medication", to: "/medications" };
  return { kind: "risk", icon: "alert", to: "/" }; // follow-up findings, level changes, emergencies
}

// Backend bodies can be several lines with rule ids ("[BP-02 v1]"); the bell shows one short line.
function shortBody(body) {
  const lines = (body ?? "")
    .replace(/\s*\[[A-Z]{2,4}-\d+\s*v\d+\]/g, "")
    .split("\n")
    .map((l) => l.replace(/^[-•]\s*/, "").trim())
    .filter((l) => l && !l.endsWith(":"));
  const first = lines[0] ?? "";
  return first.length > 140 ? `${first.slice(0, 137).trimEnd()}…` : first;
}

function fromBackend(n) {
  return {
    id: n.id,
    ...kindOf(n.kind ?? "", n.payload),
    title: n.title,
    body: shortBody(n.body),
    time: timeAgo(new Date(n.created_at).getTime()),
    unread: !n.read_at,
  };
}

export default function useNotifications() {
  const { signedIn } = useSession();
  const live = USE_BACKEND && signedIn;
  const [items, setItems] = useState(USE_BACKEND ? [] : seed);

  useEffect(() => {
    if (!live) {
      if (USE_BACKEND) setItems([]);
      return;
    }
    let cancelled = false;
    const load = () =>
      getNotifications()
        .then((list) => !cancelled && setItems(list.map(fromBackend)))
        .catch(() => {
          /* offline: keep what we have */
        });
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [live]);

  const markRead = (id) => {
    setItems((ns) => ns.map((n) => (n.id === id ? { ...n, unread: false } : n)));
    if (live) markNotificationRead(id).catch(() => {});
  };

  const markAllRead = () => {
    setItems((ns) => ns.map((n) => ({ ...n, unread: false })));
    if (live) markAllNotificationsRead().catch(() => {});
  };

  return { items, markRead, markAllRead };
}
