import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { notifications as seed } from "../data/placeholder.js";
import { useCareAlert } from "../context/CareAlertContext.jsx";
import { useSession } from "../context/SessionContext.jsx";
import { slotLabel } from "../utils/slots.js";

const ICONS = {
  appointment: (
    <>
      <rect x="4" y="5" width="16" height="15" rx="2" />
      <path d="M8 3v4M16 3v4M4 10h16" />
    </>
  ),
  medication: (
    <>
      <rect x="3" y="9" width="18" height="6" rx="3" transform="rotate(-45 12 12)" />
      <path d="M9.5 9.5l5 5" />
    </>
  ),
  lab: <path d="M9 3h6M10 3v6l-5 9a2 2 0 001.7 3h10.6a2 2 0 001.7-3l-5-9V3" />,
  alert: <path d="M3 12h4l2-5 4 10 2-5h6" />,
};

function timeAgo(ts) {
  const mins = Math.floor((Date.now() - ts) / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  return hours < 24 ? `${hours}h ago` : "Yesterday";
}

export default function NotificationsMenu() {
  const [seedItems, setItems] = useState(seed);
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const btnRef = useRef(null);
  const { pathname } = useLocation();
  const { alert, openPrompt, markAlertRead } = useCareAlert();
  const { isGuest } = useSession();

  // The held visit from a high risk score sits on top and opens the pop-up.
  const alertItem = alert && {
    id: "care-alert",
    kind: "alert",
    title: alert.status === "confirmed" ? "Appointment confirmed" : "Flagged for follow-up",
    body:
      alert.status === "confirmed"
        ? `${alert.rec.name}, ${slotLabel(alert.slot)}`
        : `Visit held with ${alert.rec.name}, ${slotLabel(alert.slot)}. Confirm to book.`,
    time: timeAgo(alert.createdAt),
    unread: !alert.read,
  };
  // Notifications are personal, so guests have none.
  const items = isGuest ? [] : alertItem ? [alertItem, ...seedItems] : seedItems;
  const unread = items.filter((n) => n.unread).length;

  useEffect(() => setOpen(false), [pathname]);

  // Close on outside click or Esc.
  useEffect(() => {
    if (!open) return;
    const onPointer = (e) => {
      if (!wrapRef.current?.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      btnRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const markRead = (id) => setItems((ns) => ns.map((n) => (n.id === id ? { ...n, unread: false } : n)));
  const markAllRead = () => {
    setItems((ns) => ns.map((n) => ({ ...n, unread: false })));
    markAlertRead();
  };

  const openAlert = () => {
    markAlertRead();
    setOpen(false);
    openPrompt();
  };

  const itemBody = (n) => (
    <>
      <span className={`notif-icon ${n.kind}`} aria-hidden="true">
        <svg viewBox="0 0 24 24">{ICONS[n.kind]}</svg>
      </span>
      <span className="notif-text">
        <strong>{n.title}</strong>
        <span>{n.body}</span>
        <small>{n.time}</small>
      </span>
      {n.unread && <span className="notif-dot" aria-label="Unread" />}
    </>
  );

  return (
    <div className="notif-wrap" ref={wrapRef}>
      <button
        ref={btnRef}
        className="icon-btn"
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {/* With unread items the count sits on the bell's shoulder and the bell
            is cut away around it (mask); with none the bell is whole. */}
        <svg viewBox="0 0 24 24">
          {unread > 0 && (
            <defs>
              <mask id="bell-cut" maskUnits="userSpaceOnUse">
                <rect x="-2" y="-2" width="28" height="28" fill="#fff" stroke="none" />
                <circle cx="19" cy="5.5" r="9.8" fill="#000" stroke="none" />
              </mask>
            </defs>
          )}
          <g mask={unread > 0 ? "url(#bell-cut)" : undefined}>
            <path d="M6 17V11a6 6 0 1112 0v6l2 2H4z" />
            <path d="M10 21h4" />
          </g>
        </svg>
        {unread > 0 && (
          <span className="notif-badge" aria-hidden="true">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="notif-panel" role="region" aria-label="Notifications">
          <div className="notif-head">
            <h2>Notifications</h2>
            {unread > 0 && (
              <button type="button" className="notif-mark" onClick={markAllRead}>
                Mark all as read
              </button>
            )}
          </div>

          {items.length === 0 ? (
            <p className="notif-empty">
              {isGuest ? "Sign in to get reminders about appointments, medications and results." : "You're all caught up."}
            </p>
          ) : (
            <ul className="notif-list">
              {items.map((n) => (
                <li key={n.id}>
                  {n.kind === "alert" ? (
                    <button
                      type="button"
                      className={`notif-item ${n.unread ? "unread" : ""}`}
                      onClick={openAlert}
                      aria-haspopup="dialog"
                    >
                      {itemBody(n)}
                    </button>
                  ) : (
                    <Link to={n.to} className={`notif-item ${n.unread ? "unread" : ""}`} onClick={() => markRead(n.id)}>
                      {itemBody(n)}
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
