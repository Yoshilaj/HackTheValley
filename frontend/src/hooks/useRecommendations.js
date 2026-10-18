import { useCallback, useEffect, useState } from "react";
import { USE_BACKEND, getRecommendations } from "../api/client.js";
import { useSession } from "../context/SessionContext.jsx";
import { recommendations as placeholder } from "../data/placeholder.js";

// Clinics recommended for the user. Backend off: the placeholder list. Backend
// on (signed in): GET /api/recommendations, the nearest real clinic (Google
// Places) for each follow-up finding in the latest risk assessment, with the
// reason; measured from the home address, and re-asked when the user shares
// their location.
// Backend field names -> the ones the Appointments page uses.
const fromBackend = (p) => ({ ...p, distanceKm: p.distance_km, acceptingNew: p.accepting_new, nextSlot: p.next_slot });

// What the km figures are measured from, for the line under a clinic list.
// distanceFrom: the backend's distance_from ("device" | "home" | null);
// location: the shared position, with its accuracy in metres.
export function distanceNote(distanceFrom, location) {
  if (distanceFrom === "device") {
    const km = location?.accuracy > 1000 ? Math.round(location.accuracy / 1000) : 0;
    return km
      ? `Straight-line distance from your current location (your browser placed you within about ${km} km)`
      : "Straight-line distance from your current location";
  }
  if (distanceFrom === "home") return "Straight-line distance from your home address";
  return "Add your address in your profile, or use your location, to see clinics near you";
}

const LOCATION_ERRORS = {
  1: "Location access was blocked. You can allow it in your browser's site settings.",
  2: "Couldn't find your location. Try again in a moment.",
  3: "Finding your location took too long. Try again.",
};

export default function useRecommendations() {
  const { signedIn } = useSession();
  const [items, setItems] = useState(USE_BACKEND ? [] : placeholder);
  const [location, setLocation] = useState(null); // { lat, lng, accuracy (metres) }
  const [locating, setLocating] = useState(false);
  const [locError, setLocError] = useState("");

  // Only asks for location when the user clicks "Use my location". A fresh,
  // high-accuracy fix (GPS / Wi-Fi where the device has it), so distances are
  // measured from where the user actually is.
  const locate = useCallback(() => {
    if (!navigator.geolocation) {
      setLocError("Your browser can't share location.");
      return;
    }
    setLocating(true);
    setLocError("");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocation({ lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy: pos.coords.accuracy });
        setLocating(false);
      },
      (err) => {
        setLocError(LOCATION_ERRORS[err.code] || "Couldn't get your location.");
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
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

  // Placeholder data (backend off) has no origin of its own.
  const distanceFrom = USE_BACKEND
    ? (items.find((r) => r.distance_from)?.distance_from ?? null)
    : location
      ? "device"
      : "home";
  return { items, location, locate, locating, locError, distanceFrom, useBackend: USE_BACKEND };
}
