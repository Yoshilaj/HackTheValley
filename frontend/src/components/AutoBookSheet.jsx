import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { HOLD_HOURS, useCareAlert } from "../context/CareAlertContext.jsx";
import { describeSlot } from "../utils/slots.js";
import { riskLevel } from "../hooks/useRiskAssessment.js";

// Pop-up for the visit CareSync held at high risk. Shows why, where and when,
// and only books once the user confirms. Native <dialog> like DetailSheet.
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
