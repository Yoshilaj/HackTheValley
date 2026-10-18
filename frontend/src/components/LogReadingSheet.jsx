import { useEffect, useRef, useState } from "react";
import { vitals } from "../data/placeholder.js";
import { BP_SESSIONS, VITAL_INPUTS, VITAL_ORDER, bpSessionFor } from "../utils/vitals.js";
import { toLocalInput } from "../utils/dates.js";

// Glass sheet behind "Log a reading": pick the vital, enter the value (two
// numbers for blood pressure), when it was taken and an optional note.
// Values are checked against sensible limits, with each error shown under
// its field. onSave({ vitalId, values, at, note, session, irregularPulse }) may
// throw. Blood pressure also asks which session the reading belongs to (morning
// or evening, the backend's home-BP protocol) and whether the cuff flagged an
// irregular pulse; both go to the backend with the reading.
// initial: { vitalId } to start on a vital, or one of the user's own readings
// ({ id, vitalId, values, at, note }) to edit it.
export default function LogReadingSheet({ open, initial, onClose, onSave }) {
  const editing = Boolean(initial?.id);
  const ref = useRef(null);
  const [vitalId, setVitalId] = useState("bp");
  const [raw, setRaw] = useState({});
  const [when, setWhen] = useState("");
  const [maxWhen, setMaxWhen] = useState("");
  const [note, setNote] = useState("");
  const [session, setSession] = useState("other"); // BP only: morning | evening | other
  const [sessionTouched, setSessionTouched] = useState(false);
  const [irregular, setIrregular] = useState(false); // BP only: cuff's irregular-pulse symbol
  const [errors, setErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog.open) {
      const now = toLocalInput(new Date());
      if (initial?.vitalId) setVitalId(initial.vitalId);
      setRaw(editing ? Object.fromEntries(Object.entries(initial.values).map(([k, v]) => [k, String(v)])) : {});
      setWhen(editing ? toLocalInput(new Date(initial.at)) : now);
      setMaxWhen(now);
      setNote(editing ? initial.note ?? "" : "");
      setSession(editing && initial.session ? initial.session : bpSessionFor(editing ? new Date(initial.at) : new Date()));
      setSessionTouched(Boolean(editing && initial.session));
      setIrregular(Boolean(editing && initial.irregularPulse));
      setErrors({});
      setSaveError("");
      dialog.showModal();
      // Start in the value box (autoFocus can't, as it runs before the dialog shows).
      dialog.querySelector(".reading-fields input")?.focus();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const input = VITAL_INPUTS[vitalId];
  const range = vitals.find((v) => v.id === vitalId)?.range;

  const pickVital = (id) => {
    setVitalId(id);
    setRaw({});
    setErrors({});
  };

  const validate = () => {
    const errs = {};
    const values = {};
    for (const f of input.fields) {
      const text = String(raw[f.key] ?? "").trim().replace(",", ".");
      const n = Number(text);
      if (!text && f.optional) continue;
      if (!text) errs[f.key] = "Enter a value.";
      else if (!Number.isFinite(n)) errs[f.key] = "Enter a number.";
      else if (n < f.min || n > f.max) errs[f.key] = `Enter a value between ${f.min} and ${f.max}.`;
      else values[f.key] = f.decimals ? Math.round(n * 10 ** f.decimals) / 10 ** f.decimals : Math.round(n);
    }
    if (vitalId === "bp" && values.sys != null && values.dia != null && values.dia >= values.sys) {
      errs.dia = "The bottom number should be lower than the top number.";
    }
    const at = new Date(when).getTime();
    if (!when || Number.isNaN(at)) errs.when = "Enter when you took this reading.";
    else if (at > Date.now() + 60 * 1000) errs.when = "That time hasn't happened yet.";
    return { errs, values, at };
  };

  const save = async (e) => {
    e.preventDefault();
    const { errs, values, at } = validate();
    setErrors(errs);
    const firstBad = Object.keys(errs)[0];
    if (firstBad) {
      document.getElementById(`reading-${firstBad}`)?.focus();
      return;
    }
    setSaving(true);
    setSaveError("");
    try {
      await onSave({
        vitalId,
        values,
        at,
        note: note.trim() || undefined,
        ...(vitalId === "bp" && { session, irregularPulse: irregular }),
      });
      onClose();
    } catch {
      setSaveError("Couldn't save this reading. Try again in a moment.");
    } finally {
      setSaving(false);
    }
  };

  const errorProps = (key) =>
    errors[key] ? { "aria-invalid": true, "aria-describedby": `reading-${key}-error` } : {};

  return (
    <dialog
      ref={ref}
      className="sheet book-sheet"
      aria-labelledby="reading-title"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      {open && (
        <form className="sheet-body" onSubmit={save} noValidate>
          <header className="sheet-head">
            <div>
              <h2 id="reading-title" className="book-title">
                {editing ? `Edit ${input.label.toLowerCase()} reading` : "Log a reading"}
              </h2>
              <p className="book-sub">
                {editing
                  ? "Fix a typo or the time. The tile and trend update to match."
                  : "Add a reading from your own device, like a blood pressure cuff or scale."}
              </p>
            </div>
            <button type="button" className="sheet-close" aria-label="Close" onClick={onClose}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </header>

          {/* The kind of reading is fixed when editing */}
          <fieldset className="book-field" hidden={editing}>
            <legend>What are you logging?</legend>
            <div className="book-chips">
              {VITAL_ORDER.map((id) => (
                <button
                  key={id}
                  type="button"
                  className="book-chip"
                  aria-pressed={vitalId === id}
                  onClick={() => pickVital(id)}
                >
                  {VITAL_INPUTS[id].label}
                </button>
              ))}
            </div>
          </fieldset>

          <div className={`reading-fields ${["", "", "two", "three"][input.fields.length] ?? "three"}`}>
            {input.fields.map((f) => (
              <div className="form-field" key={`${vitalId}-${f.key}`}>
                <label htmlFor={`reading-${f.key}`}>
                  {f.label}
                  {f.optional && <span className="book-optional">Optional</span>}
                </label>
                <div className={`input-unit ${errors[f.key] ? "invalid" : ""}`}>
                  <input
                    id={`reading-${f.key}`}
                    type="text"
                    inputMode={f.decimals ? "decimal" : "numeric"}
                    autoComplete="off"
                    value={raw[f.key] ?? ""}
                    onChange={(e) => setRaw((r) => ({ ...r, [f.key]: e.target.value }))}
                    {...errorProps(f.key)}
                  />
                  <span className="unit">{f.unit ?? input.unit}</span>
                </div>
                {errors[f.key] && (
                  <p id={`reading-${f.key}-error`} className="field-error">
                    {errors[f.key]}
                  </p>
                )}
              </div>
            ))}
          </div>
          {range && <p className="field-hint">Healthy range: {range}</p>}

          {vitalId === "bp" && (
            <>
              <fieldset className="book-field">
                <legend>Which session?</legend>
                <div className="book-chips">
                  {BP_SESSIONS.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      className="book-chip"
                      aria-pressed={session === s.id}
                      onClick={() => {
                        setSession(s.id);
                        setSessionTouched(true);
                      }}
                    >
                      {s.label}
                      {s.hint && <small>{s.hint}</small>}
                    </button>
                  ))}
                </div>
                <p className="field-hint reading-hint">
                  Take two readings each morning and evening, seated, after a minute of rest. The assessment needs
                  readings on 5 of the last 7 days.
                </p>
              </fieldset>
              <label className="check-row">
                <input type="checkbox" checked={irregular} onChange={(e) => setIrregular(e.target.checked)} />
                <span>The cuff showed an irregular heartbeat symbol</span>
              </label>
            </>
          )}

          <div className="form-field">
            <label htmlFor="reading-when">When</label>
            <input
              id="reading-when"
              className="text-input"
              type="datetime-local"
              value={when}
              max={maxWhen}
              onChange={(e) => {
                setWhen(e.target.value);
                if (!sessionTouched && e.target.value) setSession(bpSessionFor(new Date(e.target.value)));
              }}
              {...errorProps("when")}
            />
            {errors.when && (
              <p id="reading-when-error" className="field-error">
                {errors.when}
              </p>
            )}
          </div>

          <div className="form-field">
            <label htmlFor="reading-note">
              Note <span className="book-optional">Optional</span>
            </label>
            <input
              id="reading-note"
              className="text-input"
              type="text"
              maxLength={120}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. After my morning walk"
            />
          </div>

          {saveError && (
            <p className="file-error" role="alert">
              {saveError}
            </p>
          )}

          <div className="book-actions">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={saving}>
              {saving ? "Saving…" : editing ? "Save changes" : "Save reading"}
            </button>
          </div>
        </form>
      )}
    </dialog>
  );
}
