import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { HOLD_HOURS, useCareAlert } from "../context/CareAlertContext.jsx";
import { describeSlot } from "../utils/slots.js";
import { PATHWAYS, riskLevel } from "../hooks/useRiskAssessment.js";

// Pop-up when the risk score reaches the auto-booking threshold. Native
// <dialog> like DetailSheet.
//   Backend on: the AI booking agent's request (AgentBooking below): it finds a
//   clinic, then the user picks one of its three booking channels - online
//   form, e-mail or phone call - whichever can reach that clinic.
//   Backend off: a visit held in this browser; "Confirm booking" books it.

// The agent's three ways to reach a clinic, in the order it would try them.
const CHANNELS = {
  web: {
    label: "Online booking form",
    short: "online form",
    how: "CareSync's agent fills in the clinic's booking form for you.",
  },
  email: {
    label: "E-mail",
    short: "e-mail",
    how: "CareSync e-mails the clinic your request and the times. The clinic replies to confirm.",
  },
  phone: {
    label: "Phone call",
    short: "phone",
    how: "CareSync's AI voice agent calls the clinic and asks for one of the times.",
  },
};

// Backend reasons a channel can't be used -> plain words (anything else as sent).
const UNAVAILABLE = [
  [/sandbox/i, "No test clinic set up for this in demo mode"],
  [/not configured/i, "Not set up in this CareSync"],
  [/captcha|login/i, "The clinic's form needs a login or a CAPTCHA"],
  [/no website|no booking form|website blocked|website not checked/i, "No online booking form found for this clinic"],
  [/no e-?mail/i, "No e-mail address for this clinic"],
  [/no phone/i, "No phone number for this clinic"],
];
const unavailableText = (reason) => UNAVAILABLE.find(([re]) => re.test(reason ?? ""))?.[1] ?? reason ?? "Not available";

const mapsLink = (c) =>
  `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent([c?.name, c?.address].filter(Boolean).join(", "))}`;
const kmAway = (c) => (typeof c?.distance_km === "number" ? `${c.distance_km.toFixed(1)} km away` : null);

