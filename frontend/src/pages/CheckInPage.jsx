import { useEffect, useState } from "react";
import GuestGate from "../components/GuestGate.jsx";
import { useSession } from "../context/SessionContext.jsx";
import { USE_BACKEND, getDailyLogs, logMetric, saveDailyLog } from "../api/client.js";

// Daily check-in: how you slept, how tired you are, and anything you noticed.
// Saved to the backend's daily log (POST /api/logs/daily), where fatigue feeds
// the general-wellbeing rule (GEN-01), the ticked changes become observations
// the cognitive rule (COG-03) reads. Hours slept are stored as a sleep metric
// (the same series an Apple Watch export fills), the other half of GEN-01.
//
// The flag ids are the backend's own vocabulary (ingest_service.FLAG_OBSERVATIONS).

const SCALES = [
  { key: "sleep_quality", label: "How well did you sleep last night?", low: "Very poorly", high: "Very well" },
  { key: "fatigue", label: "How tired do you feel today?", low: "Not at all", high: "Extremely" },
  { key: "mood", label: "How is your mood today?", low: "Very low", high: "Very good" },
  { key: "stress", label: "How stressed do you feel?", low: "Not at all", high: "Extremely" },
];

const FLAG_GROUPS = [
  {
    id: "functional",
    title: "Memory and daily life",
    hint: "Things family or friends might have noticed in the last few weeks.",
    flags: [
      ["getting_lost", "Got lost somewhere familiar"],
      ["repeating_questions", "Repeated the same questions"],
      ["missed_appointments", "Missed appointments or plans"],
      ["trouble_managing_money", "Trouble managing money or bills"],
      ["trouble_managing_medication", "Trouble keeping up with medication"],
      ["memory_lapse", "Memory lapse (forgot a name or where something was)"],
      ["confusion", "Felt confused about the day or place"],
    ],
  },
  {
    id: "symptoms",
    title: "Symptoms today",
    hint: "Anything that felt different from usual.",
    flags: [
      ["palpitations", "Palpitations or a racing heart"],
      ["dizziness", "Dizziness or light-headedness"],
      ["breathlessness", "More breathless than usual"],
      ["tremor", "Shaking or tremor"],
      ["stiffness", "Stiffness or slowness"],
      ["headache", "Headache"],
    ],
  },
];

const todayIso = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local

function ScaleField({ field, value, onChange }) {
  return (
    <fieldset className="book-field checkin-scale">
      <legend>{field.label}</legend>
      <div className="book-chips">
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            type="button"
            className="book-chip"
            aria-pressed={value === n}
            aria-label={`${n} of 5`}
            onClick={() => onChange(value === n ? null : n)}
          >
            {n}
          </button>
        ))}
      </div>
      <p className="field-hint reading-hint">
        1 = {field.low} · 5 = {field.high}
      </p>
    </fieldset>
  );
}

