import { useCallback, useEffect, useState } from "react";
import { getRecommendations } from "../api/client.js";
import { recommendations as placeholder } from "../data/placeholder.js";

// Specialists/doctors/clinics recommended from the user's chat history. The
// backend will search near the user's location; until GET /api/recommendations
// exists this returns the placeholder list. Flip USE_BACKEND once it's live.
const USE_BACKEND = false;

const LOCATION_ERRORS = {
  1: "Location access was blocked. You can allow it in your browser's site settings.",
  2: "Couldn't find your location. Try again in a moment.",
  3: "Finding your location took too long. Try again.",
};

export default function useRecommendations() {
  const [items, setItems] = useState(placeholder);
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
    if (!USE_BACKEND) return;
    let cancelled = false;
    getRecommendations(location ?? {})
      .then((res) => {
        if (!cancelled && Array.isArray(res)) setItems(res);
      })
      .catch(() => {
        /* keep the current list if the backend is unreachable */
      });
    return () => {
      cancelled = true;
    };
  }, [location]);

  return { items, location, locate, locating, locError, useBackend: USE_BACKEND };
}
