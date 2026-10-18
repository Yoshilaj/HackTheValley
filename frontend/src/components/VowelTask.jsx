import { useEffect, useState } from "react";
import { submitVowel } from "../api/client.js";
import useRecorder from "../hooks/useRecorder.js";
import LevelMeter from "./LevelMeter.jsx";

// Sustained vowel task (spec §5.4): say "ahh" at a steady, comfortable pitch
// and loudness for about five seconds, three times, phone or laptop 10-15 cm
// from the mouth in a quiet room. Each repetition is recorded here and sent to
// POST /api/tasks/vowel, which checks the recording (enough voiced sound, low
// noise, no clipping), extracts the voice features the assessment tracks over
// time (PD-03B), and returns an experimental model score that is shown as
// detail only: it is not calibrated for this microphone and never drives the
// assessment on its own.
const REPETITIONS = 3;
const TARGET_S = 6; // the backend needs 3 s of voiced sound; aim for ~5
const MAX_S = 8;
const COUNTDOWN_S = 3;

const QUALITY_ROWS = [
  ["duration_s", "Length", (v) => `${v.toFixed(1)} s`],
  ["voiced_s", "Voiced sound", (v) => `${v.toFixed(1)} s`],
  ["snr_db", "Signal to noise", (v) => `${Math.round(v)} dB`],
  ["clip_frac", "Clipping", (v) => `${(v * 100).toFixed(1)} %`],
];

function QualityRows({ quality }) {
  const rows = QUALITY_ROWS.filter(([k]) => typeof quality?.[k] === "number");
  if (!rows.length) return null;
  return (
    <dl className="sheet-rows">
      {rows.map(([k, label, fmt]) => (
        <div key={k}>
          <dt>{label}</dt>
          <dd>{fmt(quality[k])}</dd>
        </div>
      ))}
    </dl>
  );
}

function ExperimentalScore({ score }) {
  if (!score || typeof score.probability !== "number") return null;
  return (
    <details className="task-details">
      <summary>Experimental voice-model score (detail only)</summary>
      <dl className="sheet-rows">
        <div>
          <dt>Model probability</dt>
          <dd>{Math.round(score.probability * 100)} %</dd>
        </div>
        {typeof score.threshold === "number" && (
          <div>
            <dt>Model threshold</dt>
            <dd>{Math.round(score.threshold * 100)} %</dd>
          </div>
        )}
        {typeof score.above_threshold === "boolean" && (
          <div>
            <dt>Above threshold</dt>
            <dd>{score.above_threshold ? "Yes" : "No"}</dd>
          </div>
        )}
      </dl>
      <p className="field-hint">
        {score.calibration_note ||
          "A research model trained on clinic recordings. It is not calibrated for phone or laptop microphones and does not change your assessment; your own voice stability over time does."}
      </p>
    </details>
  );
}

