import { createContext, useContext, useEffect, useState } from "react";
import { patient as basePatient } from "../data/placeholder.js";
import { USE_BACKEND, getProfile, saveProfile } from "../api/client.js";
import { useSession } from "./SessionContext.jsx";
import useStoredState from "../hooks/useStoredState.js";

// The signed-in user's health profile, editable from the profile sheet:
// { fullName, dateOfBirth (YYYY-MM-DD), healthCardLast4, allergies: [],
//   conditions: [], emergencyContact: { name, relation, phone } | null }.
// The first name and initials are worked out from the full name, so a rename
// shows in the greeting and avatar too.
//
// Backend off: everything is kept in this browser, starting from the demo patient.
// Backend on: name, date of birth and emergency contact come from and save to
// /api/profile. The backend has no allergy or condition lists and deliberately
// no health-card field (the card is brought to the visit), so those three stay
// in this browser and are merged in.
const ProfileContext = createContext(null);

const EMPTY = { fullName: "", dateOfBirth: "", healthCardLast4: "", allergies: [], conditions: [], emergencyContact: null };
const LOCAL_ONLY = ({ healthCardLast4, allergies, conditions }) => ({ healthCardLast4, allergies, conditions });

const editable = ({ fullName, dateOfBirth, healthCardLast4, allergies, conditions, emergencyContact }) => ({
  fullName,
  dateOfBirth,
  healthCardLast4,
  allergies,
  conditions,
  emergencyContact: emergencyContact ?? null,
});

function withDerived(p) {
  const words = p.fullName.trim().split(/\s+/).filter(Boolean);
  const first = words[0] ?? "";
  const last = words.length > 1 ? words[words.length - 1] : "";
  return { ...p, name: first, initials: `${first[0] ?? ""}${last[0] ?? ""}`.toUpperCase() };
}

// Backend profile -> the fields above it can supply.
function fromBackend(p, email) {
  const fullName = [p.first_name, p.last_name].filter(Boolean).join(" ") || p.display_name || (email ?? "").split("@")[0];
  const c = p.emergency_contact;
  return {
    fullName,
    dateOfBirth: p.date_of_birth ?? "",
    emergencyContact: c?.name ? { name: c.name, relation: c.relation ?? "", phone: c.phone ?? "" } : null,
  };
}

// The profile sheet's fields -> a PUT /api/profile body (only these fields change).
function toBackend(p, previousContact) {
  const [first, ...rest] = p.fullName.split(" ");
  return {
    first_name: first || null,
    last_name: rest.join(" ") || null,
    display_name: first || null,
    date_of_birth: p.dateOfBirth || null,
    // Keep any contact fields the sheet doesn't edit (e.g. the contact's email).
    emergency_contact: p.emergencyContact ? { ...previousContact, ...p.emergencyContact } : null,
  };
}

export function ProfileProvider({ children }) {
  const { session, signedIn } = useSession();
  const [stored, setStored] = useStoredState("caresync-profile", null);
  const [remote, setRemote] = useState(null); // backend profile, as returned
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    if (!USE_BACKEND || !signedIn) {
      setRemote(null);
      return;
    }
    let cancelled = false;
    setLoadError(false);
    getProfile()
      .then((p) => !cancelled && setRemote(p))
      .catch(() => !cancelled && setLoadError(true));
    return () => {
      cancelled = true;
    };
  }, [signedIn, session?.userId]);

  const profile = USE_BACKEND
    ? withDerived({
        ...EMPTY,
        ...(stored ? LOCAL_ONLY({ ...EMPTY, ...stored }) : {}),
        ...(remote ? fromBackend(remote, session?.email) : { fullName: (session?.email ?? "").split("@")[0] }),
      })
    : withDerived(stored ?? editable(basePatient));

  // Throws if the backend refuses (e.g. a phone number it can't read), so the
  // sheet can show the error and keep the draft.
  const updateProfile = async (next) => {
    const clean = editable(next);
    if (USE_BACKEND) {
      const saved = await saveProfile(toBackend(clean, remote?.emergency_contact));
      setRemote(saved);
      setStored(LOCAL_ONLY(clean));
      return;
    }
    setStored(clean);
  };

  return (
    <ProfileContext.Provider value={{ profile, updateProfile, loading: USE_BACKEND && signedIn && !remote && !loadError }}>
      {children}
    </ProfileContext.Provider>
  );
}

export function useProfile() {
  return useContext(ProfileContext);
}
