import { createContext, useCallback, useContext, useEffect, useState } from "react";
import {
  USE_BACKEND,
  approveAppointment,
  bookAppointment,
  cancelAppointment,
  cancelBooking,
  decideCalendarSync,
  declineAppointment,
  getAppointments,
  getBookings,
} from "../api/client.js";
import { useSession } from "./SessionContext.jsx";
import { appointmentList } from "../data/placeholder.js";
import { describeSlot } from "../utils/slots.js";

// The user's appointments for the whole app. Every booking path (recommendation
// cards, provider cards, the booking sheet, the high-risk held visit) goes
// through bookVisit so the visit lands in "Upcoming".
//
// Backend off: bookings are kept in this browser; past visits are placeholders.
// Backend on (signed in), "Upcoming" combines:
//   - visits the user booked (GET/POST/DELETE /api/bookings), and
//   - appointments the backend's booking agent arranged after a follow-up
//     finding (GET /api/appointments) with their real status; cancel and
//     approve (approveVisit) go to the backend.
// "Past" (backend on) is the same data once its time has gone by: your bookings
// whose time has passed, and the agent's confirmed appointments whose chosen
// time has passed. Appointments still being arranged stay under Upcoming.
// The agent's appointments are re-read every minute and when the tab regains
// focus, so a request approved from the e-mail shows up without a reload.
const STORAGE_KEY = "caresync-appointments";
const REFRESH_MS = 60_000;

// Agent appointment statuses that are still upcoming, and how each reads in the app.
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

const whenOf = (slot, fallback) =>
  slot
    ? {
        date: new Date(slot).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }),
        time: describeSlot(slot).time,
      }
    : { date: fallback ?? "Time to be confirmed", time: "" };

// provider: a recommendation or a directory entry. slot: local ISO time, or null
// when the provider has no open times listed.
function toAppointment(provider, slot, note) {
  return {
    id: `${provider.id}@${slot ?? "next"}`,
    providerId: provider.id,
    name: provider.name,
    specialty: provider.specialty,
    place: provider.clinic ?? provider.name,
    address: provider.address,
    ...whenOf(slot, provider.nextSlot),
    length: provider.length ?? "30 min",
    status: "Requested",
    glyph: provider.glyph ?? initials(provider.name),
    note: note ?? provider.reason ?? "",
    slot,
  };
}

// A booking from /api/bookings -> the shape above.
function fromBooking(b) {
  const p = b.provider ?? {};
  return {
    id: b.id,
    booking: true,
    providerId: b.provider_id,
    name: p.name ?? "Clinic",
    specialty: p.specialty ?? "",
    place: p.clinic ?? p.name ?? "",
    address: p.address ?? "",
    ...whenOf(b.slot),
    length: "30 min",
    status: "Requested",
    glyph: p.glyph ?? initials(p.name ?? "?"),
    note: b.note ?? "",
    slot: b.slot,
  };
}

