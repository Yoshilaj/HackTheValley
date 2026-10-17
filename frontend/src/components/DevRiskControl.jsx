import { useState } from "react";
import { AUTO_BOOK_THRESHOLD, useCareAlert } from "../context/CareAlertContext.jsx";

// Dev-only floating control to override the risk score (rendered only in
// `npm run dev`). The slider commits on release so dragging past the
// threshold doesn't fire the pop-up mid-drag.
const PRESETS = [14, 50, 88];

export default function DevRiskControl() {
  const { risk, devScore, setDevScore, resetAlert } = useCareAlert();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(null);
  const value = draft ?? risk.score;

  const commit = () => {
    if (draft == null) return;
    setDevScore(draft);
    setDraft(null);
  };

  return (
    <div className="dev-risk">
      {open && (
        <div className="dev-risk-panel" role="group" aria-label="Developer risk controls">
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
        </div>
      )}
      <button type="button" className="dev-risk-pill" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        Dev · Risk {risk.score}
      </button>
    </div>
  );
}
