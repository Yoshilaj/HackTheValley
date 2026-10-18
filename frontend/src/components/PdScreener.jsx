import { useEffect, useState } from "react";
import { getPdScreener, submitPdScreener } from "../api/client.js";

// Parkinson's screener (spec §5.1): the backend's yes/no items, answered by
// their ids and sent back as { answers: { id: bool } }. The reply says whether
// the screener rule (PD-01) would fire and which tasks to do next.
export default function PdScreener({ onSubmitted }) {
  const [screener, setScreener] = useState(null);
  const [answers, setAnswers] = useState({});
  const [loadError, setLoadError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    let cancelled = false;
    getPdScreener()
      .then((s) => !cancelled && setScreener(s))
      .catch(() => !cancelled && setLoadError(true));
    return () => {
      cancelled = true;
    };
  }, []);

  const set = (id, value) => setAnswers((a) => ({ ...a, [id]: value }));
  const unanswered = (screener?.items ?? []).filter((i) => answers[i.id] == null);

  const submit = async (e) => {
    e.preventDefault();
    if (unanswered.length) {
      setError(`Answer every question (${unanswered.length} left).`);
      return;
    }
    setSaving(true);
    setError("");
    try {
      const res = await submitPdScreener(answers);
      setResult(res);
      onSubmitted?.(res);
    } catch (err) {
      setError(err?.detail ? String(err.detail) : "Couldn't save your answers. Try again in a moment.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="card" aria-labelledby="screener-title">
      <div className="card-head">
        <h2 id="screener-title">Movement and sleep questions</h2>
        {result && <em className="badge normal">Saved</em>}
      </div>
      {loadError && (
        <p className="field-error" role="alert">
          Couldn't load the questions. Check your connection and reload.
        </p>
      )}
      {!screener && !loadError && <p className="field-hint">Loading questions…</p>}
      {screener && (
        <form onSubmit={submit} noValidate className="screener-form">
          <p className="field-hint">{screener.period}</p>
          <ol className="screener-items">
            {screener.items.map((item, i) => (
              <li key={item.id}>
                <fieldset className="book-field screener-item">
                  <legend>
                    <span className="screener-n">{i + 1}.</span> {item.text}
                  </legend>
                  <div className="segmented screener-yesno" role="radiogroup" aria-label={item.text}>
                    {[
                      ["yes", true],
                      ["no", false],
                    ].map(([label, value]) => (
                      <label key={label}>
                        <input
                          type="radio"
                          name={`screener-${item.id}`}
                          checked={answers[item.id] === value}
                          onChange={() => set(item.id, value)}
                        />
                        <span>{label === "yes" ? "Yes" : "No"}</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
              </li>
            ))}
          </ol>
          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}
          <div className="book-actions">
            <button type="submit" className="btn-primary" disabled={saving}>
              {saving ? "Saving…" : result ? "Save again" : "Save answers"}
            </button>
          </div>
          {result && (
            <div className="screener-result" role="status">
              <p>
                <b>
                  {result.motor_count} movement and {result.nonmotor_count} other item{result.nonmotor_count === 1 ? "" : "s"} answered yes.
                </b>{" "}
                {result.pd01_would_fire
                  ? "That pattern is worth a closer look: the assessment will note it, and the tapping and voice tasks below add detail."
                  : "Nothing in these answers stands out on its own."}
              </p>
              <p className="field-hint">{result.validation_note}</p>
            </div>
          )}
        </form>
      )}
    </section>
  );
}
