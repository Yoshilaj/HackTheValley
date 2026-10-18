import { useCallback, useEffect, useRef, useState } from "react";
import { getStory, getStoryAudio, startStory, submitStoryStep } from "../api/client.js";
import useRecorder from "../hooks/useRecorder.js";
import LevelMeter from "./LevelMeter.jsx";

// Story recall battery (spec §1, adapted from the ADNI4 story protocol): listen
// to a short story and retell it, a second story the same way, a minute of
// naming animals, a short break, then retell the first story again. The
// backend owns the session (POST /api/tasks/story/start, then one POST per
// step) and tells us the current step, its instruction, the story audio for
// listen steps, how long the break still has to run, and the result at the end
// (a 0-100 semantic recall score, experimental). The task id is kept in this
// tab's sessionStorage so a reload during the break resumes the session.
const STORAGE_KEY = "caresync-story-task";
const FLUENCY_S = 60;
const RESYNC_MS = 30_000; // during the delay, re-ask the server how long is left

const STEP_LABELS = {
  story1_listen: "Listen to story 1",
  story1_recall: "Retell story 1",
  story2_listen: "Listen to story 2",
  story2_recall: "Retell story 2",
  fluency_60s: "Name animals for a minute",
  delay: "A short break",
  story1_delayed_recall: "Retell story 1 again",
};

const kindOf = (step) =>
  !step ? null : step.endsWith("_listen") ? "listen" : step === "fluency_60s" ? "fluency" : step === "delay" ? "delay" : "recall";

const saved = () => {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
};
const remember = (id) => {
  try {
    if (id) sessionStorage.setItem(STORAGE_KEY, id);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable: a reload starts over */
  }
};

function errorText(err) {
  if (err?.status === 503) return `${err.detail ?? "The speech service isn't available."} Voice tasks need the speech service configured on the server.`;
  if (err?.status === 413) return "That recording was too large to send.";
  return err?.detail ? String(err.detail) : "Couldn't reach CareSync. Check your connection and try again.";
}

