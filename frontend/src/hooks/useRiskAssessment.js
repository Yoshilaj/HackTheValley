import { useEffect, useState } from "react";
import { getRiskAssessment } from "../api/client.js";
import { useChatHistory } from "../context/ChatHistoryContext.jsx";
import { riskAssessment as placeholder } from "../data/placeholder.js";

// Risk score (0-100, higher = more risk). The backend will work it out only
// from the user's chat history and doctors' diagnoses, checked against
// reliable medical sources. Until GET /api/risk-assessment exists this returns
// the placeholder. Flip USE_BACKEND to true once the endpoint is live.
const USE_BACKEND = false;

const clamp = (n) => Math.min(100, Math.max(0, Math.round(n)));

// Level for the overall 0-100 index; matches the card colours (black from 90).
export function riskLevel(score) {
  if (score < 34) return "Low";
  if (score < 67) return "Moderate";
  if (score < 90) return "High";
  return "Very high";
}

export default function useRiskAssessment() {
  const { chats } = useChatHistory();
  const [data, setData] = useState({
    score: clamp(placeholder.score),
    previous: placeholder.previous,
    updatedAt: placeholder.updatedAt,
    basis: null, // placeholder: counted live below
    note: placeholder.note,
    suspected: placeholder.suspected,
    conditions: placeholder.conditions,
  });

  // Re-assess whenever the chat history grows.
  const messageCount = chats.reduce((sum, c) => sum + c.messages.length, 0);

  useEffect(() => {
    if (!USE_BACKEND) return;
    let cancelled = false;
    getRiskAssessment()
      .then((res) => {
        if (!cancelled && Number.isFinite(res.score))
          setData({
            score: clamp(res.score),
            previous: res.previous ?? null,
            updatedAt: res.updatedAt ?? Date.now(),
            basis: res.basis ?? null,
            note: res.note ?? "",
            suspected: res.suspected ?? null,
            conditions: Array.isArray(res.conditions) ? res.conditions : [],
          });
      })
      .catch(() => {
        /* keep the last assessment if the backend is unreachable */
      });
    return () => {
      cancelled = true;
    };
  }, [messageCount]);

  // What the assessment rests on; until the backend reports it, count the chats here.
  const basis = data.basis ?? { chats: chats.length, diagnoses: placeholder.basis.diagnoses };

  return { ...data, basis, level: riskLevel(data.score) };
}