export default function CheckInPage() {
  const { isGuest, signedIn } = useSession();
  const live = USE_BACKEND && signedIn;
  const [date, setDate] = useState(todayIso);
  const [scales, setScales] = useState({});
  const [hoursSlept, setHoursSlept] = useState("");
  const [flags, setFlags] = useState({});
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null); // backend reply after a save
  const [recent, setRecent] = useState([]);

  const loadRecent = () => {
    if (!live) return;
    getDailyLogs(14)
      .then((rows) => setRecent(rows ?? []))
      .catch(() => {});
  };
  useEffect(loadRecent, [live]);

  // Editing a day that already has a log: start from what was saved.
  useEffect(() => {
    const existing = recent.find((r) => r.date === date);
    if (!existing) return;
    setScales({
      sleep_quality: existing.sleep_quality ?? null,
      fatigue: existing.fatigue ?? null,
      mood: existing.mood ?? null,
      stress: existing.stress ?? null,
    });
    setFlags(existing.flags ?? {});
    setNotes(existing.notes ?? "");
  }, [date, recent]);

  const toggle = (id) => setFlags((f) => ({ ...f, [id]: !f[id] }));

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    const hours = hoursSlept.trim() === "" ? null : Number(hoursSlept.replace(",", "."));
    if (hours != null && (!Number.isFinite(hours) || hours < 0 || hours > 24)) {
      setError("Hours slept should be between 0 and 24.");
      return;
    }
    if (!live) {
      setError("Sign in with the backend on to save your check-in.");
      return;
    }
    setSaving(true);
    try {
      const body = {
        date,
        sleep_quality: scales.sleep_quality ?? null,
        fatigue: scales.fatigue ?? null,
        mood: scales.mood ?? null,
        stress: scales.stress ?? null,
        flags: Object.fromEntries(Object.entries(flags).filter(([, v]) => v)),
        notes: notes.trim() || null,
      };
      const saved = await saveDailyLog(body);
      // Sleep length is a metric sample dated at the wake-up day (00:00 local), like an Apple Health night.
      if (hours != null) await logMetric("sleep_asleep_minutes", Math.round(hours * 60), `${date}T00:00:00`);
      setResult(saved);
      loadRecent();
    } catch {
      setError("Couldn't save your check-in. Try again in a moment.");
    } finally {
      setSaving(false);
    }
  };

  if (isGuest) {
    return (
      <main className="page">
        <header className="page-head">
          <div>
            <h1 tabIndex={-1}>Daily check-in</h1>
            <p className="subtitle">A minute a day so CareSync can spot changes early.</p>
          </div>
        </header>
        <GuestGate title="Check in every day">Sign in to log how you slept, how you feel and anything you noticed.</GuestGate>
      </main>
    );
  }

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1 tabIndex={-1}>Daily check-in</h1>
          <p className="subtitle">A minute a day so CareSync can spot changes early.</p>
        </div>
      </header>

      <div className="page-split checkin-split">
        <form className="card checkin-form" onSubmit={submit} noValidate aria-labelledby="checkin-title">
          <div className="card-head">
            <h2 id="checkin-title">How are you today?</h2>
            <div className="form-field checkin-date">
              <label htmlFor="checkin-date" className="sr-only">
                Day
              </label>
              <input
                id="checkin-date"
                className="text-input"
                type="date"
                max={todayIso()}
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </div>
          </div>

          {SCALES.map((f) => (
            <ScaleField key={f.key} field={f} value={scales[f.key] ?? null} onChange={(v) => setScales((s) => ({ ...s, [f.key]: v }))} />
          ))}

          <div className="form-field">
            <label htmlFor="checkin-hours">
              Hours slept <span className="book-optional">Optional</span>
            </label>
            <div className="input-unit checkin-hours">
              <input
                id="checkin-hours"
                type="text"
                inputMode="decimal"
                value={hoursSlept}
                onChange={(e) => setHoursSlept(e.target.value)}
                placeholder="e.g. 7.5"
              />
              <span className="unit">h</span>
            </div>
            <p className="field-hint reading-hint">Skip this if your Apple Watch tracks your sleep.</p>
          </div>

          {FLAG_GROUPS.map((g) => (
            <fieldset key={g.id} className={`book-field checkin-flags ${g.id}`}>
              <legend>{g.title}</legend>
              <p className="field-hint reading-hint">{g.hint}</p>
              <ul className="check-list">
                {g.flags.map(([id, label]) => (
                  <li key={id}>
                    <label className="check-row">
                      <input type="checkbox" checked={Boolean(flags[id])} onChange={() => toggle(id)} />
                      <span>{label}</span>
                    </label>
                  </li>
                ))}
              </ul>
            </fieldset>
          ))}

          <div className="form-field">
            <label htmlFor="checkin-notes">
              Anything else? <span className="book-optional">Optional</span>
            </label>
            <textarea
              id="checkin-notes"
              className="text-input checkin-notes"
              rows={3}
              maxLength={5000}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. Forgot where I parked. Felt dizzy standing up."
            />
          </div>

          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}
          <div className="book-actions">
            <button type="submit" className="btn-primary" disabled={saving}>
              {saving ? "Saving…" : "Save check-in"}
            </button>
          </div>
        </form>

        <div className="stack">
          {result && (
            <section className="card" aria-live="polite">
              <div className="card-head">
                <h2>Saved for {result.date}</h2>
                <em className="badge normal">Saved</em>
              </div>
              {result.observations?.length ? (
                <>
                  <p className="field-hint">Noted for your next assessment:</p>
                  <ul className="tag-list">
                    {result.observations.map((o) => (
                      <li key={o.id} className="tag">
                        <span>{o.label.replace(/_/g, " ")}</span>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <p className="field-hint">Your scores are in. Nothing flagged today.</p>
              )}
            </section>
          )}

          <section className="card" aria-labelledby="checkin-recent">
            <div className="card-head">
              <h2 id="checkin-recent">Last two weeks</h2>
            </div>
            {recent.length ? (
              <ul className="readings">
                {recent.map((r) => (
                  <li key={r.id}>
                    <div>
                      <b>{r.date}</b>
                      <small>
                        {[
                          r.sleep_quality != null && `sleep ${r.sleep_quality}/5`,
                          r.fatigue != null && `fatigue ${r.fatigue}/5`,
                          r.mood != null && `mood ${r.mood}/5`,
                        ]
                          .filter(Boolean)
                          .join(" · ") || "no scores"}
                      </small>
                    </div>
                    <span className="reading-value">
                      {Object.entries(r.flags ?? {}).filter(([, v]) => v).length || "–"}
                      <small className="checkin-flagcount"> flags</small>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="field-hint">{live ? "No check-ins yet." : "Check-ins are saved once you sign in with the backend on."}</p>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
