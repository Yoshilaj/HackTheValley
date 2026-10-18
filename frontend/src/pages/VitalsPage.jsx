import { useState } from "react";
import ActivityChart from "../components/ActivityChart.jsx";
import LogReadingSheet from "../components/LogReadingSheet.jsx";
import VitalSheet from "../components/VitalSheet.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import GuestGate from "../components/GuestGate.jsx";
import RhythmResultCard from "../components/RhythmResultCard.jsx";
import { USE_BACKEND } from "../api/client.js";
import { useSession } from "../context/SessionContext.jsx";
import useVitals from "../hooks/useVitals.js";
import { VITAL_INPUTS } from "../utils/vitals.js";

const labels = { normal: "Normal", watch: "Watch" };

function Sparkline({ points, label }) {
  const w = 120;
  const h = 36;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const coords = points.map((p, i) => [(i / (points.length - 1)) * w, h - 4 - ((p - min) / span) * (h - 8)]);
  const last = coords[coords.length - 1];

  return (
    <svg className="sparkline" viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${label} over the last 7 readings`}>
      <polyline points={coords.map((c) => c.join(",")).join(" ")} />
      <circle cx={last[0]} cy={last[1]} r="3.5" />
    </svg>
  );
}

export default function VitalsPage() {
  const { vitals, readings, logReading, updateReading, deleteReading } = useVitals();
  const [detailId, setDetailId] = useState(null); // vital whose tile was opened
  const [form, setForm] = useState(null); // null, or LogReadingSheet's `initial` ({} for a blank form)
  const [toDelete, setToDelete] = useState(null); // reading row awaiting "Delete?"
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [justAdded, setJustAdded] = useState(null);
  const [announce, setAnnounce] = useState("");

  const { isGuest, signedIn } = useSession();
  const detail = vitals.find((v) => v.id === detailId) ?? null;

  const save = async (data) => {
    const entry = form?.id ? await updateReading(form.id, data) : await logReading(data);
    setJustAdded(entry.id);
    setAnnounce(`${VITAL_INPUTS[entry.vitalId].label} reading ${form?.id ? "updated" : "saved"}.`);
  };

  const confirmDelete = async () => {
    setDeleting(true);
    setDeleteError("");
    try {
      await deleteReading(toDelete.id);
      setAnnounce(`${toDelete.vital} reading deleted.`);
      setToDelete(null);
    } catch {
      setDeleteError("Couldn't delete this reading. Try again in a moment.");
    } finally {
      setDeleting(false);
    }
  };

  if (isGuest) {
    return (
      <main className="page">
        <header className="page-head">
          <div>
            <h1 tabIndex={-1}>Vitals</h1>
            <p className="subtitle">Your readings and how they move over time.</p>
          </div>
        </header>
        <GuestGate title="Track your vitals">
          Sign in to log blood pressure, heart rate and more, and see how your readings change.
        </GuestGate>
      </main>
    );
  }

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1 tabIndex={-1}>Vitals</h1>
          <p className="subtitle">Your latest readings and how they've moved this week.</p>
        </div>
        <button className="btn-primary" onClick={() => setForm({})} aria-haspopup="dialog">
          Log a reading
        </button>
      </header>

      <VitalSheet
        vital={detail}
        onClose={() => setDetailId(null)}
        onLog={(vitalId) => {
          setDetailId(null);
          setForm({ vitalId });
        }}
      />
      <LogReadingSheet open={form !== null} initial={form} onClose={() => setForm(null)} onSave={save} />
      <ConfirmDialog
        open={toDelete !== null}
        title="Delete this reading?"
        description={toDelete ? `${toDelete.vital} ${toDelete.value}, ${toDelete.date}. This can't be undone.` : ""}
        confirmLabel="Delete"
        cancelLabel="Keep"
        danger
        busy={deleting}
        error={deleteError}
        onConfirm={confirmDelete}
        onClose={() => {
          setToDelete(null);
          setDeleteError("");
        }}
      />
      <p className="sr-only" role="status">
        {announce}
      </p>

      <section className="vitals-grid" aria-label="Latest vitals">
        {vitals.map((v) => (
          <article className={`card vital-tile tappable ${v.status}`} key={v.id}>
            {/* Stretched button: the whole tile opens its details */}
            <button type="button" className="card-hit" onClick={() => setDetailId(v.id)} aria-haspopup="dialog">
              <span className="sr-only">{v.label} details</span>
            </button>
            <div className="tile-head">
              <h2>{v.label}</h2>
              <em className={`badge ${v.status}`}>{labels[v.status]}</em>
            </div>
            <p className="amt">
              {v.value}
              <sup>{v.unit}</sup>
            </p>
            <Sparkline points={v.trend} label={v.label} />
            <p className="range">Healthy range: {v.range}</p>
          </article>
        ))}
      </section>

      <div className="page-split">
        <div className="stack">
          <ActivityChart />
          {USE_BACKEND && signedIn && <RhythmResultCard />}
        </div>

        <section className="card" aria-labelledby="readings-title">
          <div className="card-head">
            <h2 id="readings-title">Recent readings</h2>
          </div>
          <ul className="readings">
            {readings.map((r) => (
              <li key={r.id} className={r.id === justAdded ? "just-added" : undefined}>
                <div>
                  <b>{r.vital}</b>
                  <small>
                    {r.date}
                    {r.note && ` · ${r.note}`}
                  </small>
                </div>
                <span className="reading-value">{r.value}</span>
                <em className={`badge ${r.status}`}>{labels[r.status]}</em>
                {/* Only readings the user logged can be changed */}
                {r.entry && (
                  <span className="reading-actions">
                    <button
                      type="button"
                      className="row-action"
                      onClick={() => setForm(r.entry)}
                      aria-label={`Edit ${r.vital} reading from ${r.date}`}
                      aria-haspopup="dialog"
                    >
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M12 20h8M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z" />
                      </svg>
                    </button>
                    <button
                      type="button"
                      className="row-action danger"
                      onClick={() => setToDelete(r)}
                      aria-label={`Delete ${r.vital} reading from ${r.date}`}
                      aria-haspopup="dialog"
                    >
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" />
                      </svg>
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      </div>
    </main>
  );
}
