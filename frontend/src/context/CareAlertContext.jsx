import { createContext, useContext, useEffect, useRef, useState } from "react";
import { grantConsent } from "../api/client.js";
import useRiskAssessment, { PATHWAYS, riskLevel } from "../hooks/useRiskAssessment.js";
import { useAppointments } from "./AppointmentsContext.jsx";
import { useSession } from "./SessionContext.jsx";

// Owns the risk assessment for the whole app. When the overall index (the
// highest of the condition bars) reaches AUTO_BOOK_THRESHOLD, CareSync flags it
// for follow-up and opens the pop-up. Nothing is booked without the user's say.
//
// The AI booking agent plans a request for the highest of the three condition
// bars (POST /api/appointments/auto): a real clinic nearby, three times, and
// which of its three booking channels (web form, e-mail, phone call) can reach
// it. The pop-up lets the user pick one available channel and the agent
// contacts the clinic that way only. Alert statuses: "planning" -> "consent"
// (sharing with clinics not allowed yet) -> "choose" -> "sent"; "failed" when
// no clinic could be planned; "declined" after "Not now".
export const AUTO_BOOK_THRESHOLD = 85;
const ALERT_KEY = "caresync-care-alert";
const DEV_KEY = "caresync-dev-risk";
const OPEN_AGENT = ["planning", "consent", "choose"]; // released if the risk drops before a choice

const load = (key) => {
  try {
    return JSON.parse(localStorage.getItem(key));
  } catch {
    return null;
  }
};
const store = (key, value) => {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable */
  }
};

// Backend appointment status -> the pop-up's step.
function stepFor(appt) {
  if (appt.status === "awaiting_approval") return "choose";
  if (appt.status === "failed" || appt.status === "cancelled" || appt.status === "declined") return "failed";
  return "sent"; // proposed, processing, requested, needs_user_info, confirmed
}

// Title and one line for the notification list and the chat banner.
function describe(alert, appt) {
  const clinic = appt?.clinic?.name;
  const area = PATHWAYS[alert.pathway]?.name ?? "A condition";
  const body = {
    planning: `${area} risk reached ${alert.score}. CareSync is finding a clinic near you.`,
    consent: `${area} risk reached ${alert.score}. Allow CareSync to contact a clinic for you.`,
    choose: `${area} risk reached ${alert.score}. Choose how CareSync should contact ${clinic ?? "the clinic"}.`,
    sent: `CareSync contacted ${clinic ?? "the clinic"}. We'll tell you when it replies.`,
    failed: "CareSync couldn't arrange a visit automatically. You can book one yourself.",
    declined: `You chose not to contact ${clinic ?? "a clinic"} for now.`,
  }[alert.status];
  return { title: alert.status === "sent" ? "Appointment requested" : "Flagged for follow-up", body };
}

const CareAlertContext = createContext(null);

