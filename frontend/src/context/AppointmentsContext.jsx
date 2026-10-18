import { createContext, useContext, useEffect, useState } from "react";
import { USE_BACKEND, approveAppointment, cancelAppointment, getAppointments } from "../api/client.js";
import { useSession } from "./SessionContext.jsx";
import { appointmentList } from "../data/placeholder.js";
import { describeSlot } from "../utils/slots.js";

// The user's appointments for the whole app. Every booking path (recommendation
// cards, provider cards, the booking sheet, the high-risk held visit) goes
// through bookVisit so the visit lands in "Upcoming".
//
// Backend off: bookings are kept in this browser; past visits are placeholders.
// Backend on: "Upcoming" also lists the appointments the backend's booking agent
// arranged after a follow-up finding (GET /api/appointments), with their real
// status; cancelling one cancels it on the backend, and approveVisit approves
// one that's waiting. The backend has no endpoint for picking a provider and
// time yourself, so those bookings still stay in this browser (kept per
// account). Past visits have no backend source, so that list starts empty.
const STORAGE_KEY = "caresync-appointments";

// Backend statuses that are still upcoming, and how each reads in the app.
const UPCOMING = {
  proposed: "Proposed",
  awaiting_approval: "Needs your approval",
  processing: "Requested",
  requested: "Requested",
  needs_user_info: "Needs your info",
  confirmed: "Confirmed",
};

const initials = (name) =>
  name
    .replace(/^Dr\.?\s+/i, "")
    .replace(/\(.*?\)/g, "")
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

const capitalize = (s) => (s ? s[0].toUpperCase() + s.slice(1).replace(/_/g, " ") : "");

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

// A backend appointment -> the shape above. Its time is the chosen slot, or the
// first of the times the agent proposed while it's still being arranged.
function fromBackend(a) {
  const clinic = a.clinic ?? {};
  const slot = a.chosen_slot ?? a.proposed_slots?.[0] ?? null;
  const start = slot?.start ?? null;
  const minutes = slot?.start && slot?.end ? Math.round((new Date(slot.end) - new Date(slot.start)) / 60000) : 30;
  const proposed = !a.chosen_slot && a.proposed_slots?.length > 1;
  return {
    id: a.id,
    remote: true,
    backendStatus: a.status,
    providerId: clinic.id ?? null,
    name: clinic.name ?? "Clinic to be confirmed",
    specialty: capitalize(clinic.specialty),
    place: clinic.name ?? "",
    address: clinic.address ?? "",
    date: start
      ? new Date(start).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
      : "Time to be confirmed",
    time: start ? describeSlot(start).time : "",
    length: `${minutes} min`,
    status: UPCOMING[a.status] ?? capitalize(a.status),
    glyph: initials(clinic.name ?? "?"),
    note:
      a.status === "awaiting_approval"
        ? "CareSync found this clinic after a follow-up finding. Nothing is sent until you approve."
        : proposed
          ? `${a.proposed_slots.length} times proposed to the clinic.`
          : "",
    slot: start,
  };
}

function loadSaved(key) {
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    return Array.isArray(saved) ? saved : USE_BACKEND ? [] : appointmentList.upcoming;
  } catch {
    return USE_BACKEND ? [] : appointmentList.upcoming;
  }
}

const bySlot = (a, b) => (a.slot ?? "￿").localeCompare(b.slot ?? "￿");

const AppointmentsContext = createContext(null);

export function AppointmentsProvider({ children }) {
  const { session, signedIn } = useSession();
  // Backend on: this browser's own bookings are kept per account.
  const account = USE_BACKEND ? (signedIn ? session?.userId ?? session?.email : null) : "local";
  const key = USE_BACKEND ? `${STORAGE_KEY}:${account}` : STORAGE_KEY;
  // This browser's bookings, tagged with the storage key (account) they belong to.
  const [store, setStore] = useState(() => ({ key, list: loadSaved(key) }));
  if (store.key !== key) setStore({ key, list: loadSaved(key) }); // another account signed in
  const local = store.key === key ? store.list : [];
  const setLocal = (update) => setStore((s) => ({ ...s, list: typeof update === "function" ? update(s.list) : update }));
  const [remote, setRemote] = useState([]);
  const [loaded, setLoaded] = useState(!USE_BACKEND);
  const past = USE_BACKEND ? [] : appointmentList.past;

  useEffect(() => {
    if (!USE_BACKEND) return;
    setRemote([]);
    if (!account) {
      setLoaded(true);
      return;
    }
    let cancelled = false;
    setLoaded(false);
    getAppointments()
      .then((list) => !cancelled && setRemote(list.filter((a) => a.status in UPCOMING).map(fromBackend)))
      .catch(() => {
        /* keep what we have if the backend is unreachable */
      })
      .finally(() => !cancelled && setLoaded(true));
    return () => {
      cancelled = true;
    };
  }, [account]);

  // Saved under the key the list belongs to, so one account never overwrites another.
  useEffect(() => {
    if (USE_BACKEND && store.key.endsWith(":null")) return; // signed out: nothing to keep
    try {
      localStorage.setItem(store.key, JSON.stringify(store.list));
    } catch {
      /* storage unavailable */
    }
  }, [store]);

  const upcoming = [...remote, ...local].sort(bySlot);

  // Books with a provider the user picked. Kept in this browser in both modes.
  const bookVisit = async (provider, slot = null, note) => {
    const appt = toAppointment(provider, slot, note);
    setLocal((list) => (list.some((a) => a.id === appt.id) ? list : [...list, appt].sort(bySlot)));
    return appt;
  };

  // Throws if the backend refuses, so the confirm dialog can show the error.
  const cancelVisit = async (appointmentId) => {
    if (remote.some((a) => a.id === appointmentId)) {
      await cancelAppointment(appointmentId);
      setRemote((list) => list.filter((a) => a.id !== appointmentId));
      return;
    }
    setLocal((list) => list.filter((a) => a.id !== appointmentId));
  };

  // Approve an appointment the backend's agent is waiting on (status "Needs your approval").
  const approveVisit = async (appointmentId) => {
    const updated = await approveAppointment(appointmentId);
    setRemote((list) =>
      UPCOMING[updated.status]
        ? list.map((a) => (a.id === appointmentId ? fromBackend(updated) : a))
        : list.filter((a) => a.id !== appointmentId)
    );
  };

  const isBooked = (providerId) => upcoming.some((a) => a.providerId === providerId);

  return (
    <AppointmentsContext.Provider value={{ upcoming, past, loaded, bookVisit, cancelVisit, approveVisit, isBooked }}>
      {children}
    </AppointmentsContext.Provider>
  );
}

export function useAppointments() {
  return useContext(AppointmentsContext);
}
