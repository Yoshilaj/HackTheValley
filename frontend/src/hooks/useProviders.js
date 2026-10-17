import { useEffect, useState } from "react";
import { getProviders } from "../api/client.js";
import { providers as placeholder } from "../data/placeholder.js";

// Directory of doctors, clinics and hospitals the backend adds for the user to
// book with. Empty until GET /api/providers exists; flip USE_BACKEND then.
// Entries with `slots` also show up as openings in the booking sheet.
const USE_BACKEND = false;

export default function useProviders(location) {
  const [providers, setProviders] = useState(placeholder);
  const [loading, setLoading] = useState(USE_BACKEND);

  useEffect(() => {
    if (!USE_BACKEND) return;
    let cancelled = false;
    setLoading(true);
    getProviders(location ?? {})
      .then((res) => {
        if (!cancelled && Array.isArray(res)) setProviders(res);
      })
      .catch(() => {
        /* keep the current list if the backend is unreachable */
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [location?.lat, location?.lng]);

  return { providers, loading };
}
