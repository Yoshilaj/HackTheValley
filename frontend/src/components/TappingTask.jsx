import { useCallback, useEffect, useRef, useState } from "react";
import { submitTapping } from "../api/client.js";

// Finger-tapping task (spec §5.3, BRAIN keyboard protocol): with one index
// finger, press `s` and `;` alternately as fast as you can for 30 seconds,
// left hand then right hand. Every key press is timed with performance.now()
// (down and up), held-key repeats are ignored, and the two runs go to
// POST /api/tasks/tapping. The reply gives the task's features (taps, rhythm)
// and whether the run was valid; the assessment (PD-04) tracks them over time.

const DURATION_S = 30;
const KEYS = ["s", ";"];
const HANDS = [
  { id: "left", label: "Left hand" },
  { id: "right", label: "Right hand" },
];

const FEATURE_LABELS = {
  tap_count: ["Taps", (v) => Math.round(v)],
  mean_iti_ms: ["Average gap between taps", (v) => `${Math.round(v)} ms`],
  iti_cv: ["Rhythm variability", (v) => `${(v * 100).toFixed(0)} %`],
  dwell_mean_ms: ["Average key hold", (v) => `${Math.round(v)} ms`],
  decrement_slope_ms_per_tap: ["Slowing over the run", (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)} ms/tap`],
  alternation_error_rate: ["Wrong-key presses", (v) => `${(v * 100).toFixed(0)} %`],
  asymmetry: ["Left/right asymmetry", (v) => `${(v * 100).toFixed(0)} %`],
};

function featureRows(features) {
  if (!features || typeof features !== "object") return [];
  const rows = [];
  const push = (prefix, obj) => {
    for (const [k, v] of Object.entries(obj)) {
      if (typeof v === "number" && FEATURE_LABELS[k]) {
        const [label, fmt] = FEATURE_LABELS[k];
        rows.push([prefix ? `${prefix} ${label.toLowerCase()}` : label, fmt(v)]);
      }
    }
  };
  push("", features);
  for (const hand of ["left", "right"]) if (features[hand] && typeof features[hand] === "object") push(hand === "left" ? "Left" : "Right", features[hand]);
  return rows;
}

export default function TappingTask({ onSubmitted, embedded = false }) {
  const [step, setStep] = useState("intro"); // intro | left | right | submitting | done
  const [running, setRunning] = useState(false);
  const [remaining, setRemaining] = useState(DURATION_S);
  const [counts, setCounts] = useState({ left: 0, right: 0 });
  const [lastKey, setLastKey] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const events = useRef({ left: [], right: [] });
  const open = useRef(new Map()); // key -> down_ms while held
  const t0 = useRef(0);
  const padRef = useRef(null);
  const hand = step === "left" || step === "right" ? step : null;

  const finishHand = useCallback(() => {
    setRunning(false);
    // Keys still held at the end are closed at the deadline.
    const end = performance.now() - t0.current;
    for (const [key, down] of open.current) events.current[step].push({ key, down_ms: down, up_ms: end });
    open.current.clear();
    setStep((s) => (s === "left" ? "right" : "submitting"));
  }, [step]);

  // Countdown while a hand is running.
  useEffect(() => {
    if (!running) return undefined;
    const tick = setInterval(() => {
      const left = DURATION_S - (performance.now() - t0.current) / 1000;
      if (left <= 0) {
        clearInterval(tick);
        finishHand();
      } else setRemaining(Math.ceil(left));
    }, 100);
    return () => clearInterval(tick);
  }, [running, finishHand]);

  // Submit both hands once the right hand finishes.
  useEffect(() => {
    if (step !== "submitting") return;
    setError("");
    submitTapping({ left: events.current.left, right: events.current.right, durationS: DURATION_S, keys: KEYS })
      .then((res) => {
        setResult(res);
        setStep("done");
        onSubmitted?.(res);
      })
      .catch((err) => {
        setError(err?.detail ? String(err.detail) : "Couldn't send the task. Try again in a moment.");
        setStep("intro");
      });
  }, [step, onSubmitted]);

  const start = (which) => {
    events.current[which] = [];
    open.current.clear();
    setCounts((c) => ({ ...c, [which]: 0 }));
    setRemaining(DURATION_S);
    setLastKey(null);
    setStep(which);
    setRunning(false);
    requestAnimationFrame(() => padRef.current?.focus());
  };

  const onKeyDown = (e) => {
    if (!hand || e.repeat) return; // held-key auto-repeat is not a tap
    const key = e.key.toLowerCase();
    if (!KEYS.includes(key)) return;
    e.preventDefault();
    if (!running) {
      // The first press starts the clock (BRAIN: timing starts on the first tap).
      t0.current = performance.now();
      setRunning(true);
    }
    if (open.current.has(key)) return;
    open.current.set(key, performance.now() - t0.current);
    setLastKey(key);
  };

  const onKeyUp = (e) => {
    if (!hand || !running) return;
    const key = e.key.toLowerCase();
    const down = open.current.get(key);
    if (down == null) return;
    open.current.delete(key);
    events.current[hand].push({ key, down_ms: down, up_ms: performance.now() - t0.current });
    setCounts((c) => ({ ...c, [hand]: c[hand] + 1 }));
  };

  const reset = () => {
    events.current = { left: [], right: [] };
    open.current.clear();
    setCounts({ left: 0, right: 0 });
    setResult(null);
    setError("");
    setStep("intro");
  };

  const badge = result && <em className={`badge ${result.valid ? "normal" : "watch"}`}>{result.valid ? "Saved" : "Check"}</em>;
  const body = (
    <>

      {step === "intro" && (
        <div className="tapping-intro">
          <p>
            One index finger, <b>alternating</b> between <kbd>S</kbd> and <kbd>;</kbd> as fast and evenly as you can for 30 seconds.
            Left hand first, then right. The timer starts on your first press.
          </p>
          <p className="key-sequence" aria-label="Press S, then semicolon, then S, then semicolon, and so on">
            <kbd>S</kbd>
            <span aria-hidden="true">→</span>
            <kbd>;</kbd>
            <span aria-hidden="true">→</span>
            <kbd>S</kbd>
            <span aria-hidden="true">→</span>
            <kbd>;</kbd>
            <span aria-hidden="true">→</span>
            <small>…</small>
          </p>
          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}
          <div className="book-actions">
            <button type="button" className="btn-primary" onClick={() => start("left")}>
              Start with the left hand
            </button>
          </div>
        </div>
      )}

      {hand && (
        <div className="tapping-run">
          <p className="tapping-hand">{HANDS.find((h) => h.id === hand).label}</p>
          <div
            ref={padRef}
            className={`tapping-pad ${running ? "live" : ""}`}
            tabIndex={0}
            role="application"
            aria-label={`Tapping area, ${hand} hand. Press S and semicolon alternately.`}
            onKeyDown={onKeyDown}
            onKeyUp={onKeyUp}
          >
            {/* The key to press next is outlined, so the alternation is easy to follow */}
            <div className="tapping-keys" aria-hidden="true">
              {KEYS.map((k) => {
                const next = (lastKey ?? ";") !== k;
                return (
                  <span key={k} className={`tapping-key ${lastKey === k ? "hit" : ""} ${next ? "next" : ""}`}>
                    {k.toUpperCase()}
                    {next && <small>next</small>}
                  </span>
                );
              })}
            </div>
            <p className="tapping-count">
              <b>{counts[hand]}</b> taps
            </p>
            <p className="tapping-timer" aria-live="polite">
              {running ? `${remaining} s left · now press ${(lastKey ?? ";") === "s" ? ";" : "S"}` : "Click here, then press S to begin"}
            </p>
          </div>
          <div className="book-actions">
            <button type="button" className="btn-ghost" onClick={reset}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {step === "submitting" && <p className="field-hint">Sending your taps…</p>}

      {step === "done" && result && (
        <div className="tapping-result" role="status">
          <p className={result.valid ? "" : "checkin-urgent"}>{result.message}</p>
          {!result.valid && result.reason && <p className="field-hint">{result.reason}</p>}
          {featureRows(result.features).length > 0 && (
            <dl className="sheet-rows">
              {featureRows(result.features).map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          )}
          <div className="book-actions">
            <button type="button" className="btn-secondary" onClick={reset}>
              Do it again
            </button>
          </div>
        </div>
      )}
    </>
  );

  if (embedded) {
    return (
      <div className="task-body">
        {badge && <div className="task-badge-row">{badge}</div>}
        {body}
      </div>
    );
  }
  return (
    <section className="card" aria-labelledby="tapping-title">
      <div className="card-head">
        <h2 id="tapping-title">Finger tapping</h2>
        {badge}
      </div>
      {body}
    </section>
  );
}
