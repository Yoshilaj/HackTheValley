import { useEffect, useRef, useState } from "react";
import { useAppointments } from "../context/AppointmentsContext.jsx";
import { getCalendarSync } from "../api/client.js";

// The sheet for an appointment CareSync's booking agent is arranging. What it
// shows depends on where the request is:
//   awaiting_approval - notification A: why it was flagged, the clinic, the three
//                       times, how the clinic is contacted and what it receives,
//                       then "Not now" / "Approve request". Nothing is sent before.
//   confirmed         - notification B: the confirmed time and place, and the
//                       calendar step. CareSync never writes to the calendar
//                       until the user taps "Add"; the .ics is always offered.
//   anything else     - where the request stands and what happens next.
// Native <dialog> like the other sheets. `appointment` is the context's mapped
// record (with `.raw`, the backend appointment).

const SPECIALIST = {
  cardiology: "a cardiologist",
  neurology: "a neurologist",
  memory_clinic: "a memory clinic",
  geriatrics: "a geriatrician",
  family_medicine: "a family doctor",
  walk_in: "a walk-in clinic",
  hospital: "a hospital clinic",
};
const CHANNEL = { web: "its online booking form", email: "e-mail", phone: "phone (an AI assistant calls the front desk)" };
// From your profile (kept current: saving the profile updates a pending request).
const SHARED = [
  ["name", "Name"],
  ["date_of_birth", "Date of birth"],
  ["sex", "Sex (as on your health card)"],
  ["phone", "Phone"],
  ["email", "E-mail"],
  ["address", "Address"],
  ["postal_code", "Postal code"],
  ["reason_category", "Reason for the visit"],
];
const SEX = { male: "Male", female: "Female", other: "Other", prefer_not: "Prefer not to say" };
const sharedValue = (k, v) => (k === "sex" ? SEX[v] ?? v : String(v));
const PROFILE_FIELDS = {
  first_name: "legal first name",
  last_name: "legal last name",
  date_of_birth: "date of birth",
  sex: "sex (as on your health card)",
  phone: "phone number",
  email: "e-mail",
  street: "street address",
  city: "city",
  province: "province",
  postal_code: "postal code",
};
const NEXT = {
  requested: "The clinic picks one of the proposed times or suggests another. We'll tell you as soon as it replies, and follow up after 3 days of silence.",
  processing: "CareSync is contacting the clinic right now.",
  proposed: "CareSync is preparing this request.",
  needs_user_info: "The clinic asked something CareSync can't answer for you.",
  cancelled: "This request was closed. Nothing more is sent.",
  failed: "CareSync couldn't reach any of the clinics it tried.",
};
const TITLE = {
  requested: "Request sent",
  processing: "Contacting the clinic…",
  proposed: "Preparing a request",
  needs_user_info: "The clinic has a question",
  cancelled: "Request closed",
  failed: "Couldn't book this visit",
};

function planSentence(plan = []) {
  const usable = plan.filter((p) => p.available).map((p) => CHANNEL[p.channel] ?? p.channel);
  if (usable.length === 0) return "No contact channel is available for this clinic.";
  if (usable.length === 1) return `By ${usable[0]}.`;
  return `First by ${usable[0]}; if that doesn't work, by ${usable.slice(1).join(", then by ")}.`;
}

const distance = (c) => (typeof c?.distance_km === "number" && c.distance_km < 500 ? `${c.distance_km.toFixed(1)} km away` : null);
const mapsUrl = (c) =>
  `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent([c?.name, c?.address].filter(Boolean).join(", "))}`;
const whenOf = (slot) => {
  if (!slot?.start) return null;
  const d = new Date(slot.start);
  const e = slot.end ? new Date(slot.end) : null;
  const t = (x) => x.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  return {
    day: d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" }),
    time: e ? `${t(d)}–${t(e)}` : t(d),
  };
};

function CloseButton({ onClose }) {
  return (
    <button className="sheet-close" aria-label="Close" onClick={onClose}>
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M6 6l12 12M18 6L6 18" />
      </svg>
    </button>
  );
}

