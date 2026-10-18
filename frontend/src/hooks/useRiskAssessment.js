import { useCallback, useEffect, useState } from "react";
import { getRiskCards } from "../api/client.js";
import { useChatHistory } from "../context/ChatHistoryContext.jsx";
import { useSession } from "../context/SessionContext.jsx";
import { RISK_UPDATED_EVENT } from "../utils/riskEvents.js";

// Risk score (0-100, higher = more risk) and the per-condition estimates, from
// GET /api/risk/latest, re-fetched as the chat history grows and whenever
// something re-ran the assessment (RISK_UPDATED_EVENT). The backend scores four pathways
// (cardio, cognitive, parkinsons, general), each with a 0-100 follow-up
// priority, a level and the findings behind it; only cardio has a true
// percentage (10-year Framingham risk). toAssessment maps that onto this shape:
//   overall score = the highest follow-up priority
//   conditions    = cardio (with its %), cognitive and Parkinson's (findings only)
//   suspected     = the highest-priority pathway with a finding (drives the 85+ hold)
//   bookingPathway = the highest of the three condition bars (cardio, cognitive,
//                    parkinsons): the pathway the 85+ auto-booking asks the agent for

const clamp = (n) => Math.min(100, Math.max(0, Math.round(n)));

// Level for the overall 0-100 index; matches the card colours (black from 90).
export function riskLevel(score) {
  if (score < 34) return "Low";
  if (score < 67) return "Moderate";
  if (score < 90) return "High";
  return "Very high";
}

export const PATHWAYS = {
  cardio: { id: "cardio", name: "Stroke / heart disease", outcome: "have a heart attack or stroke", specialty: "Cardiology" },
  cognitive: { id: "alzheimers", name: "Alzheimer's disease", outcome: "develop dementia", specialty: "Memory clinic" },
  parkinsons: { id: "parkinsons", name: "Parkinson's disease", outcome: "develop Parkinson's disease", specialty: "Neurology" },
};

// Backend level -> what the card says, and the colour band it gets.
const FINDINGS = {
  insufficient_data: ["Not enough data", "Low"],
  invalid_measurement: ["Check your readings", "Moderate"],
  no_finding: ["No findings", "Low"],
  watch: ["Keep an eye on", "Moderate"],
  followup_recommended: ["Follow-up recommended", "High"],
};

// Canadian Cardiovascular Society bands for 10-year Framingham risk: low under 10 %, high from 20 %.
const FRAMINGHAM_BANDS = [10, 20];
// Findings as sentences: "A. B." (the backend sends them without full stops).
const sentences = (texts) => {
  const parts = texts.filter(Boolean).map((t) => t.replace(/[.\s]+$/, ""));
  return parts.length ? `${parts.join(". ")}.` : "";
};
const TO_BRING = "Photo ID, health card, your recent readings and a list of current medications";

function toCondition(card) {
  const meta = PATHWAYS[card.pathway];
  const [finding, tone] = FINDINGS[card.level] ?? [card.level, "Low"];
  const estimate = (card.probabilities ?? []).find(
    (p) => p.kind === "population_estimate" && p.status === "ok" && Number.isFinite(p.value_pct)
  );
  return {
    id: meta.id,
    pathway: card.pathway,
    name: meta.name,
    outcome: meta.outcome,
    level: card.level,
    priority: card.priority_score ?? null, // follow-up priority 0-100 (null = not enough data)
    risk: estimate ? estimate.value_pct : null,
    horizon: estimate ? 10 : null,
    typical: null,
    bands: estimate ? FRAMINGHAM_BANDS : null,
    method: estimate ? estimate.name : card.score_label,
    finding,
    tone,
    headline: card.headline,
    details: {
      summary: card.headline,
      findings: (card.drivers ?? []).map((d) => d.text).filter(Boolean),
      tips: (card.preventive_measures ?? []).map((p) => p.text).filter(Boolean),
    },
  };
}

function toAssessment(cards, chatCount) {
  const scored = cards.map((c) => ({ ...c, score: c.priority_score ?? 0 }));
  const top = scored.reduce((a, b) => (b.score > a.score ? b : a), scored[0]);
  const lastWeek = scored
    .map((c) => c.trend?.at(-2)?.priority_score)
    .filter((n) => Number.isFinite(n));
  const flagged = top && top.score > 0 && PATHWAYS[top.pathway];
  // The highest of the three condition bars: what the 85+ auto-booking books for.
  const bar = scored.filter((c) => PATHWAYS[c.pathway]).reduce((a, b) => (!a || b.score > a.score ? b : a), null);
  return {
    score: clamp(top?.score ?? 0),
    bookingPathway: bar?.pathway ?? "cardio",
    previous: lastWeek.length ? { score: Math.max(...lastWeek) } : null,
    updatedAt: Date.now(),
    basis: { chats: chatCount, diagnoses: 0 },
    note: "",
    suspected: flagged
      ? {
          condition: PATHWAYS[top.pathway].name,
          specialty: PATHWAYS[top.pathway].specialty,
          why: sentences((top.drivers ?? []).map((d) => d.text)) || top.headline,
          bring: TO_BRING,
        }
      : null,
    conditions: ["cognitive", "cardio", "parkinsons"]
      .map((p) => cards.find((c) => c.pathway === p))
      .filter(Boolean)
      .map(toCondition),
  };
}

// Until the backend answers, a blank assessment rather than someone else's numbers.
const LOADING = { score: 0, previous: null, updatedAt: null, basis: null, note: "", suspected: null, conditions: [] };

export default function useRiskAssessment() {
  const { chats } = useChatHistory();
  const { signedIn } = useSession();
  const [data, setData] = useState(LOADING);
  const [cards, setCards] = useState([]); // the backend's cards as returned, for the trend charts
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((n) => n + 1), []);

  // Re-read whenever the chat history grows.
  const messageCount = chats.reduce((sum, c) => sum + c.messages.length, 0);

  useEffect(() => {
    if (!signedIn) {
      setData(LOADING);
      setCards([]);
      return;
    }
    let cancelled = false;
    getRiskCards()
      .then((list) => {
        if (cancelled || !Array.isArray(list) || !list.length) return;
        setCards(list);
        setData(toAssessment(list, chats.length));
      })
      .catch(() => {
        /* keep the last assessment if the backend is unreachable */
      });
    return () => {
      cancelled = true;
    };
  }, [messageCount, signedIn, tick]);

  // Something re-ran the assessment (a reading, a task, a check-in): re-read.
  useEffect(() => {
    window.addEventListener(RISK_UPDATED_EVENT, refresh);
    return () => window.removeEventListener(RISK_UPDATED_EVENT, refresh);
  }, [refresh]);

  const basis = data.basis ?? { chats: chats.length, diagnoses: 0 };

  return { ...data, basis, level: riskLevel(data.score), cards, refresh };
}