function AgentBooking({ alert, onClose }) {
  const { appointment: appt, allowSharing, bookBy, decline, retry, confirming, error } = useCareAlert();
  const navigate = useNavigate();
  const approval = appt?.approval ?? {};
  const clinic = approval.clinic ?? appt?.clinic ?? {};
  const plan = approval.channel_plan ?? [];
  const usable = plan.filter((p) => p.available);
  const [choice, setChoice] = useState(null);
  const chosen = choice && usable.some((p) => p.channel === choice) ? choice : usable[0]?.channel;
  const area = PATHWAYS[alert.pathway] ?? { name: "Your health", specialty: "a doctor" };
  const finding = approval.finding ?? {};
  const why = finding.points?.length ? finding.points.join(" ") : finding.headline;
  const demo = plan.some((p) => p.redirected);

  const bookYourself = () => {
    onClose();
    navigate("/appointments#book");
  };

  const head = {
    planning: ["Finding a clinic for you", `CareSync's booking agent is looking for ${area.specialty.toLowerCase()} near you, picking times and checking how to reach the clinic. This can take up to a minute.`],
    consent: ["Allow CareSync to contact a clinic?", "To book for you, CareSync's agent sends the clinic your name, date of birth, contact details, postal code, the reason for the visit and a link to your report."],
    choose: ["How should CareSync book?", `CareSync found ${clinic.name ?? "a clinic"}. Pick how its agent should ask for one of the times below. Nothing is sent until you choose.`],
    sent: [appt?.status === "confirmed" ? "Appointment confirmed" : "Request sent", null],
    failed: ["CareSync couldn't book automatically", null],
    declined: ["Not now", "Nothing was sent. You can book a visit yourself any time."],
  }[alert.status] ?? ["Flagged for follow-up", null];

  const sentBy = CHANNELS[alert.channel ?? appt?.channel]?.short;

  return (
    <div className="sheet-body">
      <header className="sheet-head">
        <div>
          <em className="badge alert">
            {area.name} risk {alert.score} · {riskLevel(alert.score)}
          </em>
          <h2 id="autobook-title" className="book-title">
            {head[0]}
          </h2>
          {head[1] && <p className="book-sub">{head[1]}</p>}
        </div>
        <button className="sheet-close" aria-label="Close" onClick={onClose}>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </header>

      {alert.status === "planning" && (
        <p className="autobook-progress" role="status">
          <span className="reply-pulse" aria-hidden="true" />
          Searching nearby clinics and their booking options…
        </p>
      )}

      {alert.status === "consent" && (
        <>
          <p className="autobook-note">
            Never your measurements, diagnoses, medications or health-card number. You approve the clinic and the way
            it's contacted before anything is sent.
          </p>
          <div className="book-actions autobook-actions">
            <button type="button" className="btn-ghost" onClick={decline}>
              Not now
            </button>
            <button type="button" className="btn-primary" onClick={allowSharing} disabled={confirming}>
              {confirming ? "Saving…" : "Allow and continue"}
            </button>
          </div>
        </>
      )}

      {alert.status === "choose" && (
        <>
          {why && (
            <div className="autobook-reason">
              <h3>Why this was flagged</h3>
              <p>{why}</p>
            </div>
          )}
          <dl className="sheet-rows">
            <div>
              <dt>Clinic</dt>
              <dd>
                {clinic.name}
                <small>{[clinic.address, kmAway(clinic)].filter(Boolean).join(" · ")}</small>
                <a className="autobook-map" href={mapsLink(clinic)} target="_blank" rel="noopener noreferrer">
                  Open in Maps
                </a>
              </dd>
            </div>
            <div>
              <dt>Times to ask for</dt>
              <dd>
                {(approval.slots ?? []).map((s) => (
                  <span key={s.start} className="autobook-time">
                    {s.label_user ?? s.label}
                  </span>
                ))}
                <small>The clinic picks one.</small>
              </dd>
            </div>
          </dl>

          <fieldset className="autobook-channels">
            <legend>Choose how to book</legend>
            {plan.map((p) => {
              const c = CHANNELS[p.channel] ?? { label: p.channel, how: "" };
              return (
                <label key={p.channel} className={`autobook-channel ${p.available ? "" : "off"}`}>
                  <input
                    type="radio"
                    name="autobook-channel"
                    value={p.channel}
                    checked={chosen === p.channel}
                    disabled={!p.available}
                    onChange={() => setChoice(p.channel)}
                  />
                  <span>
                    <strong>{c.label}</strong>
                    <small>{p.available ? c.how : unavailableText(p.reason)}</small>
                  </span>
                </label>
              );
            })}
          </fieldset>
          {demo && (
            <p className="autobook-note">Demo mode: CareSync contacts a test clinic instead of the real one.</p>
          )}
          {usable.length === 0 && (
            <p className="file-error" role="alert">
              None of the three ways can reach this clinic. You can book a visit yourself instead.
            </p>
          )}
        </>
      )}

      {alert.status === "sent" && (
        <p className="book-sub">
          {appt?.status === "confirmed"
            ? `${clinic.name ?? "The clinic"} confirmed your visit${sentBy ? ` (booked by ${sentBy})` : ""}. It's in Appointments.`
            : `CareSync contacted ${clinic.name ?? "the clinic"}${sentBy ? ` by ${sentBy}` : ""}. We'll tell you as soon as it replies.`}
        </p>
      )}

      {error && (
        <p className="file-error" role="alert">
          {error}
        </p>
      )}

      {alert.status === "choose" && (
        <div className="book-actions autobook-actions">
          <button type="button" className="btn-ghost" onClick={decline} disabled={confirming}>
            Not now
          </button>
          {usable.length === 0 ? (
            <button type="button" className="btn-primary" onClick={bookYourself}>
              Book yourself
            </button>
          ) : (
            <button type="button" className="btn-primary" onClick={() => bookBy(chosen)} disabled={confirming || !chosen}>
              {confirming ? "Contacting the clinic…" : `Book by ${CHANNELS[chosen]?.short ?? chosen}`}
            </button>
          )}
        </div>
      )}
      {alert.status === "failed" && (
        <div className="book-actions autobook-actions">
          <button type="button" className="btn-secondary" onClick={retry}>
            Try again
          </button>
          <button type="button" className="btn-primary" onClick={bookYourself}>
            Book yourself
          </button>
        </div>
      )}
      {(alert.status === "sent" || alert.status === "declined") && (
        <div className="book-actions">
          <button type="button" className="btn-primary" onClick={onClose}>
            Done
          </button>
        </div>
      )}

      <p className="autobook-note">
        This isn't a diagnosis. If you feel very unwell, call your local emergency number.
      </p>
    </div>
  );
}

