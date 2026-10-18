import { runRiskAssessment } from "../api/client.js";

// After the user adds data (a reading, a check-in, a task, a medication), ask
// the backend to re-assess straight away so the dashboard reflects it, then
// tell the risk hook to re-read. The scheduled nightly and weekly runs on the
// server remain the main mechanism; this is only for immediacy, so failures
// are ignored and callers never await it on the UI path.
export const RISK_UPDATED_EVENT = "caresync:risk-updated";

export async function reassessNow() {
  try {
    await runRiskAssessment();
  } catch {
    /* rate-limited or offline: the next scheduled run picks it up */
  }
  window.dispatchEvent(new Event(RISK_UPDATED_EVENT));
}
