import { useCallback, useEffect, useState } from "react";
import GuestGate from "../components/GuestGate.jsx";
import TappingTask from "../components/TappingTask.jsx";
import AppleHealthImport from "../components/AppleHealthImport.jsx";
import { useSession } from "../context/SessionContext.jsx";
import { USE_BACKEND, getTaskHistory } from "../api/client.js";
import { capitalize, relativeDay } from "../utils/dates.js";

// Short checks you can do yourself, plus your Apple Watch data. Everything here
// goes to the backend and is read by the next risk assessment. The voice
// tasks (sustained vowel, story recall) need a recording flow that isn't built
// into the website yet, so they are listed as coming soon.
const TASK_NAMES = {
  finger_tap: "Finger tapping",
  sustained_vowel: "Sustained vowel",
  story_recall: "Story recall",
  word_recall: "Word recall",
};

export default function AssessmentsPage() {
  const { isGuest, signedIn } = useSession();
  const live = USE_BACKEND && signedIn;
  const [history, setHistory] = useState([]);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!live) return;
    getTaskHistory(null, 20)
      .then((res) => setHistory(res?.items ?? []))
      .catch(() => {});
  }, [live, tick]);

  if (isGuest) {
    return (
      <main className="page">
        <header className="page-head">
          <div>
            <h1 tabIndex={-1}>Assessments</h1>
            <p className="subtitle">Short checks that help spot changes early.</p>
          </div>
        </header>
        <GuestGate title="Do the checks">Sign in to do the tapping task and import your Apple Watch data.</GuestGate>
      </main>
    );
  }

  if (!live) {
    return (
      <main className="page">
        <header className="page-head">
          <div>
            <h1 tabIndex={-1}>Assessments</h1>
            <p className="subtitle">Short checks that help spot changes early.</p>
          </div>
        </header>
        <section className="card">
          <p className="field-hint">These checks save to the backend. Run the app with VITE_USE_BACKEND=true to use them.</p>
        </section>
      </main>
    );
  }

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1 tabIndex={-1}>Assessments</h1>
          <p className="subtitle">Short checks that help spot changes early. Each one is read by your next assessment.</p>
        </div>
      </header>

      <div className="page-split assessments-split">
        <div className="stack">
          <TappingTask onSubmitted={refresh} />
        </div>
        <div className="stack">
          <AppleHealthImport />
          <section className="card" aria-labelledby="voice-title">
            <div className="card-head">
              <h2 id="voice-title">Voice checks</h2>
              <em className="badge watch">Coming soon</em>
            </div>
            <p className="field-hint">
              The sustained "ahh" and story-recall tasks need a microphone recording flow that isn't on the website
              yet. Until then they can be done through the CareSync app.
            </p>
          </section>
          <section className="card" aria-labelledby="task-history-title">
            <div className="card-head">
              <h2 id="task-history-title">Recent checks</h2>
            </div>
            {history.length ? (
              <ul className="readings">
                {history.map((t) => (
                  <li key={t.id}>
                    <div>
                      <b>{TASK_NAMES[t.task_type] ?? t.task_type}</b>
                      <small>{capitalize(relativeDay(new Date(t.created_at).getTime()))}</small>
                    </div>
                    <em className={`badge ${t.valid ? "normal" : "watch"}`}>{t.valid ? "Valid" : t.status === "in_progress" ? "In progress" : "Check"}</em>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="field-hint">No checks done yet.</p>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
