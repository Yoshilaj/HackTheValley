import { createContext, useContext, useEffect, useState } from "react";
import { patient as basePatient } from "../data/placeholder.js";
import { USE_BACKEND, getProfile, saveProfile } from "../api/client.js";
import { useSession } from "./SessionContext.jsx";
import useStoredState from "../hooks/useStoredState.js";

// The signed-in user's health profile, editable from the profile sheet:
// { fullName, dateOfBirth (YYYY-MM-DD), healthCardLast4, allergies: [],
//   conditions: [], emergencyContact: { name, relation, phone } | null,
//   sex, phone, email, street, city, province, postalCode }.
// The first name and initials are worked out from the full name, so a rename
// shows in the greeting and avatar too. Name, date of birth, sex and the
// contact and address fields are what a clinic receives when booking.
//
// Backend off: everything is kept in this browser, starting from the demo patient.
// Backend on: everything but the health card comes from and saves to
// /api/profile (email: the contact e-mail, else the account's). Saving fires
// PROFILE_SAVED_EVENT so pending booking requests reload with the new
// details. The backend deliberately has no
// health-card field (the card is brought to the visit), so that stays in this
// browser and is merged in. Allergy/condition lists saved in this browser
// before the backend had them are shown until the first save moves them over.
const ProfileContext = createContext(null);

export const PROFILE_SAVED_EVENT = "caresync:profile-saved";

// Booking details: what a clinic asks for, all plain strings ("" = not given).
const CONTACT_FIELDS = ["sex", "phone", "email", "street", "city", "province", "postalCode"];
const NO_CONTACT = Object.fromEntries(CONTACT_FIELDS.map((k) => [k, ""]));

const EMPTY = {
  fullName: "",
  dateOfBirth: "",
  healthCardLast4: "",
  allergies: [],
  conditions: [],
  emergencyContact: null,
  ...NO_CONTACT,
};
const LOCAL_ONLY = ({ healthCardLast4, allergies, conditions }) => ({ healthCardLast4, allergies, conditions });
const list = (v) => (Array.isArray(v) ? v : undefined); // undefined: the backend has none saved yet

const editable = (p) => ({
  fullName: p.fullName,
  dateOfBirth: p.dateOfBirth,
  healthCardLast4: p.healthCardLast4,
  allergies: p.allergies,
  conditions: p.conditions,
  emergencyContact: p.emergencyContact ?? null,
  ...Object.fromEntries(CONTACT_FIELDS.map((k) => [k, p[k] ?? ""])),
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
    sex: p.sex ?? "",
    phone: p.phone ?? "",
    email: p.contact_email || email || "",
    street: p.street ?? "",
    city: p.city ?? "",
    province: p.province ?? "",
    postalCode: p.postal_code ?? "",
    // Left out when the backend has never stored them, so the browser's lists show instead.
    ...(list(p.allergies) && { allergies: p.allergies }),
    ...(list(p.conditions) && { conditions: p.conditions }),
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
    allergies: p.allergies,
    conditions: p.conditions,
    sex: p.sex || null,
    phone: p.phone || null,
    contact_email: p.email || null,
    street: p.street || null,
    city: p.city || null,
    province: p.province || null,
    postal_code: p.postalCode || null,
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
        ...(remote
          ? fromBackend(remote, session?.email)
          : { fullName: (session?.email ?? "").split("@")[0], email: session?.email ?? "" }),
      })
    : withDerived({ ...EMPTY, ...(stored ?? editable(basePatient)) });

  // Throws if the backend refuses (e.g. a phone number it can't read), so the
  // sheet can show the error and keep the draft.
  const updateProfile = async (next) => {
    const clean = editable(next);
    if (USE_BACKEND) {
      const saved = await saveProfile(toBackend(clean, remote?.emergency_contact));
      setRemote(saved);
      setStored(LOCAL_ONLY(clean));
      window.dispatchEvent(new Event(PROFILE_SAVED_EVENT));
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
