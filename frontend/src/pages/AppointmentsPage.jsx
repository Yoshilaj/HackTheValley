import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import useRecommendations from "../hooks/useRecommendations.js";
import useProviders from "../hooks/useProviders.js";
import { getAppointment } from "../api/client.js";
import BookVisitSheet from "../components/BookVisitSheet.jsx";
import CancelAppointmentDialog from "../components/CancelAppointmentDialog.jsx";
import AgentAppointmentSheet from "../components/AgentAppointmentSheet.jsx";
import VisitSummary from "../components/VisitSummary.jsx";
import GuestGate from "../components/GuestGate.jsx";
import { useSession } from "../context/SessionContext.jsx";
import { useAppointments } from "../context/AppointmentsContext.jsx";
import { downloadIcs } from "../utils/calendar.js";

// ⋮ menu on an upcoming appointment: directions, add to calendar, print a
// visit summary, cancel. Closes on outside click or Esc.
function AppointmentMenu({ a, onCancel, onPrint }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const btnRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e) => !wrapRef.current?.contains(e.target) && setOpen(false);
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

  const mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(
    [a.name, a.address].filter(Boolean).join(", ")
  )}`;

  return (
    <div className="appt-menu-wrap" ref={wrapRef}>
      <button
        ref={btnRef}
        className="dots"
        aria-label={`More options for ${a.name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        ⋮
      </button>
      {open && (
        <div className="appt-menu" role="menu">
          {a.address && (
            <a role="menuitem" href={mapsUrl} target="_blank" rel="noopener noreferrer" onClick={() => setOpen(false)}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 21s-6-5.6-6-11a6 6 0 0112 0c0 5.4-6 11-6 11z" />
                <circle cx="12" cy="10" r="2.2" />
              </svg>
              Get directions
            </a>
          )}
          {a.slot && (
            <button
              role="menuitem"
              onClick={() => {
                setOpen(false);
                downloadIcs(a);
              }}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="4" y="5" width="16" height="15" rx="2" />
                <path d="M8 3v4M16 3v4M4 10h16M12 13v5M9.5 15.5h5" />
              </svg>
              Add to calendar
            </button>
          )}
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onPrint(a);
            }}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M7 9V3h10v6M7 17H5a2 2 0 01-2-2v-4a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2h-2" />
              <rect x="7" y="14" width="10" height="7" rx="1" />
            </svg>
            Print visit summary
          </button>
          <button
            role="menuitem"
            className="danger"
            onClick={() => {
              setOpen(false);
              onCancel(a);
            }}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" />
            </svg>
            Cancel appointment
          </button>
        </div>
      )}
    </div>
  );
}

// What a visit arranged by the booking agent asks of the user right now. The
// buttons open the appointment sheet, which shows the details before anything
// happens: review & approve (nothing is sent before), add the confirmed time to
// the calendar (nothing is written before), or just see where it stands.
function AgentActions({ a, onOpen }) {
  if (!a.remote) return null;
  if (a.backendStatus === "awaiting_approval") {
    return (
      <div className="appt-actions">
        <button type="button" className="btn-primary" onClick={() => onOpen(a)} aria-haspopup="dialog">
          Review & approve
        </button>
      </div>
    );
  }
  if (a.backendStatus === "confirmed" && a.calendarStatus === "pending") {
    return (
      <div className="appt-actions">
        <button type="button" className="btn-primary" onClick={() => onOpen(a)} aria-haspopup="dialog">
          Add to calendar
        </button>
        <button type="button" className="btn-secondary" onClick={() => onOpen(a)} aria-haspopup="dialog">
          Details
        </button>
      </div>
    );
  }
  return (
    <div className="appt-actions">
      <button type="button" className="btn-secondary" onClick={() => onOpen(a)} aria-haspopup="dialog">
        {a.backendStatus === "needs_user_info" ? "See the clinic's question" : "Details"}
      </button>
    </div>
  );
}

