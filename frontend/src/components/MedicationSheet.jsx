import { useEffect, useRef } from "react";
import { pharmacies } from "../data/placeholder.js";
import { relativeDay } from "../utils/dates.js";

// Glass sheet behind a medication card: how and when to take it, supply left,
// common side effects, what to check before mixing it with, and its refill
// request (send one, or cancel the open one). Native <dialog> like DetailSheet.
// request: the open refill request for this medication, or null.
export default function MedicationSheet({ med, request, onClose, onRequestRefill, onCancelRequest }) {
  const ref = useRef(null);

  useEffect(() => {
    const dialog = ref.current;
    if (med && !dialog.open) dialog.showModal();
    if (!med && dialog.open) dialog.close();
  }, [med]);

  const m = med;
  const runsOut =
    m?.daysLeft != null &&
    new Date(Date.now() + m.daysLeft * 24 * 60 * 60 * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const pharmacy = request && pharmacies.find((p) => p.id === request.pharmacyId);

  return (
    <dialog
      ref={ref}
      className="sheet book-sheet med-sheet"
      aria-labelledby="med-title"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      {m && (
        <div className="sheet-body">
          <header className="sheet-head">
            <div className="med-sheet-who">
              <span className="med-glyph" aria-hidden="true">
                {m.glyph}
              </span>
              <div>
                <h2 id="med-title" className="book-title">
                  {m.name} <span className="med-sheet-dose">{m.dose}</span>
                </h2>
                <p className="book-sub">For {m.purpose.toLowerCase()}</p>
              </div>
            </div>
            <button className="sheet-close" aria-label="Close" onClick={onClose}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </header>

          <dl className="sheet-rows">
            <div>
              <dt>How often</dt>
              <dd>{m.frequency}</dd>
            </div>
            <div>
              <dt>Today</dt>
              <dd>{m.times.join(", ")}</dd>
            </div>
            {m.daysLeft != null && (
              <div>
                <dt>Supply</dt>
                <dd>
                  {m.daysLeft} days left · runs out {runsOut}
                </dd>
              </div>
            )}
            <div>
              <dt>Refills left</dt>
              <dd>{m.refills}</dd>
            </div>
            <div>
              <dt>Prescribed by</dt>
              <dd>{m.prescriber}</dd>
            </div>
          </dl>

          {m.takeWith && (
            <section className="med-section">
              <h3 className="sheet-sub">How to take it</h3>
              <p>{m.takeWith}</p>
            </section>
          )}
          {m.sideEffects?.length > 0 && (
            <section className="med-section">
              <h3 className="sheet-sub">Common side effects</h3>
              <ul className="sheet-tips">
                {m.sideEffects.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </section>
          )}
          {m.interactions?.length > 0 && (
            <section className="med-section">
              <h3 className="sheet-sub">Check with your pharmacist before taking it with</h3>
              <ul className="sheet-tips">
                {m.interactions.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </section>
          )}

          {request ? (
            <div className="med-refill">
              <p>
                <b>Refill requested {relativeDay(request.requestedAt)}</b>
                {pharmacy && (
                  <small>
                    {request.method === "delivery" ? "Delivery from" : "Pick up at"} {pharmacy.name}
                  </small>
                )}
              </p>
              <button type="button" className="btn-secondary btn-secondary-danger" onClick={onCancelRequest} aria-haspopup="dialog">
                Cancel request
              </button>
            </div>
          ) : (
            <div className="med-refill">
              <p>
                <b>{m.refills === 0 ? "No refills left" : `${m.refills} refill${m.refills === 1 ? "" : "s"} left`}</b>
                {m.refills === 0 && <small>The request goes to {m.prescriber} for a new prescription.</small>}
              </p>
              <button type="button" className="btn-primary" onClick={onRequestRefill} aria-haspopup="dialog">
                Request refill
              </button>
            </div>
          )}

          <p className="autobook-note">General information, not medical advice. Your pharmacist can tell you more.</p>
        </div>
      )}
    </dialog>
  );
}