export default function AutoBookSheet() {
  const { alert, promptOpen, closePrompt, confirm, decline, confirming, error } = useCareAlert();
  const ref = useRef(null);
  const navigate = useNavigate();
  const open = Boolean(promptOpen && alert);

  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const pickAnother = () => {
    closePrompt();
    navigate("/appointments#book");
  };

  if (alert?.kind === "agent") {
    return (
      <dialog
        ref={ref}
        className="sheet book-sheet autobook-sheet"
        aria-labelledby="autobook-title"
        onClose={closePrompt}
        onClick={(e) => e.target === ref.current && closePrompt()}
      >
        {open && <AgentBooking alert={alert} onClose={closePrompt} />}
      </dialog>
    );
  }

  const confirmed = alert?.status === "confirmed";
  const s = alert && describeSlot(alert.slot);
  const heldUntil =
    alert &&
    new Date(alert.createdAt + HOLD_HOURS * 3600 * 1000).toLocaleString("en-US", {
      weekday: "short",
      hour: "numeric",
      minute: "2-digit",
    });
  const mapsUrl =
    alert &&
    `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${alert.rec.clinic}, ${alert.rec.address}`)}`;

  return (
    <dialog
      ref={ref}
      className="sheet book-sheet autobook-sheet"
      aria-labelledby="autobook-title"
      onClose={closePrompt}
      onClick={(e) => e.target === ref.current && closePrompt()}
    >
      {alert && (
        <div className="sheet-body">
          <header className="sheet-head">
            <div>
              {confirmed ? (
                <span className="book-check" aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    <path d="M5 12.5l4.5 4.5L19 7.5" />
                  </svg>
                </span>
              ) : (
                <em className="badge alert">
                  Overall risk {alert.score} · {riskLevel(alert.score)}
                </em>
              )}
              <h2 id="autobook-title" className="book-title">
                {confirmed ? "Appointment confirmed" : "Flagged for follow-up"}
              </h2>
              {!confirmed && (
                <p className="book-sub">
                  CareSync has held a visit with {alert.rec.name}. Nothing is booked until you confirm.
                </p>
              )}
            </div>
            <button className="sheet-close" aria-label="Close" onClick={closePrompt}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </header>

          {!confirmed && (
            <div className="autobook-reason">
              <h3>Why this was flagged</h3>
              <p>{alert.suspected.why}</p>
            </div>
          )}

          <dl className="sheet-rows">
            <div>
              <dt>Possible condition</dt>
              <dd>{alert.suspected.condition}</dd>
            </div>
            <div>
              <dt>Doctor</dt>
              <dd>
                {alert.rec.name}
                <small>{alert.rec.specialty}</small>
              </dd>
            </div>
            <div>
              <dt>When</dt>
              <dd>
                {s.day}, {s.date} · {s.time}
                <small>About 30 min, arrive 10 min early</small>
              </dd>
            </div>
            <div>
              <dt>Where</dt>
              <dd>
                {alert.rec.clinic}
                <small>
                  {alert.rec.address} · {alert.rec.distance}
                </small>
                <a className="autobook-map" href={mapsUrl} target="_blank" rel="noopener noreferrer">
                  Open in Maps
                </a>
              </dd>
            </div>
            {!confirmed && (
              <div>
                <dt>Held until</dt>
                <dd>{heldUntil}</dd>
              </div>
            )}
            <div>
              <dt>Bring</dt>
              <dd>{alert.suspected.bring}</dd>
            </div>
          </dl>

          {error && (
            <p className="file-error" role="alert">
              {error}
            </p>
          )}

          {confirmed ? (
            <div className="book-actions">
              <button type="button" className="btn-primary" onClick={closePrompt}>
                Done
              </button>
            </div>
          ) : (
            <div className="book-actions autobook-actions">
              <button type="button" className="btn-ghost" onClick={decline}>
                Not now
              </button>
              <button type="button" className="btn-secondary" onClick={pickAnother}>
                Pick another time
              </button>
              <button type="button" className="btn-primary" onClick={confirm} disabled={confirming}>
                {confirming ? "Confirming…" : "Confirm booking"}
              </button>
            </div>
          )}

          <p className="autobook-note">
            This isn't a diagnosis. If you feel very unwell, call your local emergency number.
          </p>
        </div>
      )}
    </dialog>
  );
}