export function CareAlertProvider({ children }) {
  const assessed = useRiskAssessment();
  const { loaded, startAutoVisit, approveVisit, declineVisit, agentById } = useAppointments();
  const { signedIn } = useSession();
  const [devScore, setDevScoreState] = useState(() => (import.meta.env.DEV ? load(DEV_KEY) : null));
  // Only the agent's requests are remembered; a browser-only hold saved by an older version is dropped.
  const [alert, setAlertState] = useState(() => {
    const saved = load(ALERT_KEY);
    return saved?.kind === "agent" ? saved : null;
  });
  const [appt, setAppt] = useState(null); // the agent's request, as the backend last returned it
  const [promptOpen, setPromptOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const starting = useRef(false);

  const score = devScore ?? assessed.score;
  const risk = { ...assessed, score, level: riskLevel(score) };

  const setAlert = (next) => {
    setAlertState(next);
    store(ALERT_KEY, next);
  };
  const setDevScore = (s) => {
    setDevScoreState(s);
    store(DEV_KEY, s);
  };

  // Ask the agent for the pathway's request (an active one is reused) and move to its step.
  const startAgent = async (base) => {
    if (starting.current) return;
    starting.current = true;
    setError("");
    try {
      const res = await startAutoVisit(base.pathway);
      setAppt(res);
      setAlert({ ...base, status: stepFor(res), appointmentId: res.id });
    } catch (e) {
      if (e?.status === 403) setAlert({ ...base, status: "consent" });
      else {
        setAlert({ ...base, status: "failed" });
        setError(
          e?.status === 404
            ? "CareSync needs a risk assessment for this area before it can find a clinic."
            : "Couldn't reach CareSync to arrange the visit. Try again in a moment."
        );
      }
    } finally {
      starting.current = false;
    }
  };

  useEffect(() => {
    if (!signedIn) return; // guests have no assessment to flag
    if (score >= AUTO_BOOK_THRESHOLD && !alert) {
      const base = { kind: "agent", status: "planning", pathway: assessed.bookingPathway, score, read: false, createdAt: Date.now() };
      setAlert(base);
      setPromptOpen(true);
      startAgent(base);
    } else if (score < AUTO_BOOK_THRESHOLD && alert) {
      // Risk came back down before the user decided: release the alert. A request
      // the agent planned stays under Appointments until approved or declined.
      if (OPEN_AGENT.includes(alert.status)) {
        setAlert(null);
        setAppt(null);
        setPromptOpen(false);
      }
    }
  }, [signedIn, score, alert, assessed.bookingPathway]);

  // After a reload: re-ask a request that was still being planned, and pick up
  // the saved request's latest state once the appointment list has it.
  useEffect(() => {
    if (!signedIn || !alert) return;
    if (alert.status === "planning") startAgent(alert);
    else if (alert.appointmentId && !appt) {
      const known = agentById(alert.appointmentId);
      if (known) setAppt(known.raw);
    }
  }, [signedIn, alert?.status, alert?.appointmentId, loaded]);

  // "Allow and continue": the share_with_clinic consent, then plan the request.
  const allowSharing = async () => {
    setError("");
    setConfirming(true);
    try {
      await grantConsent("share_with_clinic");
    } catch {
      setError("Couldn't save your permission. Try again in a moment.");
      setConfirming(false);
      return;
    }
    setConfirming(false);
    const base = { ...alert, status: "planning" };
    setAlert(base);
    startAgent(base);
  };

  // The user picked web, email or phone: the agent contacts the clinic that way.
  const bookBy = async (channel) => {
    if (!alert?.appointmentId) return;
    setError("");
    setConfirming(true);
    try {
      const updated = await approveVisit(alert.appointmentId, channel);
      setAppt(updated.raw);
      setAlert({ ...alert, status: stepFor(updated.raw), channel, read: true });
    } catch (e) {
      setError(
        e?.status === 409
          ? "This request was already handled (maybe from the e-mail). Check Appointments."
          : "Couldn't contact the clinic. Try again in a moment, or pick another way."
      );
    } finally {
      setConfirming(false);
    }
  };

  const decline = async () => {
    if (alert?.status === "choose" && alert.appointmentId) {
      try {
        await declineVisit(alert.appointmentId);
      } catch {
        /* already handled elsewhere: just close */
      }
    }
    setAlert({ ...alert, status: "declined" });
    setPromptOpen(false);
  };

  // A saved alert belongs to the signed-in account; guests never see it.
  const value = {
    risk,
    alert: signedIn ? alert : null,
    alertText: signedIn && alert ? describe(alert, appt) : null,
    appointment: appt,
    promptOpen: signedIn && promptOpen,
    confirming,
    error,
    devScore,
    setDevScore,
    allowSharing,
    bookBy,
    retry: () => alert && startAgent({ ...alert, status: "planning" }),
    openPrompt: () => {
      setError("");
      setPromptOpen(true);
    },
    closePrompt: () => setPromptOpen(false),
    decline,
    markAlertRead: () => alert && !alert.read && setAlert({ ...alert, read: true }),
    // Dev: forget the alert; if the score is still high a new one is raised right away.
    resetAlert: () => {
      setAppt(null);
      setAlert(null);
    },
  };

  return <CareAlertContext.Provider value={value}>{children}</CareAlertContext.Provider>;
}

export function useCareAlert() {
  return useContext(CareAlertContext);
}
