import { createContext, useContext } from "react";
import { patient as basePatient } from "../data/placeholder.js";
import { saveProfile } from "../api/client.js";
import useStoredState from "../hooks/useStoredState.js";

// The signed-in user's health profile, editable from the profile sheet:
// { fullName, dateOfBirth (YYYY-MM-DD), healthCardLast4, allergies: [],
//   conditions: [], emergencyContact: { name, relation, phone } | null }.
// Edits are kept in this browser until the backend's profile endpoint is
// wired up (flip USE_BACKEND). The first name and initials are worked out
// from the full name, so a rename shows in the greeting and avatar too.
const USE_BACKEND = false;

const ProfileContext = createContext(null);

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

export function ProfileProvider({ children }) {
  const [stored, setStored] = useStoredState("caresync-profile", null);
  const profile = withDerived(stored ?? editable(basePatient));

  // Throws if the backend refuses, so the sheet can show the error and keep the draft.
  const updateProfile = async (next) => {
    const clean = editable(next);
    if (USE_BACKEND) await saveProfile(clean);
    setStored(clean);
  };

  return <ProfileContext.Provider value={{ profile, updateProfile }}>{children}</ProfileContext.Provider>;
}

export function useProfile() {
  return useContext(ProfileContext);
}
