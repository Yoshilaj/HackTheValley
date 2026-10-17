import { useEffect, useRef, useState } from "react";
import { pharmacies } from "../data/placeholder.js";

// Glass sheet behind "Request refill", in steps:
//   form    - which medications (low ones pre-ticked), pharmacy, pickup or delivery
//   confirm - "Send refill request?" final check
//   done    - sent
// onSubmit({ medicationIds, pharmacyId, method, note }) may throw.
// preselect: medication ids to tick on open (defaults to the ones running low).
const METHODS = [
  { id: "pickup", label: "Pick up" },
  { id: "delivery", label: "Delivery" },
];

export default function RefillSheet({ open, onClose, medications, isRequested, onSubmit, preselect }) {
  const ref = useRef(null);
  const [step, setStep] = useState("form");
  const [selected, setSelected] = useState([]);
  const [pharmacyId, setPharmacyId] = useState(pharmacies[0].id);
  const [method, setMethod] = useState("pickup");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");

  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog.open) {
      setStep("form");
      setSelected(
        preselect ?? medications.filter((m) => m.refills <= 1 && !isRequested(m.id)).map((m) => m.id)
      );
      setNote("");
      setError("");
      setSendError("");
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
    // Only on open/close; the lists are read fresh at that moment.
  }, [open]);

  const chosen = medications.filter((m) => selected.includes(m.id));
  const pharmacy = pharmacies.find((p) => p.id === pharmacyId);

  const toggle = (id) => {
    setError("");
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  };

  const review = (e) => {
    e.preventDefault();
    if (!selected.length) {
      setError("Choose at least one medication.");
      return;
    }
    setStep("confirm");
  };

  const send = async () => {
    setSending(true);
    setSendError("");
    try {
      await onSubmit({ medicationIds: selected, pharmacyId, method, note: note.trim() || undefined });
      setStep("done");
    } catch {
      setSendError("Couldn't send the request. Try again in a moment.");
    } finally {
      setSending(false);
    }
  };

  const meds = chosen.map((m) => `${m.name} ${m.dose}`).join(", ");

  return (
    <dialog
      ref={ref}
      className={`sheet book-sheet ${step === "confirm" ? "confirming" : ""}`}
      aria-labelledby="refill-title"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      {open && step === "form" && (
        <form className="sheet-body" onSubmit={review} noValidate>
          <header className="sheet-head">
            <div>
              <h2 id="refill-title" className="book-title">
                Request a refill
              </h2>
              <p className="book-sub">Your pharmacy and prescriber get the request together.</p>
            </div>
            <button type="button" className="sheet-close" aria-label="Close" onClick={onClose}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </header>

          <fieldset className="book-field" aria-describedby={error ? "refill-error" : undefined}>
            <legend>Medications</legend>
            <ul className="choice-list">
              {medications.map((m) => {
                const requested = isRequested(m.id);
                return (
                  <li key={m.id}>
                    <label className={`choice ${requested ? "disabled" : ""}`}>
                      <input
                        type="checkbox"
                        checked={selected.includes(m.id)}
                        disabled={requested}
                        onChange={() => toggle(m.id)}
                      />
                      <span className="choice-text">
                        <b>
                          {m.name} {m.dose}
                        </b>
                        <small>
                          {requested
                            ? "Refill already requested"
                            : `${m.refills} refill${m.refills === 1 ? "" : "s"} left · ${m.prescriber}`}
                        </small>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
            {error && (
              <p id="refill-error" className="field-error" role="alert">
                {error}
              </p>
            )}
          </fieldset>

          <fieldset className="book-field">
            <legend>Pharmacy</legend>
            <ul className="choice-list">
              {pharmacies.map((p) => (
                <li key={p.id}>
                  <label className="choice">
                    <input
                      type="radio"
                      name="refill-pharmacy"
                      checked={pharmacyId === p.id}
                      onChange={() => setPharmacyId(p.id)}
                    />
                    <span className="choice-text">
                      <b>{p.name}</b>
                      <small>
                        {p.address} · {p.hours}
                      </small>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>

          <fieldset className="book-field">
            <legend>Pick up or delivery</legend>
            <div className="segmented">
              {METHODS.map((m) => (
                <label key={m.id}>
                  <input type="radio" name="refill-method" checked={method === m.id} onChange={() => setMethod(m.id)} />
                  <span>{m.label}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <div className="form-field">
            <label htmlFor="refill-note">
              Note for the pharmacy <span className="book-optional">Optional</span>
            </label>
            <input
              id="refill-note"
              className="text-input"
              type="text"
              maxLength={140}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. I'm down to my last 3 tablets"
            />
          </div>

          <div className="book-actions">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary">
              Review request
            </button>
          </div>
        </form>
      )}

      {/* Final check, Apple alert style */}
      {open && step === "confirm" && (
        <div className="sheet-body confirm-body">
          <h2 id="refill-title" className="confirm-title">
            Send refill request?
          </h2>
          <p className="confirm-desc">
            {meds}
            <br />
            <b className="confirm-when">
              {method === "pickup" ? "Pick up at" : "Delivery from"} {pharmacy.name}
            </b>
          </p>
          {sendError && (
            <p className="file-error" role="alert">
              {sendError}
            </p>
          )}
          <div className="confirm-actions">
            <button type="button" className="btn-secondary" onClick={() => setStep("form")} disabled={sending}>
              Go back
            </button>
            <button type="button" className="btn-primary" onClick={send} disabled={sending} autoFocus>
              {sending ? "Sending…" : "Send request"}
            </button>
          </div>
        </div>
      )}

      {open && step === "done" && (
        <div className="sheet-body book-done" role="status">
          <span className="book-check" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M5 12.5l4.5 4.5L19 7.5" />
            </svg>
          </span>
          <h2 id="refill-title" className="book-title">
            Refill requested
          </h2>
          <p>{meds}</p>
          <p className="book-meta">
            {pharmacy.name} and your prescriber have your request. You'll get a notification when it's ready
            {method === "pickup" ? " to pick up" : " to deliver"}.
          </p>
          <button type="button" className="btn-primary" onClick={onClose}>
            Done
          </button>
        </div>
      )}
    </dialog>
  );
}