// An agent appointment from /api/appointments -> the shape above. Its time is the
// chosen slot, or the first of the times the agent proposed while it's arranged.
function fromAgent(a) {
  const clinic = a.clinic ?? {};
  const slot = a.chosen_slot ?? a.proposed_slots?.[0] ?? null;
  const start = slot?.start ?? null;
  const minutes = slot?.start && slot?.end ? Math.round((new Date(slot.end) - new Date(slot.start)) / 60000) : 30;
  const proposed = !a.chosen_slot && a.proposed_slots?.length > 1;
  return {
    id: a.id,
    remote: true,
    backendStatus: a.status,
    chosen: Boolean(a.chosen_slot), // false while the agent is still proposing times
    providerId: clinic.id ?? null,
    name: clinic.name ?? "Clinic to be confirmed",
    specialty: capitalize(clinic.specialty),
    place: clinic.name ?? "",
    address: clinic.address ?? "",
    ...whenOf(start),
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
    // The calendar step of a confirmed visit: pending until the user adds or skips it.
    calendarStatus: a.approval?.calendar?.status ?? (a.status === "confirmed" && a.chosen_slot ? "pending" : "none"),
    raw: a, // the backend record, for the appointment sheet (approval card, finding, calendar)
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

const bySlot = (a, b) => (a.slot ?? "￿").localeCompare(b.slot ?? "￿");

const AppointmentsContext = createContext(null);

export function AppointmentsProvider({ children }) {
  const { session, signedIn } = useSession();
  const account = signedIn ? session?.userId ?? session?.email : null;
  const [local, setLocal] = useState(() => (USE_BACKEND ? [] : loadSaved())); // backend off
  const [booked, setBooked] = useState([]); // backend on: /api/bookings
  const [agent, setAgent] = useState([]); // backend on: /api/appointments
  const [loaded, setLoaded] = useState(!USE_BACKEND);

  useEffect(() => {
    if (USE_BACKEND) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(local));
    } catch {
      /* storage unavailable */
    }
  }, [local]);

  // Backend on: load the signed-in account's bookings and agent appointments.
  useEffect(() => {
    if (!USE_BACKEND) return;
    setBooked([]);
    setAgent([]);
    if (!account) {
      setLoaded(true);
      return;
    }
    let cancelled = false;
    setLoaded(false);
    Promise.allSettled([getBookings(), getAppointments()])
      .then(([bookings, appointments]) => {
        if (cancelled) return;
        if (bookings.status === "fulfilled") setBooked(bookings.value.map(fromBooking));
        if (appointments.status === "fulfilled")
          setAgent(appointments.value.filter((a) => a.status in UPCOMING).map(fromAgent));
      })
      .finally(() => !cancelled && setLoaded(true));
    return () => {
      cancelled = true;
    };
  }, [account]);

  // Re-read the agent's appointments (their status changes on the server: clinic
  // replies, approvals from the e-mail buttons).
  const refresh = useCallback(() => {
    if (!USE_BACKEND || !account) return Promise.resolve();
    return getAppointments()
      .then((list) => setAgent(list.filter((a) => a.status in UPCOMING).map(fromAgent)))
      .catch(() => {
        /* offline: keep what we have */
      });
  }, [account]);

  useEffect(() => {
    if (!USE_BACKEND || !account) return;
    const timer = setInterval(refresh, REFRESH_MS);
    const onFocus = () => document.visibilityState === "visible" && refresh();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [account, refresh]);

  // Backend on: anything with a settled time that has passed moves to "Past", newest first.
  const now = Date.now();
  const hasPassed = (a) => a.slot && new Date(a.slot).getTime() < now && (a.booking || (a.backendStatus === "confirmed" && a.chosen));
  const all = USE_BACKEND ? [...agent, ...booked] : local;
  const upcoming = all.filter((a) => !(USE_BACKEND && hasPassed(a))).sort(bySlot);
  const past = USE_BACKEND
    ? all
        .filter(hasPassed)
        .map((a) => ({ ...a, status: "Past visit" }))
        .sort((a, b) => bySlot(b, a))
    : appointmentList.past;

  // Books with a provider the user picked. Throws if the backend refuses.
  const bookVisit = async (provider, slot = null, note) => {
    if (USE_BACKEND) {
      const { name, specialty, clinic, address, phone, glyph, distance } = provider;
      const res = await bookAppointment({
        providerId: provider.id,
        slot,
        note: note ?? provider.reason,
        provider: { name, specialty, clinic, address, phone, glyph, distance },
      });
      const appt = fromBooking(res);
      setBooked((list) => (list.some((a) => a.id === appt.id) ? list : [...list, appt]));
      return appt;
    }
    const appt = toAppointment(provider, slot, note);
    setLocal((list) => (list.some((a) => a.id === appt.id) ? list : [...list, appt]));
    return appt;
  };

  // Throws if the backend refuses, so the confirm dialog can show the error.
  const cancelVisit = async (appointmentId) => {
    if (agent.some((a) => a.id === appointmentId)) {
      await cancelAppointment(appointmentId);
      setAgent((list) => list.filter((a) => a.id !== appointmentId));
    } else if (booked.some((a) => a.id === appointmentId)) {
      await cancelBooking(appointmentId);
      setBooked((list) => list.filter((a) => a.id !== appointmentId));
    } else {
      setLocal((list) => list.filter((a) => a.id !== appointmentId));
    }
  };

  // Replace one agent appointment with the backend's updated record (or drop it
  // when it is no longer upcoming). Returns the mapped appointment.
  const applyUpdate = (appointmentId, updated) => {
    const mapped = fromAgent(updated);
    setAgent((list) =>
      UPCOMING[updated.status]
        ? list.map((a) => (a.id === appointmentId ? mapped : a))
        : list.filter((a) => a.id !== appointmentId)
    );
    return mapped;
  };

  // Approve an appointment the backend's agent is waiting on (status "Needs your approval").
  const approveVisit = async (appointmentId) => applyUpdate(appointmentId, await approveAppointment(appointmentId));

  // "Not now" on the approval card: nothing is sent, the request is closed.
  const declineVisit = async (appointmentId) => applyUpdate(appointmentId, await declineAppointment(appointmentId));

  // The calendar step of a confirmed visit: "add" writes it to Google Calendar
  // (backend), "skip" stops the prompt. Returns the sync status.
  const calendarDecision = async (appointmentId, decision) => {
    const sync = await decideCalendarSync(appointmentId, decision);
    setAgent((list) =>
      list.map((a) =>
        a.id === appointmentId
          ? {
              ...a,
              calendarStatus: sync.status,
              raw: { ...a.raw, calendar_event_id: sync.event_id ?? a.raw.calendar_event_id, approval: { ...a.raw.approval, calendar: sync } },
            }
          : a
      )
    );
    return sync;
  };

  const isBooked = (providerId) => upcoming.some((a) => a.providerId === providerId);
  const agentById = (id) => agent.find((a) => a.id === id) ?? null;

  return (
    <AppointmentsContext.Provider
      value={{ upcoming, past, loaded, bookVisit, cancelVisit, approveVisit, declineVisit, calendarDecision, refresh, agentById, isBooked }}
    >
      {children}
    </AppointmentsContext.Provider>
  );
}

export function useAppointments() {
  return useContext(AppointmentsContext);
}
