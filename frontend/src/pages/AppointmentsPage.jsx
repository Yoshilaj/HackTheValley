import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import useRecommendations from "../hooks/useRecommendations.js";
import useProviders from "../hooks/useProviders.js";
import BookVisitSheet from "../components/BookVisitSheet.jsx";
import CancelAppointmentDialog from "../components/CancelAppointmentDialog.jsx";
import BookDetailSheet from "../components/BookDetailSheet.jsx";
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
    [a.place, a.address].filter(Boolean).join(", ")
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

// A visit the backend's booking agent is waiting on: nothing is sent to the
// clinic until the user approves. Shows the error in place if approving fails.
function ApproveButton({ a, onApprove }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const approve = async () => {
    setBusy(true);
    setError("");
    try {
      await onApprove(a.id);
    } catch {
      setError("Couldn't approve it. Try again in a moment.");
      setBusy(false);
    }
  };
  return (
    <div className="appt-approve">
      <button type="button" className="btn-primary" onClick={approve} disabled={busy} aria-label={`Approve request to ${a.name}`}>
        {busy ? "Approving…" : "Approve request"}
      </button>
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

// onCancel / onPrint are only passed for upcoming visits; past ones have no menu.
function AppointmentCard({ a, onCancel, onPrint, onApprove }) {
  return (
    <li className="card appt-card">
      <span className="appt-icon" aria-hidden="true">
        {a.glyph}
      </span>
      <div className="appt-main">
        <h3>{a.name}</h3>
        <p className="appt-meta">{[a.specialty, a.place, a.address].filter(Boolean).join(" · ")}</p>
        {a.note && <p className="appt-note">{a.note}</p>}
        {onApprove && a.backendStatus === "awaiting_approval" && <ApproveButton a={a} onApprove={onApprove} />}
      </div>
      <div className="appt-when">
        <b>{a.date}</b>
        <span>{[a.time, a.length].filter(Boolean).join(" · ")}</span>
      </div>
      <span className={`status ${a.status.toLowerCase()}`}>{a.status}</span>
      {onCancel ? <AppointmentMenu a={a} onCancel={onCancel} onPrint={onPrint} /> : <span aria-hidden="true" />}
    </li>
  );
}

// status: undefined | "requested". Book opens the detail sheet, which does the booking.
function BookButton({ status, onBook, label }) {
  if (status === "requested") {
    return (
      <span className="status confirmed rec-done" role="status">
        ✓ Requested
      </span>
    );
  }
  return (
    <button className="btn-primary rec-book" onClick={onBook} aria-label={label} aria-haspopup="dialog">
      Book
    </button>
  );
}

function RecommendationCard({ r, status, onBook }) {
  return (
    <li className="card rec-card">
      <span className="appt-icon" aria-hidden="true">
        {r.glyph}
      </span>
      <div className="appt-main">
        <h3>{r.name}</h3>
        <p className="appt-meta">
          {r.specialty} · {r.clinic} · {r.distance}
        </p>
        <p className="rec-why">
          <b>Why:</b> {r.reason}
        </p>
      </div>
      <div className="appt-when">
        <b>Next available</b>
        <span>{r.nextSlot}</span>
      </div>
      <BookButton status={status} onBook={onBook} label={`Book ${r.name}`} />
    </li>
  );
}

const PROVIDER_TYPES = { doctor: "Doctor", clinic: "Clinic", hospital: "Hospital", lab: "Lab" };

// A doctor/clinic/hospital from the backend directory. Every field but name is optional.
function ProviderCard({ p, status, onBook }) {
  const glyph = p.glyph ?? p.name.slice(0, 2).toUpperCase();
  return (
    <li className="card provider-card">
      <span className="appt-icon" aria-hidden="true">
        {glyph}
      </span>
      <div className="appt-main">
        <div className="provider-title">
          <h3>{p.name}</h3>
          {p.type && <span className="provider-type">{PROVIDER_TYPES[p.type] ?? p.type}</span>}
          {p.acceptingNew != null && (
            <span className={`status ${p.acceptingNew ? "confirmed" : "pending"}`}>
              {p.acceptingNew ? "Accepting new patients" : "Waitlist"}
            </span>
          )}
        </div>
        <p className="appt-meta">{[p.specialty, p.clinic].filter(Boolean).join(" · ")}</p>
        <dl className="provider-facts">
          {p.address && (
            <div>
              <dt>Address</dt>
              <dd>{[p.address, p.distance].filter(Boolean).join(" · ")}</dd>
            </div>
          )}
          {p.hours && (
            <div>
              <dt>Hours</dt>
              <dd>{p.hours}</dd>
            </div>
          )}
          {p.phone && (
            <div>
              <dt>Phone</dt>
              <dd>
                <a href={`tel:${p.phone.replace(/[^\d+]/g, "")}`}>{p.phone}</a>
              </dd>
            </div>
          )}
          {p.website && (
            <div>
              <dt>Website</dt>
              <dd>
                <a href={p.website} target="_blank" rel="noopener noreferrer">
                  {p.website.replace(/^https?:\/\//, "")}
                </a>
              </dd>
            </div>
          )}
          {p.languages?.length > 0 && (
            <div>
              <dt>Languages</dt>
              <dd>{p.languages.join(", ")}</dd>
            </div>
          )}
        </dl>
      </div>
      {p.slots?.length > 0 ? (
        <BookButton status={status} onBook={onBook} label={`Book ${p.name}`} />
      ) : (
        p.phone && (
          <a className="btn-secondary provider-call" href={`tel:${p.phone.replace(/[^\d+]/g, "")}`}>
            Call to book
          </a>
        )
      )}
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
  const { hash, key } = useLocation();
  const recRef = useRef(null);
  const { items, location, locate, locating, locError } = useRecommendations();
  const { providers, loading: providersLoading } = useProviders(location);
  const { upcoming, past, isBooked, approveVisit } = useAppointments();
  const [bookOpen, setBookOpen] = useState(false);
  const [cancelTarget, setCancelTarget] = useState(null);
  const [detailTarget, setDetailTarget] = useState(null); // provider whose Book was pressed
  const [printing, setPrinting] = useState(null); // appointment whose visit summary is being printed
  const { isGuest } = useSession();

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
  // Which sections are expanded: the ones you act on start open, the rest closed.
  const [open, setOpen] = useState({ upcoming: true, recommended: true, providers: false, past: false });
  const toggle = (id) => setOpen((o) => ({ ...o, [id]: !o[id] }));

  // Booked if it's in Upcoming, wherever it was booked from.
  const statusOf = (id) => (isBooked(id) ? "requested" : undefined);
  // Once booked a recommendation moves to Upcoming; cancelling brings it back.
  const openRecs = items.filter((r) => !isBooked(r.id));

  // Everything the booking sheet can offer times from (recommendations first).
  const bookable = useMemo(
    () => [...items, ...providers.filter((p) => !items.some((r) => r.id === p.id))],
    [items, providers]
  );

  const scrollToRecs = () => {
    setOpen((o) => ({ ...o, recommended: true }));
    recRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // Arriving from the dashboard's "Let CareSync book a visit" (#book) or a link
  // to the list (#recommended). Deferred so it runs after the app's own
  // scroll-to-top on page change.
  useEffect(() => {
    if (hash !== "#book" && hash !== "#recommended") return;
    const t = setTimeout(() => (hash === "#book" ? setBookOpen(true) : scrollToRecs()), 50);
    return () => clearTimeout(t);
    // key: re-run when the same hash is navigated to again (e.g. "Pick another time").
  }, [hash, key]);

  // Card "Book" buttons open the detail sheet (info, time, then "are you sure?").
  const book = (provider) => setDetailTarget(provider);

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
          Sign in to get specialists suggested for you, book visits and keep track of your appointments.
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
        locate={locate}
        locating={locating}
      />
      <CancelAppointmentDialog appointment={cancelTarget} onClose={() => setCancelTarget(null)} />
      <BookDetailSheet provider={detailTarget} onClose={() => setDetailTarget(null)} />
      {printing && <VisitSummary appointment={printing} />}

      <section aria-labelledby="upcoming-title">
        <SectionHeading id="upcoming" open={open.upcoming} onToggle={() => toggle("upcoming")}>
          Upcoming
        </SectionHeading>
        {open.upcoming && (
          <div id="upcoming-body" className="section-body section-reveal">
            {upcoming.length === 0 ? (
              <div className="card empty-state">
                <p>No upcoming appointments.</p>
                <p className="appt-meta">Visits you book show up here.</p>
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
                    onApprove={approveVisit}
                  />
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      <section aria-labelledby="recommended-title" id="recommended" ref={recRef} className="rec-section">
        <SectionHeading id="recommended" open={open.recommended} onToggle={() => toggle("recommended")}>
          Recommended for you
        </SectionHeading>
        {open.recommended && (
          <div id="recommended-body" className="section-body section-reveal">
            <div className="rec-head">
              <p className="subtitle">CareSync suggests specialists near you based on your chats and diagnoses.</p>
              <button className="rec-locate" onClick={locate} disabled={locating}>
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M12 21s-6-5.6-6-11a6 6 0 0112 0c0 5.4-6 11-6 11z" />
                  <circle cx="12" cy="10" r="2.2" />
                </svg>
                {locating ? "Finding you…" : location ? "Near your location" : "Use my location"}
              </button>
            </div>
            {locError && (
              <p className="file-error" role="alert">
                {locError}
              </p>
            )}
            {openRecs.length === 0 ? (
              <div className="card empty-state">
                <p>You've booked all your recommendations.</p>
                <p className="appt-meta">CareSync will suggest more as it learns from your chats.</p>
              </div>
            ) : (
              <ul className="appt-list">
                {openRecs.map((r) => (
                  <RecommendationCard key={r.id} r={r} status={statusOf(r.id)} onBook={() => book(r)} />
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      <section aria-labelledby="providers-title" className="rec-section">
        <SectionHeading id="providers" open={open.providers} onToggle={() => toggle("providers")}>
          Nearby appointments
        </SectionHeading>
        {open.providers && (
          <div id="providers-body" className="section-body section-reveal">
            <p className="subtitle">Places in your care network you can book with.</p>
            {providersLoading ? (
              <div className="card empty-state" role="status">
                <p className="appt-meta">Loading doctors and hospitals…</p>
              </div>
            ) : providers.length === 0 ? (
              <div className="card empty-state">
                <p>No doctors or hospitals added yet.</p>
                <p className="appt-meta">
                  Once your care network is connected, they'll appear here with addresses, hours and open times.
                </p>
              </div>
            ) : (
              <ul className="appt-list">
                {providers.map((p) => (
                  <ProviderCard key={p.id} p={p} status={statusOf(p.id)} onBook={() => book(p)} />
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
