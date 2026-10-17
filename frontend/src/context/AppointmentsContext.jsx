import { createContext, useContext, useEffect, useState } from "react";
import { bookAppointment, cancelAppointment, getAppointments } from "../api/client.js";
import { appointmentList } from "../data/placeholder.js";
import { describeSlot } from "../utils/slots.js";

// The user's appointments for the whole app. Every booking path (recommendation
// cards, provider cards, the booking sheet, the high-risk held visit) goes
// through bookVisit so the visit lands in "Upcoming". Until the backend's
// GET /api/appointments exists, bookings are kept in localStorage.
// Flip USE_BACKEND once the endpoints are live.
const USE_BACKEND = false;
const STORAGE_KEY = "caresync-appointments";

const initials = (name) =>
  name
    .replace(/^Dr\.?\s+/i, "")
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

// provider: a recommendation or a directory entry (see `providers` in placeholder.js).
// slot: local ISO time, or null when the provider has no open times listed.
function toAppointment(provider, slot, note) {
  const when = slot
    ? {
        date: new Date(slot).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }),
        time: describeSlot(slot).time,
      }
    : { date: provider.nextSlot ?? "Time to be confirmed", time: "" };
  return {
    id: `${provider.id}@${slot ?? "next"}`,
    providerId: provider.id,
    name: provider.name,
    specialty: provider.specialty,
    place: provider.clinic ?? provider.name,
    address: provider.address,
    ...when,
    length: provider.length ?? "30 min",
    status: "Requested",
    glyph: provider.glyph ?? initials(provider.name),
    note: note ?? provider.reason ?? "",
    slot,
  };
}

function loadSaved() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    return Array.isArray(saved) ? saved : appointmentList.upcoming;
  } catch {
    return appointmentList.upcoming;
  }
}

const AppointmentsContext = createContext(null);

export function AppointmentsProvider({ children }) {
  const [upcoming, setUpcoming] = useState(() => (USE_BACKEND ? appointmentList.upcoming : loadSaved()));
  const [past, setPast] = useState(appointmentList.past);
  // False until the backend list has arrived, so nothing reacts to the placeholder list.
  const [loaded, setLoaded] = useState(!USE_BACKEND);

  useEffect(() => {
    if (!USE_BACKEND) return;
    let cancelled = false;
    getAppointments()
      .then((res) => {
        if (cancelled) return;
        if (Array.isArray(res.upcoming)) setUpcoming(res.upcoming);
        if (Array.isArray(res.past)) setPast(res.past);
      })
      .catch(() => {
        /* keep what we have if the backend is unreachable */
      })
      .finally(() => !cancelled && setLoaded(true));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (USE_BACKEND) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(upcoming));
    } catch {
      /* storage unavailable */
    }
  }, [upcoming]);

  // Throws if the backend rejects the booking, so callers can show the error.
  const bookVisit = async (provider, slot = null, note) => {
    let appt = toAppointment(provider, slot, note);
    if (USE_BACKEND) {
      const res = await bookAppointment(provider.id, slot ?? undefined);
      if (res?.appointment) appt = res.appointment;
    }
    setUpcoming((list) =>
      list.some((a) => a.id === appt.id)
        ? list
        : [...list, appt].sort((a, b) => (a.slot ?? "￿").localeCompare(b.slot ?? "￿"))
    );
    return appt;
  };

  // Throws if the backend refuses, so the confirm dialog can show the error.
  const cancelVisit = async (appointmentId) => {
    if (USE_BACKEND) await cancelAppointment(appointmentId);
    setUpcoming((list) => list.filter((a) => a.id !== appointmentId));
  };

  const isBooked = (providerId) => upcoming.some((a) => a.providerId === providerId);

  return (
    <AppointmentsContext.Provider value={{ upcoming, past, loaded, bookVisit, cancelVisit, isBooked }}>
      {children}
    </AppointmentsContext.Provider>
  );
}

export function useAppointments() {
  return useContext(AppointmentsContext);
}
