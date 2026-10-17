import { createContext, useContext, useEffect, useState } from "react";
import useRiskAssessment, { riskLevel } from "../hooks/useRiskAssessment.js";
import useRecommendations from "../hooks/useRecommendations.js";
import { useAppointments } from "./AppointmentsContext.jsx";
import { useSession } from "./SessionContext.jsx";

// Owns the risk assessment for the whole app. When the overall index reaches
// AUTO_BOOK_THRESHOLD, CareSync flags it for follow-up: it holds a visit with
// a specialist for the suspected condition and asks the user to confirm it.
// Nothing is booked without that confirmation.
export const AUTO_BOOK_THRESHOLD = 85;
const ALERT_KEY = "caresync-care-alert";
const DEV_KEY = "caresync-dev-risk";
export const HOLD_HOURS = 24;

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

// Matching specialist if there is one, otherwise the nearest; earliest slot.
function holdVisit(recs, suspected, score, note) {
  const open = recs.filter((r) => r.slots?.length);
  const rec =
    open.find((r) => suspected && r.specialty === suspected.specialty) ??
    [...open].sort((a, b) => a.distanceKm - b.distanceKm)[0];
  if (!rec) return null;
  const { slots, ...snapshot } = rec;
  return {
    status: "pending", // "pending" | "declined" | "confirmed"
    read: false,
    rec: snapshot,
    slot: [...slots].sort()[0],
    suspected: suspected ?? { condition: "Needs a check-up", why: note, bring: "Photo ID and health card" },
    score,
    createdAt: Date.now(),
  };
}

const CareAlertContext = createContext(null);

export function CareAlertProvider({ children }) {
  const assessed = useRiskAssessment();
  const { items: recs } = useRecommendations();
  const { bookVisit, isBooked, loaded } = useAppointments();
  const { signedIn } = useSession();
  const [devScore, setDevScoreState] = useState(() => (import.meta.env.DEV ? load(DEV_KEY) : null));
  const [alert, setAlertState] = useState(() => load(ALERT_KEY));
  const [promptOpen, setPromptOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");

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

  useEffect(() => {
    if (!signedIn) return; // guests have no assessment to flag
    if (score >= AUTO_BOOK_THRESHOLD && !alert) {
      const held = holdVisit(recs, assessed.suspected, score, assessed.note);
      if (held) {
        setAlert(held);
        setError("");
        setPromptOpen(true);
      }
    } else if (score < AUTO_BOOK_THRESHOLD && alert && alert.status !== "confirmed") {
      // Risk came back down before the user said yes: release the hold.
      setAlert(null);
      setPromptOpen(false);
    }
  }, [signedIn, score, alert, recs, assessed.suspected, assessed.note]);

  // The confirmed visit was cancelled from Appointments: offer the hold again
  // instead of showing "confirmed" for a visit that no longer exists.
  const confirmedGone = loaded && alert?.status === "confirmed" && !isBooked(alert.rec.id);
  useEffect(() => {
    if (confirmedGone) setAlert({ ...alert, status: "declined" });
  }, [confirmedGone]);

  const confirm = async () => {
    if (!alert) return;
    setError("");
    setConfirming(true);
    try {
      await bookVisit(alert.rec, alert.slot, `Held by CareSync: ${alert.suspected.condition}`);
    } catch {
      setError("Couldn't confirm with the clinic. Try again in a moment.");
      return;
    } finally {
      setConfirming(false);
    }
    setAlert({ ...alert, status: "confirmed", read: true });
  };

  // A saved alert belongs to the signed-in account; guests never see it.
  const value = {
    risk,
    alert: signedIn ? alert : null,
    promptOpen: signedIn && promptOpen,
    confirming,
    error,
    devScore,
    setDevScore,
    confirm,
    openPrompt: () => {
      setError("");
      setPromptOpen(true);
    },
    closePrompt: () => setPromptOpen(false),
    decline: () => {
      setAlert({ ...alert, status: "declined" });
      setPromptOpen(false);
    },
    markAlertRead: () => alert && !alert.read && setAlert({ ...alert, read: true }),
    // Dev: forget the alert; if the score is still high a new one is held right away.
    resetAlert: () => setAlert(null),
  };

  return <CareAlertContext.Provider value={value}>{children}</CareAlertContext.Provider>;
}

export function useCareAlert() {
  return useContext(CareAlertContext);
}