// A tiny "copy" button beside the address; says "Copied" for a moment.
function CopyButton({ text, label }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch {
      /* clipboard blocked: nothing to do */
    }
  };
  return (
    <button type="button" className={`copy-btn ${done ? "done" : ""}`} onClick={copy} aria-label={done ? "Copied" : label} title={done ? "Copied" : label}>
      {done ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M5 12.5l4.5 4.5L19 7.5" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect x="9" y="9" width="11" height="11" rx="2" />
          <path d="M5 15V5a1 1 0 011-1h10" />
        </svg>
      )}
    </button>
  );
}

// Name, address (copyable), date, time and length. onCancel / onPrint are only
// passed for upcoming visits; past ones have no menu.
function AppointmentCard({ a, onCancel, onPrint, onOpen }) {
  return (
    <li className="card appt-card">
      <div className="appt-main">
        <h3>{a.name}</h3>
        {a.address ? (
          <p className="appt-address">
            <span>{a.address}</span>
            <CopyButton text={a.address} label={`Copy the address of ${a.name}`} />
          </p>
        ) : (
          <p className="appt-meta">Address to be confirmed</p>
        )}
        {onOpen && <AgentActions a={a} onOpen={onOpen} />}
      </div>
      <div className="appt-when">
        <b>{a.date}</b>
        {a.time && <span>{a.time}</span>}
        {a.length && <span>{a.length}</span>}
      </div>
      {onCancel ? <AppointmentMenu a={a} onCancel={onCancel} onPrint={onPrint} /> : <span aria-hidden="true" />}
    </li>
  );
}

// Section heading that expands/collapses the section body (`${id}-body`).
function SectionHeading({ id, open, onToggle, children }) {
  return (
    <h2 id={`${id}-title`} className="section-title">
      <button
        type="button"
        className="section-toggle"
        aria-expanded={open}
        aria-controls={`${id}-body`}
        onClick={onToggle}
      >
        {children}
        <svg className="section-chevron" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M9 6l6 6-6 6" />
        </svg>
      </button>
    </h2>
  );
}

