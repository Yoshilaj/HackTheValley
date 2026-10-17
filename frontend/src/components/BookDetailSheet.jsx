import { useEffect, useRef, useState } from "react";
import { useAppointments } from "../context/AppointmentsContext.jsx";
import { describeSlot, slotLabel } from "../utils/slots.js";

// Glass sheet behind a card's "Book" button, in three steps:
//   details - who, why, where, and the open times to pick from
//   confirm - "Book this appointment?" final check
//   done    - booked, now in Upcoming
// Native <dialog> like DetailSheet.
export default function BookDetailSheet({ provider, onClose }) {
  const ref = useRef(null);
  const { bookVisit } = useAppointments();
  const [step, setStep] = useState("details");
  const [slot, setSlot] = useState(null);
  const [booking, setBooking] = useState(false);
  const [error, setError] = useState("");

  const p = provider;
  const slots = p?.slots?.length ? [...p.slots].sort() : [];

  useEffect(() => {
    const dialog = ref.current;
    if (p && !dialog.open) {
      setStep("details");
      setSlot(p.slots?.length ? [...p.slots].sort()[0] : null);
      setError("");
      dialog.showModal();
    }
    if (!p && dialog.open) dialog.close();
  }, [p]);

  const book = async () => {
    setBooking(true);
    setError("");
    try {
      await bookVisit(p, slot);
      setStep("done");
    } catch {
      setError("Couldn't book with the clinic. Try again in a moment.");
    } finally {
      setBooking(false);
    }
  };

  const when = slot ? slotLabel(slot) : p?.nextSlot ?? "Next available time";
  const place = p && [p.clinic, p.address].filter(Boolean).join(", ");
  const mapsUrl = place && `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(place)}`;

  return (
    <dialog
      ref={ref}
      className={`sheet book-sheet book-detail ${step === "confirm" ? "confirming" : ""}`}
      aria-labelledby="book-detail-title"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      {p && step === "details" && (
        <div className="sheet-body">
          <header className="sheet-head">
            <div className="book-detail-who">
              <span className="appt-icon" aria-hidden="true">
                {p.glyph ?? p.name.slice(0, 2).toUpperCase()}
              </span>
              <div>
                <h2 id="book-detail-title" className="book-title">
                  {p.name}
                </h2>
                <p className="book-sub">{[p.specialty, p.clinic].filter(Boolean).join(" · ")}</p>
              </div>
            </div>
            <button className="sheet-close" aria-label="Close" onClick={onClose}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </header>

          {p.reason && (
            <div className="book-detail-why">
              <h3>Why CareSync suggests this</h3>
              <p>{p.reason}</p>
            </div>
          )}

          <dl className="sheet-rows">
            {place && (
              <div>
                <dt>Where</dt>
                <dd>
                  {p.clinic ?? p.name}
                  {(p.address || p.distance) && <small>{[p.address, p.distance].filter(Boolean).join(" · ")}</small>}
                  <a className="autobook-map" href={mapsUrl} target="_blank" rel="noopener noreferrer">
                    Open in Maps
                  </a>
                </dd>
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
                  <a className="autobook-map" href={`tel:${p.phone.replace(/[^\d+]/g, "")}`}>
                    {p.phone}
                  </a>
                </dd>
              </div>
            )}
            <div>
              <dt>Visit length</dt>
              <dd>{p.length ?? "About 30 min"}</dd>
            </div>
          </dl>

          {slots.length > 0 && (
            <fieldset className="book-field">
              <legend>Pick a time</legend>
              <div className="book-chips book-slots">
                {slots.map((iso) => {
                  const s = describeSlot(iso);
                  return (
                    <button
                      key={iso}
                      type="button"
                      className="book-chip"
                      aria-pressed={slot === iso}
                      onClick={() => setSlot(iso)}
                    >
                      {s.day}, {s.date}
                      <small>{s.time}</small>
                    </button>
                  );
                })}
              </div>
            </fieldset>
          )}

          <div className="book-actions">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Not now
            </button>
            <button type="button" className="btn-primary" onClick={() => setStep("confirm")}>
              Book appointment
            </button>
          </div>
        </div>
      )}

      {/* Final check, Apple alert style */}
      {p && step === "confirm" && (
        <div className="sheet-body confirm-body">
          <h2 id="book-detail-title" className="confirm-title">
            Book this appointment?
          </h2>
          <p className="confirm-desc">
            {p.name}
            {p.clinic ? ` at ${p.clinic}` : ""}
            <br />
            <b className="confirm-when">{when}</b>
          </p>
          {error && (
            <p className="file-error" role="alert">
              {error}
            </p>
          )}
          <div className="confirm-actions">
            <button type="button" className="btn-secondary" onClick={() => setStep("details")} disabled={booking}>
              Go back
            </button>
            <button type="button" className="btn-primary" onClick={book} disabled={booking} autoFocus>
              {booking ? "Booking…" : "Yes, book"}
            </button>
          </div>
        </div>
      )}

      {p && step === "done" && (
        <div className="sheet-body book-done" role="status">
          <span className="book-check" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M5 12.5l4.5 4.5L19 7.5" />
            </svg>
          </span>
          <h2 id="book-detail-title" className="book-title">
            Appointment booked
          </h2>
          <p className="book-when">{when}</p>
          <p>
            {p.name}
            {p.clinic ? `, ${p.clinic}` : ""}
          </p>
          <p className="book-meta">
            Added to your upcoming appointments. CareSync will confirm with the clinic and send you a notification.
          </p>
          <button type="button" className="btn-primary" onClick={onClose}>
            Done
          </button>
        </div>
      )}
    </dialog>
  );
}
