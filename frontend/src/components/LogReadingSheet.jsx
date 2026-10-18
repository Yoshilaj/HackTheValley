import { useEffect, useRef, useState } from "react";
import { BP_SESSIONS, VITAL_INPUTS, VITAL_ORDER, bpSessionFor } from "../utils/vitals.js";
import { toLocalInput } from "../utils/dates.js";

// Glass sheet for logging a reading. Built around the home blood pressure
// protocol the assessment follows (spec §2): a morning and an evening session,
// seated after a minute of rest, two readings a minute apart. So for blood
// pressure the sheet asks for the session, then one or two readings, with the
// time defaulting to now. Other vitals take a single value.
// initial: { vitalId, lock? } - lock hides the "what are you logging" choice
// (the Daily tasks page opens it for blood pressure only).
// onSave(data) may throw. data is
//   { vitalId: "bp", readings: [{ sys, dia, pulse? }], at, session, irregularPulse, note }
//   or { vitalId, values: { value }, at, note }.
const BP_FIELDS = [
  { key: "sys", label: "Top", unit: "mmHg", min: 60, max: 260 },
  { key: "dia", label: "Bottom", unit: "mmHg", min: 30, max: 160 },
  { key: "pulse", label: "Pulse", unit: "bpm", min: 25, max: 250, optional: true },
];

const SESSION_HINT = { morning: "Within 1 h of waking, before breakfast", evening: "Before bed", other: "Any other time" };