export default function VowelTask({ onSubmitted, embedded = false }) {
  const rec = useRecorder();
  const [step, setStep] = useState("intro"); // intro | ready | countdown | recording | submitting | result | done
  const [rep, setRep] = useState(1);
  const [count, setCount] = useState(COUNTDOWN_S);
  const [results, setResults] = useState([]); // one per completed repetition
  const [current, setCurrent] = useState(null); // the last reply
  const [error, setError] = useState("");

  // 3-2-1, then record.
  useEffect(() => {
    if (step !== "countdown") return undefined;
    if (count === 0) {
      setStep("recording");
      rec.start({ maxMs: MAX_S * 1000 });
      return undefined;
    }
    const t = setTimeout(() => setCount((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [step, count]);

  // The recorder stopped (the user pressed Stop, or the time ran out): send it.
  useEffect(() => {
    if (step !== "recording" || rec.state !== "stopped" || !rec.take) return;
    const take = rec.take;
    setStep("submitting");
    setError("");
    submitVowel(take.blob, rep, take.ext)
      .then((res) => {
        setCurrent(res);
        setResults((rs) => [...rs.filter((r) => r.repetition_index !== rep), res]);
        setStep("result");
        if (res.valid) onSubmitted?.(res);
      })
      .catch((err) => {
        setError(
          err?.status === 413
            ? "That recording was too large to send."
            : err?.detail
              ? String(err.detail)
              : "Couldn't send the recording. Check your connection and try again."
        );
        setStep("ready");
      });
  }, [step, rec.state, rec.take, rep, onSubmitted]);

  // Microphone refused or missing: back to the start with the reason.
  useEffect(() => {
    if ((rec.state === "denied" || rec.state === "error" || rec.state === "unsupported") && step !== "intro") {
      setError(rec.error || "This browser can't record audio.");
      setStep("ready");
    }
  }, [rec.state, rec.error, step]);

  const begin = (n) => {
    setRep(n);
    setCurrent(null);
    setError("");
    setCount(COUNTDOWN_S);
    rec.reset();
    setStep("countdown");
  };

  const stopNow = () => rec.stop();

  const reset = () => {
    rec.reset();
    setResults([]);
    setCurrent(null);
    setError("");
    setRep(1);
    setStep("intro");
  };

  const validCount = results.filter((r) => r.valid).length;
  const recording = step === "recording";
  const longEnough = rec.elapsedS >= 3.5;

  const badge = results.length > 0 && (
    <em className={`badge ${validCount === results.length ? "normal" : "watch"}`}>
      {validCount} of {results.length} usable
    </em>
  );
  const body = (
    <>

      {step === "intro" && (
        <div className="tapping-intro">
          <p>
            Say a steady <b>"ahh"</b> for about five seconds, three times. Quiet room, microphone 10-15 cm away.
          </p>
          {rec.state === "unsupported" && (
            <p className="field-error" role="alert">
              This browser can't record audio. Try Chrome, Edge, Firefox or Safari.
            </p>
          )}
          <div className="book-actions">
            <button type="button" className="btn-primary" onClick={() => begin(1)} disabled={rec.state === "unsupported"}>
              Start repetition 1 of {REPETITIONS}
            </button>
          </div>
        </div>
      )}

      {(step === "ready" || step === "countdown" || recording || step === "submitting") && (
        <div className="tapping-run">
          <p className="tapping-hand">Repetition {rep} of {REPETITIONS}</p>
          <div className={`tapping-pad vowel-pad ${recording ? "live" : ""}`} aria-live="polite">
            {step === "countdown" && (
              <>
                <p className="countdown">{count}</p>
                <p className="tapping-timer">Breathe in… start your "ahh" when the count ends.</p>
              </>
            )}
            {recording && (
              <>
                <p className="countdown recording-dot">
                  <span aria-hidden="true" /> {Math.max(0, Math.ceil(TARGET_S - rec.elapsedS))}
                </p>
                <LevelMeter level={rec.level} />
                <p className="tapping-timer">Keep going, steady and even. {rec.elapsedS.toFixed(1)} s</p>
              </>
            )}
            {step === "submitting" && <p className="tapping-timer">Checking the recording…</p>}
            {step === "ready" && (
              <p className="tapping-timer">
                {error ? "Ready to try again." : "Press Start when you're ready."}
              </p>
            )}
          </div>
          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}
          <div className="book-actions">
            <button type="button" className="btn-ghost" onClick={reset}>
              Cancel
            </button>
            {recording && (
              <button type="button" className="btn-primary" onClick={stopNow} disabled={!longEnough}>
                {longEnough ? "Stop" : "Keep going…"}
              </button>
            )}
            {step === "ready" && (
              <button type="button" className="btn-primary" onClick={() => begin(rep)}>
                Start repetition {rep}
              </button>
            )}
          </div>
        </div>
      )}

      {step === "result" && current && (
        <div className="tapping-result" role="status">
          <p className={current.valid ? "" : "checkin-urgent"}>{current.message}</p>
          {!current.valid && current.reasons?.length > 0 && (
            <ul className="sheet-tips">
              {current.reasons.map((r) => (
                <li key={r}>{String(r).replace(/_/g, " ")}</li>
              ))}
            </ul>
          )}
          {current.tips?.length > 0 && (
            <ul className="sheet-tips">
              {current.tips.map((t) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
          )}
          <QualityRows quality={current.recording_quality} />
          {current.valid && <ExperimentalScore score={current.experimental_score} />}
          <div className="book-actions">
            {!current.valid && (
              <button type="button" className="btn-secondary" onClick={() => begin(rep)}>
                Record this one again
              </button>
            )}
            {rep < REPETITIONS ? (
              <button type="button" className="btn-primary" onClick={() => begin(rep + 1)}>
                Next: repetition {rep + 1}
              </button>
            ) : (
              <button type="button" className="btn-primary" onClick={() => setStep("done")}>
                Finish
              </button>
            )}
            {rep < REPETITIONS && validCount > 0 && (
              <button type="button" className="btn-ghost" onClick={() => setStep("done")}>
                Finish early
              </button>
            )}
          </div>
        </div>
      )}

      {step === "done" && (
        <div className="tapping-result" role="status">
          <p>
            {validCount === 0
              ? "None of the recordings could be used this time. Try again in a quieter spot with the microphone closer."
              : `Thanks. ${validCount} usable recording${validCount === 1 ? "" : "s"} saved. Your next assessment compares them with your earlier ones.`}
          </p>
          <ul className="readings">
            {results.map((r) => (
              <li key={r.task_id}>
                <div>
                  <b>Repetition {r.repetition_index}</b>
                  <small>
                    {typeof r.recording_quality?.snr_db === "number" && `${Math.round(r.recording_quality.snr_db)} dB signal to noise`}
                  </small>
                </div>
                <em className={`badge ${r.valid ? "normal" : "watch"}`}>{r.valid ? "Usable" : "Check"}</em>
              </li>
            ))}
          </ul>
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
    <section className="card" aria-labelledby="vowel-title">
      <div className="card-head">
        <h2 id="vowel-title">Sustained vowel</h2>
        {badge}
      </div>
      {body}
    </section>
  );
}