export default function AppointmentsPage() {
  const { hash, key, search } = useLocation();
  const navigate = useNavigate();
  // The booking sheet offers times from the recommended clinics and the directory.
  const { items, location, locate, locating, distanceFrom } = useRecommendations();
  const { providers } = useProviders(location);
  const { upcoming, past, loaded, agentById } = useAppointments();
  const [bookOpen, setBookOpen] = useState(false);
  const [cancelTarget, setCancelTarget] = useState(null);
  const [printing, setPrinting] = useState(null); // appointment whose visit summary is being printed
  const [agentTarget, setAgentTarget] = useState(null); // agent appointment open in its sheet
  const { isGuest } = useSession();

  // Arriving from a notification or an e-mail button (?appointment=<id>): open that
  // appointment's sheet. The list usually has it; a closed one (cancelled, past) is
  // fetched on its own so the link still shows what happened.
  const linkedId = new URLSearchParams(search).get("appointment");
  useEffect(() => {
    if (!linkedId || !loaded || isGuest) return;
    const fromList = agentById(linkedId);
    if (fromList) {
      setAgentTarget(fromList);
      return;
    }
    let cancelled = false;
    getAppointment(linkedId)
      .then((raw) => !cancelled && setAgentTarget({ id: raw.id, remote: true, backendStatus: raw.status, status: raw.status, name: raw.clinic?.name, raw }))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // agentById is derived from the list; re-running on every list change would reopen a closed sheet.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkedId, loaded, isGuest]);

  // Keep the open sheet in step with the list (approve → requested, calendar added…).
  const liveTarget = agentTarget && (agentById(agentTarget.id) ?? agentTarget);
  const closeAgent = () => {
    setAgentTarget(null);
    if (linkedId) navigate("/appointments", { replace: true });
  };

  // The summary renders first, then the print dialog opens; it's removed once printing ends.
  useEffect(() => {
    if (!printing) return;
    const done = () => setPrinting(null);
    window.addEventListener("afterprint", done);
    const frame = requestAnimationFrame(() => window.print());
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("afterprint", done);
    };
  }, [printing]);
  const [open, setOpen] = useState({ upcoming: true, past: true });
  const toggle = (id) => setOpen((o) => ({ ...o, [id]: !o[id] }));

  const bookable = useMemo(
    () => [...items, ...providers.filter((p) => !items.some((r) => r.id === p.id))],
    [items, providers]
  );

  // Arriving from the dashboard's "Let CareSync book a visit" (#book). Deferred so
  // it runs after the app's own scroll-to-top on page change.
  useEffect(() => {
    if (hash !== "#book" && hash !== "#recommended") return;
    const t = setTimeout(() => setBookOpen(true), 50);
    return () => clearTimeout(t);
    // key: re-run when the same hash is navigated to again (e.g. "Pick another time").
  }, [hash, key]);

  if (isGuest) {
    return (
      <main className="page">
        <header className="page-head">
          <div>
            <h1 tabIndex={-1}>Appointments</h1>
            <p className="subtitle">Your doctor visits and lab bookings in one place.</p>
          </div>
        </header>
        <GuestGate title="Book and manage visits">
          Sign in to have CareSync book visits for you and keep track of your appointments.
        </GuestGate>
      </main>
    );
  }

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1 tabIndex={-1}>Appointments</h1>
          <p className="subtitle">Your doctor visits and lab bookings in one place.</p>
        </div>
        <button className="btn-primary" onClick={() => setBookOpen(true)} aria-haspopup="dialog">
          Let CareSync book a visit
        </button>
      </header>

      <BookVisitSheet
        open={bookOpen}
        onClose={() => setBookOpen(false)}
        providers={bookable}
        location={location}
        distanceFrom={distanceFrom}
        locate={locate}
        locating={locating}
      />
      <CancelAppointmentDialog appointment={cancelTarget} onClose={() => setCancelTarget(null)} />
      <AgentAppointmentSheet appointment={liveTarget} onClose={closeAgent} />
      {printing && <VisitSummary appointment={printing} />}

      <section aria-labelledby="upcoming-title">
        <SectionHeading id="upcoming" open={open.upcoming} onToggle={() => toggle("upcoming")}>
          Upcoming
        </SectionHeading>
        {open.upcoming && (
          <div id="upcoming-body" className="section-body section-reveal">
            {!loaded ? (
              <div className="card empty-state" role="status">
                <p className="appt-meta">Loading your appointments…</p>
              </div>
            ) : upcoming.length === 0 ? (
              <div className="card empty-state">
                <p>No upcoming appointments.</p>
                <p className="appt-meta">Visits CareSync books for you show up here.</p>
                <button className="btn-secondary" onClick={() => setBookOpen(true)} aria-haspopup="dialog">
                  Find a time
                </button>
              </div>
            ) : (
              <ul className="appt-list">
                {upcoming.map((a) => (
                  <AppointmentCard
                    key={a.id}
                    a={a}
                    onCancel={setCancelTarget}
                    onPrint={(appt) => setPrinting({ ...appt })}
                    onOpen={setAgentTarget}
                  />
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      <section aria-labelledby="past-title">
        <SectionHeading id="past" open={open.past} onToggle={() => toggle("past")}>
          Past visits
        </SectionHeading>
        {open.past && (
          <div id="past-body" className="section-body section-reveal">
            {past.length === 0 ? (
              <p className="appt-meta">No past visits yet.</p>
            ) : (
              <ul className="appt-list">
                {past.map((a) => (
                  <AppointmentCard key={a.id} a={a} />
                ))}
              </ul>
            )}
          </div>
        )}
      </section>
    </main>
  );
}
