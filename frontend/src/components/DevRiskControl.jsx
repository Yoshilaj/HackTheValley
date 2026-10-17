import { useState } from "react";
import { AUTO_BOOK_THRESHOLD, useCareAlert } from "../context/CareAlertContext.jsx";
import { useSession } from "../context/SessionContext.jsx";
import useStoredState from "../hooks/useStoredState.js";

// Dev-only "Dev access" button (rendered only in `npm run dev`) with two views:
//   User view - the app exactly as a user sees it: no dev tools, and the button
//               dims to stay out of the way. Any risk override stays applied, so
//               you can set up a scenario in dev view and present it here.
//   Dev view  - the risk override (slider, presets, reset) and what the app is
//               running on.
// The slider commits on release so dragging past the threshold doesn't fire
// the pop-up mid-drag. The chosen view is remembered in this browser.
const PRESETS = [14, 50, 88];

export default function DevRiskControl() {
  const { risk, devScore, setDevScore, resetAlert } = useCareAlert();
  const { session } = useSession();
  const [open, setOpen] = useState(false);
  const [view, setView] = useStoredState("caresync-dev-view", "dev"); // "user" | "dev"
  const [draft, setDraft] = useState(null);
  const value = draft ?? risk.score;
  const who = session?.guest ? "Guest" : session?.email ?? (session ? `Signed in with ${session.method}` : "Signed out");

  const commit = () => {
    if (draft == null) return;
    setDevScore(draft);
    setDraft(null);
  };

  return (
    <div className={`dev-risk ${view === "user" ? "user-view" : ""}`}>
      {open && (
        <div className="dev-risk-panel" role="group" aria-label="Developer access">
          <div className="dev-view-toggle" role="group" aria-label="View">
            <button type="button" aria-pressed={view === "user"} onClick={() => setView("user")}>
              User view
            </button>
            <button type="button" aria-pressed={view === "dev"} onClick={() => setView("dev")}>
              Dev view
            </button>
          </div>

          {view === "user" ? (
            <p>
              You're seeing the app as a user would, with no dev tools.
              {devScore != null && ` The risk score is still set to ${devScore}.`}
            </p>
          ) : (
            <>
              <div className="dev-risk-head">
                <label htmlFor="dev-risk-range">Overall risk</label>
                <output htmlFor="dev-risk-range">{value}</output>
              </div>
              <input
                id="dev-risk-range"
                type="range"
                min="0"
                max="100"
                value={value}
                onChange={(e) => setDraft(Number(e.target.value))}
                onPointerUp={commit}
                onKeyUp={commit}
                onBlur={commit}
              />
              <div className="dev-risk-presets">
                {PRESETS.map((n) => (
                  <button
                    key={n}
                    type="button"
                    aria-pressed={value === n}
                    onClick={() => {
                      setDraft(null);
                      setDevScore(n);
                    }}
                  >
                    {n}
                  </button>
                ))}
              </div>
              <div className="dev-risk-foot">
                <button type="button" onClick={() => setDevScore(null)} disabled={devScore == null}>
                  Use real score
                </button>
                <button type="button" onClick={resetAlert}>
                  Reset alert
                </button>
              </div>
              <p>Auto-booking triggers at {AUTO_BOOK_THRESHOLD}+.</p>
              <dl className="dev-info">
                <div>
                  <dt>Session</dt>
                  <dd>{who}</dd>
                </div>
                <div>
                  <dt>Score</dt>
                  <dd>{devScore != null ? `Override ${devScore}` : "Real"}</dd>
                </div>
                <div>
                  <dt>Data</dt>
                  <dd>Placeholder (backend off)</dd>
                </div>
              </dl>
            </>
          )}
        </div>
      )}
      <button type="button" className="dev-risk-pill" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        Dev access
      </button>
    </div>
  );
}
