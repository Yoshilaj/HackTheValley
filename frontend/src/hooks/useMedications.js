import { useEffect, useState } from "react";
import { USE_BACKEND, getMedications } from "../api/client.js";
import { useSession } from "../context/SessionContext.jsx";
import { medicationList } from "../data/placeholder.js";

// The user's medications, in the shape the Medications page uses:
// { id, name, dose, frequency, purpose, prescriber, refills, times, glyph,
//   daysLeft, takeWith, sideEffects, interactions }.
// Backend off: the placeholder list. Backend on: GET /api/medications (the
// profile's list, which the risk assessment and chat also read).

function fromBackend(m) {
  return {
    id: m.id,
    name: m.name,
    dose: m.dose ?? "",
    frequency: m.frequency ?? "",
    purpose: m.purpose ?? "",
    prescriber: m.prescriber ?? "",
    refills: m.refills ?? 0,
    times: m.times ?? [],
    glyph: m.glyph || (m.name ?? "?")[0].toUpperCase(),
    daysLeft: m.days_left ?? null,
    takeWith: m.take_with ?? "",
    sideEffects: m.side_effects ?? [],
    interactions: m.interactions ?? [],
  };
}

export default function useMedications() {
  const { signedIn } = useSession();
  const [medications, setMedications] = useState(USE_BACKEND ? [] : medicationList);
  const [loading, setLoading] = useState(USE_BACKEND);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!USE_BACKEND || !signedIn) return;
    let cancelled = false;
    setLoading(true);
    getMedications()
      .then((list) => {
        if (cancelled) return;
        setMedications(list.map(fromBackend));
        setError(false);
      })
      .catch(() => !cancelled && setError(true))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [signedIn]);

  return { medications, loading, error };
}
