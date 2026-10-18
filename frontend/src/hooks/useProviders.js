import { useEffect, useState } from "react";
import { getProviders } from "../api/client.js";
import { useSession } from "../context/SessionContext.jsx";

// Directory of doctors, clinics and hospitals the user can book with, nearest
// first: GET /api/providers (the backend's clinic directory, with open times).
// Entries with `slots` also show up as openings in the booking sheet.
// Backend field names -> the ones the Appointments page uses.
const fromBackend = (p) => ({ ...p, distanceKm: p.distance_km, acceptingNew: p.accepting_new, nextSlot: p.next_slot });

export default function useProviders(location) {
  const { signedIn } = useSession();
  const [providers, setProviders] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    setLoading(true);
    getProviders(location ?? {})
      .then((res) => {
        if (!cancelled && Array.isArray(res)) setProviders(res.map(fromBackend));
      })
      .catch(() => {
        /* keep the current list if the backend is unreachable */
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [location?.lat, location?.lng, signedIn]);

  return { providers, loading };
}
