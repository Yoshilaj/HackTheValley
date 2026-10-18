import { useCallback, useEffect, useState } from "react";
import { USE_BACKEND, getRecommendations } from "../api/client.js";
import { useSession } from "../context/SessionContext.jsx";
import { recommendations as placeholder } from "../data/placeholder.js";

// Clinics recommended for the user. Backend off: the placeholder list. Backend
// on (signed in): GET /api/recommendations, the nearest clinic for each
// follow-up finding in the latest risk assessment, with the reason; re-asked
// when the user shares their location.
// Backend field names -> the ones the Appointments page uses.
const fromBackend = (p) => ({ ...p, distanceKm: p.distance_km, acceptingNew: p.accepting_new, nextSlot: p.next_slot });

const LOCATION_ERRORS = {
  1: "Location access was blocked. You can allow it in your browser's site settings.",
  2: "Couldn't find your location. Try again in a moment.",
  3: "Finding your location took too long. Try again.",
};

export default function useRecommendations() {
  const { signedIn } = useSession();
  const [items, setItems] = useState(USE_BACKEND ? [] : placeholder);
  const [location, setLocation] = useState(null); // { lat, lng }
  const [locating, setLocating] = useState(false);
  const [locError, setLocError] = useState("");

  // Only asks for location when the user clicks "Use my location".
  const locate = useCallback(() => {
    if (!navigator.geolocation) {
      setLocError("Your browser can't share location.");
      return;
    }
    setLocating(true);
    setLocError("");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocation({ lat: pos.coords.latitude, lng: pos.coords.longitude });
        setLocating(false);
      },
      (err) => {
        setLocError(LOCATION_ERRORS[err.code] || "Couldn't get your location.");
        setLocating(false);
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 5 * 60 * 1000 }
    );
  }, []);

  useEffect(() => {
    if (!USE_BACKEND || !signedIn) return;
    let cancelled = false;
    getRecommendations(location ?? {})
      .then((res) => {
        if (!cancelled && Array.isArray(res)) setItems(res.map(fromBackend));
      })
      .catch(() => {
        /* keep the current list if the backend is unreachable */
      });
    return () => {
      cancelled = true;
    };
  }, [location, signedIn]);

  return { items, location, locate, locating, locError, useBackend: USE_BACKEND };
}