const whenText = (d) => d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export default function LogReadingSheet({ open, initial, onClose, onSave }) {
  const ref = useRef(null);
  const [vitalId, setVitalId] = useState("bp");
  const [raw, setRaw] = useState({}); // non-BP value
  const [bp, setBp] = useState([{}, {}]); // two readings, each { sys, dia, pulse } as typed
  const [session, setSession] = useState("other");
  const [sessionTouched, setSessionTouched] = useState(false);
  const [irregular, setIrregular] = useState(false);
  const [when, setWhen] = useState("");
  const [maxWhen, setMaxWhen] = useState("");
  const [editingWhen, setEditingWhen] = useState(false);
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const locked = Boolean(initial?.lock);

  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog.open) {
      const now = new Date();
      setVitalId(initial?.vitalId ?? "bp");
      setRaw({});
      setBp([{}, {}]);
      setWhen(toLocalInput(now));
      setMaxWhen(toLocalInput(now));
      setEditingWhen(false);
      setNote("");
      setSession(bpSessionFor(now));
      setSessionTouched(false);
      setIrregular(false);
      setErrors({});
      setSaveError("");
      dialog.showModal();
      dialog.querySelector(".reading-fields input")?.focus();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const isBp = vitalId === "bp";
  const input = VITAL_INPUTS[vitalId];

  const pickVital = (id) => {
    setVitalId(id);
    setRaw({});
    setErrors({});
  };

  const num = (text) => {
    const t = String(text ?? "").trim().replace(",", ".");
    return { t, n: Number(t) };
  };

  const validateBp = () => {
    const errs = {};
    const readings = [];
    bp.forEach((r, i) => {
      const typed = BP_FIELDS.some((f) => String(r[f.key] ?? "").trim());
      if (i === 1 && !typed) return; // the second reading is optional
      const values = {};
      for (const f of BP_FIELDS) {
        const { t, n } = num(r[f.key]);
        const key = `${i}-${f.key}`;
        if (!t && f.optional) continue;
        if (!t) errs[key] = "Enter a value.";
        else if (!Number.isFinite(n)) errs[key] = "Enter a number.";
        else if (n < f.min || n > f.max) errs[key] = `${f.min}–${f.max}.`;
        else values[f.key] = Math.round(n);
      }
      if (values.sys != null && values.dia != null && values.dia >= values.sys) errs[`${i}-dia`] = "Bottom should be lower than top.";
      readings.push(values);
    });
    return { errs, readings };
  };

  const validateOther = () => {
    const errs = {};
    const values = {};
    for (const f of input.fields) {
      const { t, n } = num(raw[f.key]);
      if (!t && f.optional) continue;
      if (!t) errs[f.key] = "Enter a value.";
      else if (!Number.isFinite(n)) errs[f.key] = "Enter a number.";
      else if (n < f.min || n > f.max) errs[f.key] = `Enter a value between ${f.min} and ${f.max}.`;
      else values[f.key] = f.decimals ? Math.round(n * 10 ** f.decimals) / 10 ** f.decimals : Math.round(n);
    }
    return { errs, values };
  };

  const save = async (e) => {
    e.preventDefault();
    const at = new Date(when).getTime();
    const { errs, readings, values } = isBp ? validateBp() : validateOther();
    if (!when || Number.isNaN(at)) errs.when = "Enter when you took this reading.";
    else if (at > Date.now() + 60 * 1000) errs.when = "That time hasn't happened yet.";
    setErrors(errs);
    const firstBad = Object.keys(errs)[0];
    if (firstBad) {
      if (firstBad === "when") setEditingWhen(true);
      document.getElementById(`reading-${firstBad}`)?.focus();
      return;
    }
    setSaving(true);
    setSaveError("");
    try {
      await onSave(
        isBp
          ? { vitalId, readings, at, session, irregularPulse: irregular, note: note.trim() || undefined }
          : { vitalId, values, at, note: note.trim() || undefined }
      );
      onClose();
    } catch {
      setSaveError("Couldn't save this reading. Try again in a moment.");
    } finally {
      setSaving(false);
    }
  };

  const errorProps = (key) => (errors[key] ? { "aria-invalid": true, "aria-describedby": `reading-${key}-error` } : {});
  const typedSecond = BP_FIELDS.some((f) => String(bp[1][f.key] ?? "").trim());
  const count = isBp ? (typedSecond ? 2 : 1) : 1;

  return (
    <dialog
      ref={ref}
      className="sheet book-sheet reading-sheet"
      aria-labelledby="reading-title"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      {open && (
        <form className="sheet-body" onSubmit={save} noValidate>
          <header className="sheet-head">
            <div>
              <h2 id="reading-title" className="book-title">
                {isBp ? "Log blood pressure" : `Log ${input.label.toLowerCase()}`}
              </h2>
              <p className="book-sub">
                {isBp
                  ? "Seated, after a minute of rest. Take two readings a minute apart and enter both."
                  : "Add a reading from your own device."}
              </p>
            </div>
            <button type="button" className="sheet-close" aria-label="Close" onClick={onClose}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </header>

          {!locked && (
            <fieldset className="book-field">
              <legend>What are you logging?</legend>
              <div className="book-chips">
                {VITAL_ORDER.map((id) => (
                  <button key={id} type="button" className="book-chip" aria-pressed={vitalId === id} onClick={() => pickVital(id)}>
                    {VITAL_INPUTS[id].label}
                  </button>
                ))}
              </div>
            </fieldset>
          )}

          {isBp ? (
            <>
              <fieldset className="book-field">
                <legend>Session</legend>
                <div className="session-chips">
                  {BP_SESSIONS.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      className="book-chip session-chip"
                      aria-pressed={session === s.id}
                      onClick={() => {
                        setSession(s.id);
                        setSessionTouched(true);
                      }}
                    >
                      {s.label}
                      <small>{SESSION_HINT[s.id]}</small>
                    </button>
                  ))}
                </div>
                {!sessionTouched && <p className="field-hint reading-hint">Picked from the clock. Change it if this was a different session.</p>}
              </fieldset>

              {[0, 1].map((i) => (
                <fieldset className="book-field bp-reading" key={i}>
                  <legend>
                    Reading {i + 1}
                    {i === 1 && <span className="book-optional">Optional</span>}
                  </legend>
                  <div className="reading-fields three">
                    {BP_FIELDS.map((f) => {
                      const key = `${i}-${f.key}`;
                      return (
                        <div className="form-field" key={key}>
                          <label htmlFor={`reading-${key}`}>
                            {f.label}
                            {f.optional && <span className="book-optional">Optional</span>}
                          </label>
                          <div className={`input-unit ${errors[key] ? "invalid" : ""}`}>
                            <input
                              id={`reading-${key}`}
                              type="text"
                              inputMode="numeric"
                              autoComplete="off"
                              placeholder={f.key === "sys" ? "120" : f.key === "dia" ? "80" : "70"}
                              value={bp[i][f.key] ?? ""}
                              onChange={(e) => setBp((rs) => rs.map((r, j) => (j === i ? { ...r, [f.key]: e.target.value } : r)))}
                              {...errorProps(key)}
                            />
                            <span className="unit">{f.unit}</span>
                          </div>
                          {errors[key] && (
                            <p id={`reading-${key}-error`} className="field-error">
                              {errors[key]}
                            </p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </fieldset>
              ))}

              <label className="check-row">
                <input type="checkbox" checked={irregular} onChange={(e) => setIrregular(e.target.checked)} />
                <span>My cuff showed the irregular heartbeat symbol</span>
              </label>
            </>
          ) : (
            <>
              <div className="reading-fields">
                {input.fields.map((f) => (
                  <div className="form-field" key={`${vitalId}-${f.key}`}>
                    <label htmlFor={`reading-${f.key}`}>{f.label}</label>
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
              {input.range && <p className="field-hint reading-hint">Healthy range: {input.range}</p>}
            </>
          )}

          <div className="form-field">
            <div className="when-row">
              <label htmlFor="reading-when">Taken</label>
              {!editingWhen ? (
                <>
                  <span className="when-value">{when ? whenText(new Date(when)) : "Now"}</span>
                  <button type="button" className="link-btn" onClick={() => setEditingWhen(true)}>
                    Change
                  </button>
                </>
              ) : (
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
              )}
            </div>
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
              placeholder={isBp ? "e.g. Felt dizzy standing up" : "e.g. After my morning walk"}
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
              {saving ? "Saving…" : isBp ? `Save ${count} reading${count === 1 ? "" : "s"}` : "Save reading"}
            </button>
          </div>
        </form>
      )}
    </dialog>
  );
}
