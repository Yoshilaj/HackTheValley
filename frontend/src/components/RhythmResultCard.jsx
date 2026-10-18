import { useEffect, useState } from "react";
import { getRhythmResults, logRhythmResult } from "../api/client.js";
import { capitalize, relativeDay, toLocalInput } from "../utils/dates.js";

// Heart-rhythm results from a watch or cuff, typed in by hand (an Apple Health
// export brings them in automatically). Sent to POST /api/metrics/rhythm with
// the device's own wording; the backend normalises it and the rhythm rules
// (RHY-01..03) read it.
const SOURCES = [
  { id: "apple_ecg", label: "Apple Watch ECG", labels: ["Sinus Rhythm", "Atrial Fibrillation", "Inconclusive", "Poor Recording", "High Heart Rate", "Low Heart Rate"] },
  { id: "apple_irn", label: "Apple Watch irregular rhythm notification", labels: ["Irregular rhythm notification"] },
  { id: "omron_connect", label: "Omron cuff (AFib indicator)", labels: ["Possible AFib"] },
  { id: "manual_irregular_pulse", label: "Felt an irregular pulse", labels: ["Irregular pulse"] },
];

export default function RhythmResultCard() {
  const [source, setSource] = useState(SOURCES[0].id);
  const [label, setLabel] = useState(SOURCES[0].labels[0]);
  const [when, setWhen] = useState(() => toLocalInput(new Date()));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [rows, setRows] = useState([]);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    getRhythmResults()
      .then((r) => setRows(r ?? []))
      .catch(() => {});
  }, [reload]);

  const pickSource = (id) => {
    setSource(id);
    setLabel(SOURCES.find((s) => s.id === id).labels[0]);
  };

  const save = async (e) => {
    e.preventDefault();
    const at = new Date(when);
    if (Number.isNaN(at.getTime())) {
      setError("Enter when the result was recorded.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await logRhythmResult({ source, label, recordedAt: at.toISOString() });
      setReload((n) => n + 1);
    } catch {
      setError("Couldn't save this result. Try again in a moment.");
    } finally {
      setSaving(false);
    }
  };

  const current = SOURCES.find((s) => s.id === source);
  return (
    <section className="card" aria-labelledby="rhythm-title">
      <div className="card-head">
        <h2 id="rhythm-title">Heart rhythm results</h2>
      </div>
      <form className="rhythm-form" onSubmit={save} noValidate>
        <div className="form-field">
          <label htmlFor="rhythm-source">Where from</label>
          <select id="rhythm-source" className="text-input" value={source} onChange={(e) => pickSource(e.target.value)}>
            {SOURCES.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field">
          <label htmlFor="rhythm-label">Result</label>
          <select id="rhythm-label" className="text-input" value={label} onChange={(e) => setLabel(e.target.value)}>
            {current.labels.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field">
          <label htmlFor="rhythm-when">When</label>
          <input id="rhythm-when" className="text-input" type="datetime-local" value={when} max={toLocalInput(new Date())} onChange={(e) => setWhen(e.target.value)} />
        </div>
        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}
        <div className="book-actions">
          <button type="submit" className="btn-secondary" disabled={saving}>
            {saving ? "Saving…" : "Add result"}
          </button>
        </div>
      </form>
      {rows.length > 0 && (
        <ul className="readings">
          {rows.slice(0, 5).map((r) => (
            <li key={r.id}>
              <div>
                <b>{r.raw_label ?? r.label}</b>
                <small>{capitalize(relativeDay(new Date(r.recorded_at).getTime()))} · {SOURCES.find((s) => s.id === r.source)?.label ?? r.source}</small>
              </div>
              <em className={`badge ${r.label === "sinus_rhythm" || r.label === "normal" ? "normal" : "watch"}`}>{r.label.replace(/_/g, " ")}</em>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