// ---- notification A: the approval card
function ApprovalView({ a, onClose, onApproved }) {
  const { approveVisit, declineVisit } = useAppointments();
  const raw = a.raw;
  const approval = raw.approval ?? {};
  const clinic = approval.clinic ?? raw.clinic ?? {};
  const finding = approval.finding ?? {};
  const shared = approval.shared_fields ?? {};
  const missing = approval.missing_profile_fields ?? [];
  const backups = (approval.candidates ?? [])
    .filter((c) => c.rank > 1)
    .map((c) => c.summary?.name ?? c.clinic?.name)
    .filter(Boolean);
  const [busy, setBusy] = useState(null); // "approve" | "decline"
  const [error, setError] = useState("");

  const act = async (what) => {
    setBusy(what);
    setError("");
    try {
      const updated = what === "approve" ? await approveVisit(a.id) : await declineVisit(a.id);
      if (what === "decline") onClose();
      else onApproved(updated.backendStatus);
    } catch (e) {
      setError(
        e?.status === 409
          ? "This request was already handled (maybe from the e-mail). Close and check the list."
          : what === "approve"
            ? "Couldn't send the request. Try again in a moment."
            : "Couldn't close the request. Try again in a moment."
      );
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="sheet-body">
      <header className="sheet-head">
        <div>
          <em className="badge requested">Your approval is needed</em>
          <h2 id="agent-sheet-title" className="book-title">
            {approval.reapproval_reason ? `New times for ${clinic.name}` : `Approve the request to ${clinic.name}?`}
          </h2>
          <p className="book-sub">
            {approval.reapproval_reason
              ? "The times you approved are no longer available, so CareSync picked three new ones."
              : `CareSync suggests seeing ${SPECIALIST[clinic.specialty] ?? "a clinic"} and found one near you.`}{" "}
            Nothing is sent until you approve.
          </p>
        </div>
        <CloseButton onClose={onClose} />
      </header>

      {(finding.headline || finding.points?.length > 0) && (
        <section className="agent-why">
          <h3>Why this was flagged</h3>
          {finding.headline && <p>{finding.headline}</p>}
          {finding.points?.length > 0 && (
            <ul>
              {finding.points.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          <small>The priority score reflects which follow-up rules fired, not the chance that you have a condition.</small>
        </section>
      )}

      <dl className="sheet-rows agent-rows">
        <div>
          <dt>Clinic</dt>
          <dd>
            {clinic.name}
            <small>{[clinic.specialty?.replace(/_/g, " "), clinic.address, distance(clinic)].filter(Boolean).join(" · ")}</small>
            {clinic.address && (
              <a className="autobook-map" href={mapsUrl(clinic)} target="_blank" rel="noopener noreferrer">
                Open in Maps
              </a>
            )}
          </dd>
        </div>
        <div>
          <dt>Proposed times</dt>
          <dd>
            <ul className="agent-slots">
              {(approval.slots ?? []).map((s) => (
                <li key={s.start}>{s.label_user ?? s.label}</li>
              ))}
            </ul>
            <small>The clinic picks one. If none works for them, we ask you before accepting another.</small>
          </dd>
        </div>
        <div>
          <dt>How we'll contact them</dt>
          <dd>{planSentence(approval.channel_plan)}</dd>
        </div>
        <div>
          <dt>The clinic receives</dt>
          <dd>
            <ul className="agent-shared">
              {SHARED.filter(([k]) => shared[k]).map(([k, label]) => (
                <li key={k}>
                  <span>{label}</span>
                  {sharedValue(k, shared[k])}
                </li>
              ))}
              {shared.report_url && (
                <li>
                  <span>Report link</span>a secure link to your referral report
                </li>
              )}
            </ul>
            <small>Never your measurements, diagnoses, medications or health-card number.</small>
          </dd>
        </div>
      </dl>

      {missing.length > 0 && (
        <p className="agent-note warn">
          Your profile is missing your {missing.map((m) => PROFILE_FIELDS[m] ?? m).join(", ")}. The clinic may ask for it; CareSync will say
          you can provide it.
        </p>
      )}
      {backups.length > 0 && (
        <p className="agent-note">
          If {clinic.name} can't book you, CareSync tries {backups.join(" and then ")} with the same details and times, without asking again.
        </p>
      )}
      {approval.referral_note && <p className="agent-note">{approval.referral_note}</p>}
      {approval.target_mode === "sandbox" && (
        <p className="agent-note warn">Demo mode: the request goes to a test inbox or test number controlled by the team, never to the real clinic.</p>
      )}

      {error && (
        <p className="file-error" role="alert">
          {error}
        </p>
      )}

      <div className="book-actions autobook-actions">
        <button type="button" className="btn-ghost" onClick={() => act("decline")} disabled={busy !== null}>
          {busy === "decline" ? "Closing…" : "Not now"}
        </button>
        <button type="button" className="btn-primary" onClick={() => act("approve")} disabled={busy !== null}>
          {busy === "approve" ? "Sending…" : "Approve request"}
        </button>
      </div>
      <p className="autobook-note">You also got this request by e-mail; its buttons do the same thing.</p>
    </div>
  );
}

// ---- notification B: confirmed + the calendar step
function ConfirmedView({ a, onClose }) {
  const { calendarDecision } = useAppointments();
  const raw = a.raw;
  const clinic = raw.clinic ?? {};
  const when = whenOf(raw.chosen_slot);
  const [sync, setSync] = useState(null); // from GET /calendar-sync
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    getCalendarSync(a.id)
      .then((s) => !cancelled && setSync(s))
      .catch(() => !cancelled && setSync({ status: a.calendarStatus, mode: "manual" }));
    return () => {
      cancelled = true;
    };
  }, [a.id]);

  const decide = async (decision) => {
    setBusy(true);
    setError("");
    try {
      setSync(await calendarDecision(a.id, decision));
    } catch (e) {
      setError(
        e?.status === 409
          ? "Google Calendar isn't connected, so use the invitation file or the Google link below."
          : "Couldn't update your calendar. Try again in a moment."
      );
    } finally {
      setBusy(false);
    }
  };

  const status = sync?.status ?? a.calendarStatus;
  return (
    <div className="sheet-body">
      <header className="sheet-head">
        <div>
          <span className="book-check" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M5 12.5l4.5 4.5L19 7.5" />
            </svg>
          </span>
          <h2 id="agent-sheet-title" className="book-title">
            Appointment confirmed
          </h2>
          <p className="book-sub">{clinic.name} confirmed a time.</p>
        </div>
        <CloseButton onClose={onClose} />
      </header>

      <dl className="sheet-rows agent-rows">
        {when && (
          <div>
            <dt>When</dt>
            <dd>
              {when.day}
              <small>{when.time} · about 30 min, arrive 10 min early</small>
            </dd>
          </div>
        )}
        <div>
          <dt>Where</dt>
          <dd>
            {clinic.name}
            {clinic.address && <small>{clinic.address}</small>}
            {clinic.phone && (
              <small>
                <a href={`tel:${clinic.phone.replace(/[^\d+]/g, "")}`}>{clinic.phone}</a>
              </small>
            )}
            {clinic.address && (
              <a className="autobook-map" href={mapsUrl(clinic)} target="_blank" rel="noopener noreferrer">
                Get directions
              </a>
            )}
          </dd>
        </div>
        <div>
          <dt>Bring</dt>
          <dd>
            Your health card
            <small>a list of your medications, and your questions</small>
          </dd>
        </div>
      </dl>

      <section className={`agent-calendar ${status}`}>
        {status === "added" ? (
          <>
            <h3>In your Google Calendar</h3>
            <p>Added as a confirmed event with a reminder one hour before.</p>
          </>
        ) : (
          <>
            <h3>{status === "skipped" ? "Add it to your calendar" : "Add it to your calendar?"}</h3>
            <p>
              {sync?.mode === "google"
                ? "CareSync never changes your calendar without your OK. Tap Add and it goes into your Google Calendar."
                : "Your Google Calendar isn't connected, so nothing is added automatically. Use the invitation file or open it in Google Calendar."}
            </p>
            {error && (
              <p className="file-error" role="alert">
                {error}
              </p>
            )}
            <div className="agent-calendar-actions">
              {sync?.mode === "google" ? (
                <button type="button" className="btn-primary" onClick={() => decide("add")} disabled={busy || !sync}>
                  {busy ? "Adding…" : "Add to Google Calendar"}
                </button>
              ) : (
                sync?.google_url && (
                  <a className="btn-primary agent-link-btn" href={sync.google_url} target="_blank" rel="noopener noreferrer">
                    Open in Google Calendar
                  </a>
                )
              )}
              {sync?.ics_url && (
                <a className="btn-secondary agent-link-btn" href={sync.ics_url}>
                  Download invitation (.ics)
                </a>
              )}
              {status === "pending" && (
                <button type="button" className="btn-ghost" onClick={() => decide("skip")} disabled={busy}>
                  Not now
                </button>
              )}
            </div>
          </>
        )}
      </section>

      <div className="book-actions">
        <button type="button" className="btn-primary" onClick={onClose}>
          Done
        </button>
      </div>
      <p className="autobook-note">Need to move it? Call the clinic{clinic.phone ? ` at ${clinic.phone}` : ""}, or cancel from the ⋮ menu.</p>
    </div>
  );
}

// ---- everything in between
function StatusView({ a, onClose }) {
  const raw = a.raw;
  const clinic = raw.clinic ?? {};
  const approval = raw.approval ?? {};
  const q = raw.pending_question;
  return (
    <div className="sheet-body">
      <header className="sheet-head">
        <div>
          <em className={`badge ${raw.status === "needs_user_info" ? "watch" : "requested"}`}>{a.status}</em>
          <h2 id="agent-sheet-title" className="book-title">
            {TITLE[raw.status] ?? a.status}
          </h2>
          <p className="book-sub">{NEXT[raw.status] ?? ""}</p>
        </div>
        <CloseButton onClose={onClose} />
      </header>
      {q && (
        <section className="agent-why">
          <h3>{q.type === "counter_offer" ? "The clinic suggests another time" : "The clinic asks"}</h3>
          <p>{q.question}</p>
          <small>
            {q.reply_path === "email"
              ? "Reply to the clinic's e-mail thread, or call the clinic, and CareSync will record the outcome."
              : "Please call the clinic back; CareSync can't answer this for you."}
          </small>
        </section>
      )}
      <dl className="sheet-rows agent-rows">
        <div>
          <dt>Clinic</dt>
          <dd>
            {clinic.name}
            <small>{[clinic.address, clinic.phone].filter(Boolean).join(" · ")}</small>
          </dd>
        </div>
        {(approval.slots ?? raw.proposed_slots ?? []).length > 0 && (
          <div>
            <dt>Proposed times</dt>
            <dd>
              <ul className="agent-slots">
                {(approval.slots ?? raw.proposed_slots).map((s) => (
                  <li key={s.start}>{s.label_user ?? s.label ?? new Date(s.start).toLocaleString("en-US")}</li>
                ))}
              </ul>
            </dd>
          </div>
        )}
        {raw.channel && (
          <div>
            <dt>Contacted by</dt>
            <dd>{CHANNEL[raw.channel] ?? raw.channel}</dd>
          </div>
        )}
      </dl>
      <div className="book-actions">
        <button type="button" className="btn-primary" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

// What the user sees right after approving, before the sheet closes.
function ApprovedView({ a, result, onClose }) {
  const name = a.raw?.clinic?.name ?? "the clinic";
  const text =
    {
      requested: `We're contacting ${name} now. We'll let you know, here and by e-mail, as soon as the clinic replies.`,
      confirmed: `${name} confirmed a time straight away. Open the appointment for the details and to add it to your calendar.`,
      needs_user_info: `${name} has a question for you. Open the appointment to see it.`,
      awaiting_approval: "The first clinics couldn't be reached, so CareSync prepared a new request for you to approve.",
      failed: "CareSync couldn't reach any of the clinics. You can still book a visit yourself from this page.",
    }[result] ?? "Done.";
  return (
    <div className="sheet-body book-done">
      <span className="book-check" aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <path d="M5 12.5l4.5 4.5L19 7.5" />
        </svg>
      </span>
      <h2 id="agent-sheet-title" className="book-title">
        Request approved
      </h2>
      <p className="book-sub">{text}</p>
      <button type="button" className="btn-primary" onClick={onClose}>
        Done
      </button>
    </div>
  );
}

export default function AgentAppointmentSheet({ appointment, onClose }) {
  const ref = useRef(null);
  const open = Boolean(appointment);
  const [approved, setApproved] = useState(null); // { id, result } after approving in this sheet

  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
    if (!open) setApproved(null);
  }, [open]);

  const status = appointment?.raw?.status;
  const justApproved = approved && approved.id === appointment?.id;
  return (
    <dialog
      ref={ref}
      className="sheet book-sheet autobook-sheet agent-sheet"
      aria-labelledby="agent-sheet-title"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      {appointment &&
        (justApproved ? (
          <ApprovedView a={appointment} result={approved.result} onClose={onClose} />
        ) : status === "awaiting_approval" ? (
          <ApprovalView a={appointment} onClose={onClose} onApproved={(result) => setApproved({ id: appointment.id, result })} />
        ) : status === "confirmed" ? (
          <ConfirmedView a={appointment} onClose={onClose} />
        ) : (
          <StatusView a={appointment} onClose={onClose} />
        ))}
    </dialog>
  );
}
