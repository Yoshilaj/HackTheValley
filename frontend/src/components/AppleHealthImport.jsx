import { useEffect, useRef, useState } from "react";
import { getUploadJob, uploadAppleHealth } from "../api/client.js";

// Apple Watch data arrives as the Health app's export (Health → profile →
// Export All Health Data → export.zip). The backend parses it in the
// background into daily metrics: walking speed, step length, double support
// and asymmetry (PD-02), sleep (GEN-01), resting heart rate, and any
// irregular-rhythm or ECG results (RHY-01).
const POLL_MS = 1500;

export default function AppleHealthImport() {
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const input = useRef(null);

  useEffect(() => {
    if (!job || (job.status !== "pending" && job.status !== "running")) return undefined;
    const t = setTimeout(() => getUploadJob(job.id).then(setJob).catch(() => {}), POLL_MS);
    return () => clearTimeout(t);
  }, [job]);

  const pick = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    setError("");
    setJob(null);
    try {
      const res = await uploadAppleHealth(file);
      setJob({ id: res.job_id, status: "pending" });
    } catch (err) {
      setError(err?.detail ? String(err.detail) : "Couldn't upload the export. It should be export.zip or export.xml, up to the size limit.");
    } finally {
      setBusy(false);
    }
  };

  const counts = job?.result ?? {};
  return (
    <section className="card" aria-labelledby="apple-title">
      <div className="card-head">
        <h2 id="apple-title">Apple Watch data</h2>
        {job?.status === "done" && <em className="badge normal">Imported</em>}
      </div>
      <p className="field-hint">
        On your iPhone: Health → your picture → Export All Health Data, then AirDrop or save the export.zip here.
        Walking speed, step length, sleep and any irregular-rhythm or ECG results feed your assessment.
      </p>
      <input ref={input} type="file" accept=".zip,.xml,application/zip,text/xml" className="sr-only" onChange={pick} />
      <div className="book-actions apple-actions">
        <button type="button" className="btn-secondary" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "Uploading…" : "Choose export.zip"}
        </button>
      </div>
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      {job && (
        <p className="field-hint" role="status">
          {job.status === "pending" || job.status === "running"
            ? "Reading the export…"
            : job.status === "done"
              ? `Imported ${counts.metrics?.inserted ?? 0} new daily values, ${counts.bp_readings ?? 0} blood pressure readings and ${counts.rhythm_results ?? 0} rhythm results.`
              : `Import failed${job.error ? `: ${job.error}` : "."}`}
        </p>
      )}
    </section>
  );
}