export default function StoryRecallTask({ onSubmitted, embedded = false }) {
  const rec = useRecorder();
  const [status, setStatus] = useState(null); // the backend's StoryStatusOut
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [audioUrl, setAudioUrl] = useState(null);
  const [audioError, setAudioError] = useState(false);
  const [played, setPlayed] = useState(0);
  const [ended, setEnded] = useState(false);
  const [delayLeft, setDelayLeft] = useState(null);
  const audioRef = useRef(null);
  const delayBase = useRef(null); // { remaining, at } when the delay step was last read
  const autoSent = useRef(false);

  const step = status?.current_step ?? null;
  const kind = kindOf(step);
  const finished = Boolean(status?.finished);

  const apply = useCallback((s) => {
    setStatus(s);
    setError("");
    if (s?.finished) remember(null);
    else if (s?.task_id) remember(s.task_id);
    if (s?.current_step === "delay" || s?.current_step === "fluency_60s") {
      delayBase.current = { remaining: s.delay_remaining_s ?? 0, at: Date.now() };
    }
    autoSent.current = false;
  }, []);

  // Resume a session this tab started earlier (e.g. a reload during the break).
  useEffect(() => {
    const id = saved();
    if (!id) return;
    getStory(id)
      .then((s) => (s.finished ? remember(null) : apply(s)))
      .catch(() => remember(null));
  }, [apply]);

  // Listen steps: fetch the story audio with the sign-in token.
  useEffect(() => {
    if (kind !== "listen" || !status?.audio_url) return undefined;
    let url = null;
    let cancelled = false;
    setAudioError(false);
    setPlayed(0);
    setEnded(false);
    getStoryAudio(status.audio_url)
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setAudioUrl(url);
      })
      .catch((err) => {
        if (cancelled) return;
        setAudioError(true);
        if (err?.status === 503) setError(errorText(err));
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
      setAudioUrl(null);
    };
  }, [kind, status?.audio_url]);

  // The break: count down locally, resync with the server now and then, and move on at zero.
  useEffect(() => {
    if (kind !== "delay") {
      setDelayLeft(null);
      return undefined;
    }
    const tick = () => {
      const b = delayBase.current;
      const left = b ? Math.max(0, b.remaining - (Date.now() - b.at) / 1000) : 0;
      setDelayLeft(left);
      if (left <= 0 && !autoSent.current && !busy) {
        autoSent.current = true;
        advance({ step: "delay" });
      }
    };
    tick();
    const timer = setInterval(tick, 1000);
    const resync = setInterval(() => status?.task_id && getStory(status.task_id).then(apply).catch(() => {}), RESYNC_MS);
    return () => {
      clearInterval(timer);
      clearInterval(resync);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, status?.task_id, busy]);

  // A recording finished (Stop pressed, or the time limit): send it as this step.
  useEffect(() => {
    if ((kind !== "recall" && kind !== "fluency") || rec.state !== "stopped" || !rec.take || busy) return;
    const take = rec.take;
    rec.reset();
    advance({ step, blob: take.blob, ext: take.ext });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rec.state, rec.take, kind, step, busy]);

  const advance = async (fields) => {
    if (!status?.task_id) return;
    setBusy(true);
    setError("");
    try {
      const s = await submitStoryStep(status.task_id, fields);
      apply(s);
      if (s.finished) onSubmitted?.(s);
    } catch (err) {
      if (err?.status === 409 && typeof err.body?.delay_remaining_s === "number") {
        delayBase.current = { remaining: err.body.delay_remaining_s, at: Date.now() };
        autoSent.current = false;
      } else if (err?.status === 409 && err.body?.current_step) {
        getStory(status.task_id).then(apply).catch(() => {});
      } else {
        setError(errorText(err));
      }
    } finally {
      setBusy(false);
    }
  };

  const begin = async () => {
    setBusy(true);
    setError("");
    try {
      apply(await startStory());
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const abandon = () => {
    rec.reset();
    remember(null);
    setStatus(null);
    setError("");
  };

  const maxRecordS = kind === "fluency" ? FLUENCY_S : (status?.max_recall_s ?? 180);
  const recording = rec.state === "recording";
  const stepNo = status ? Math.min(status.step_index + 1, status.steps.length) : 0;

  const badge = status && !finished && (
    <em className="badge normal">
      Step {stepNo} of {status.steps.length}
    </em>
  );
  const body = (
    <>

      {!status && (
        <div className="tapping-intro">
          <p>Hear two short stories and retell them, name animals for a minute, then retell the first story again. About eight minutes.</p>
          <div className="task-preview" aria-hidden="true">
            <ol className="task-steps preview">
              {["Listen", "Retell", "Listen", "Retell", "Name animals", "Break", "Retell story 1"].map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
          </div>
          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}
          {rec.state === "unsupported" && (
            <p className="field-error" role="alert">
              This browser can't record audio. Try Chrome, Edge, Firefox or Safari.
            </p>
          )}
          <div className="book-actions">
            <button type="button" className="btn-primary" onClick={begin} disabled={busy || rec.state === "unsupported"}>
              {busy ? "Starting…" : "Start the story task"}
            </button>
          </div>
        </div>
      )}

      {status && !finished && (
        <div className="tapping-run">
          <ol className="task-steps" aria-label="Steps">
            {status.steps.map((s, i) => (
              <li key={s} className={i < status.step_index ? "done" : i === status.step_index ? "now" : ""}>
                {STEP_LABELS[s] ?? s}
              </li>
            ))}
          </ol>
          <p className="tapping-hand">{STEP_LABELS[step] ?? step}</p>
          <p>{status.instruction}</p>

          {kind === "listen" && (
            <div className="tapping-pad vowel-pad">
              {/* Play only: the story text is never shown and the audio can't be scrubbed. */}
              <audio
                ref={audioRef}
                src={audioUrl ?? undefined}
                preload="auto"
                onTimeUpdate={(e) => setPlayed((p) => Math.max(p, e.currentTarget.currentTime))}
                onEnded={() => setEnded(true)}
                onError={() => setAudioError(true)}
              />
              {audioError ? (
                <p className="tapping-timer">The story audio couldn't be played here. You can continue anyway.</p>
              ) : (
                <p className="tapping-timer">{ended ? "Finished. Press Continue." : audioUrl ? "Press Play and listen carefully." : "Loading the story…"}</p>
              )}
              <div className="book-actions">
                {!audioError && (
                  <button type="button" className="btn-secondary" disabled={!audioUrl || busy} onClick={() => audioRef.current?.play()}>
                    {played > 0 && !ended ? "Playing…" : "Play the story"}
                  </button>
                )}
                <button
                  type="button"
                  className="btn-primary"
                  disabled={busy || (!ended && !audioError)}
                  onClick={() => advance({ step, playedS: Math.round(played * 10) / 10 })}
                >
                  {busy ? "Saving…" : "Continue"}
                </button>
              </div>
            </div>
          )}

          {(kind === "recall" || kind === "fluency") && (
            <div className={`tapping-pad vowel-pad ${recording ? "live" : ""}`} aria-live="polite">
              {recording ? (
                <>
                  <p className="countdown recording-dot">
                    <span aria-hidden="true" /> {Math.max(0, Math.ceil(maxRecordS - rec.elapsedS))}
                  </p>
                  <LevelMeter level={rec.level} />
                  <p className="tapping-timer">{kind === "fluency" ? "Keep naming animals…" : "Take your time. Press Done when you've finished."}</p>
                </>
              ) : busy ? (
                <p className="tapping-timer">Transcribing and saving…</p>
              ) : (
                <p className="tapping-timer">
                  {kind === "fluency" ? "Recording stops after one minute." : `Up to ${Math.round(maxRecordS / 60)} minutes.`}
                </p>
              )}
              <div className="book-actions">
                {recording ? (
                  <button type="button" className="btn-primary" onClick={() => rec.stop()} disabled={rec.elapsedS < 2}>
                    Done
                  </button>
                ) : (
                  <button type="button" className="btn-primary" disabled={busy} onClick={() => rec.start({ maxMs: maxRecordS * 1000 })}>
                    {kind === "fluency" ? "Start the minute" : "Record"}
                  </button>
                )}
              </div>
            </div>
          )}

          {kind === "delay" && (
            <div className="tapping-pad vowel-pad">
              <p className="countdown">
                {delayLeft == null ? "…" : `${Math.floor(delayLeft / 60)}:${String(Math.floor(delayLeft % 60)).padStart(2, "0")}`}
              </p>
              <p className="tapping-timer">
                {delayLeft > 0 ? "Keep this page open. The last step starts by itself." : busy ? "Starting the last step…" : "Ready."}
              </p>
              {import.meta.env.DEV && (
                <button type="button" className="btn-ghost" disabled={busy} onClick={() => advance({ step: "delay", forceDelay: true })}>
                  Dev: skip the wait
                </button>
              )}
            </div>
          )}

          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}
          <div className="book-actions">
            <button type="button" className="btn-ghost" onClick={abandon} disabled={recording}>
              Stop the task
            </button>
          </div>
        </div>
      )}

      {status && finished && status.result && (
        <div className="tapping-result" role="status">
          <p className={status.result.valid ? "" : "checkin-urgent"}>{status.result.message}</p>
          <dl className="sheet-rows">
            {typeof status.result.score === "number" && (
              <div>
                <dt>{status.result.score_name || "Recall score"}</dt>
                <dd>{Math.round(status.result.score)} / 100</dd>
              </div>
            )}
            {Object.entries(status.result.per_recall ?? {})
              .filter(([, v]) => typeof v === "number")
              .map(([k, v]) => (
                <div key={k}>
                  <dt>{k.replace(/_/g, " ")}</dt>
                  <dd>{Math.round(v)}</dd>
                </div>
              ))}
          </dl>
          {status.result.invalid_reasons?.length > 0 && (
            <ul className="sheet-tips">
              {status.result.invalid_reasons.map((r) => (
                <li key={r}>{String(r).replace(/_/g, " ")}</li>
              ))}
            </ul>
          )}
          <p className="field-hint">Experimental: the score is how much of each story your retelling kept. It is read alongside your other checks, never on its own.</p>
          <div className="book-actions">
            <button type="button" className="btn-secondary" onClick={abandon}>
              Done
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
    <section className="card" aria-labelledby="story-title">
      <div className="card-head">
        <h2 id="story-title">Story recall</h2>
        {badge}
      </div>
      {body}
    </section>
  );
}
