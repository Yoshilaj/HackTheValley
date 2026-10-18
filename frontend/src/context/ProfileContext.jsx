import { createContext, useContext, useEffect, useState } from "react";
import { completeOnboarding, getProfile, saveProfile } from "../api/client.js";
import { useSession } from "./SessionContext.jsx";
import useStoredState from "../hooks/useStoredState.js";

// The signed-in user's health profile, editable from the profile sheet:
// { fullName, dateOfBirth (YYYY-MM-DD), healthCardLast4, allergies: [],
//   conditions: [], emergencyContact: { name, relation, phone } | null,
//   risk: { sex, smokingStatus, diabetes, knownHypertension, knownAf, knownChd,
//           knownPd, knownDementia, familyPrematureCvd, totalChol, hdl, ldl, egfr },
//   phone, email, street, city, province, postalCode }.
// `risk` holds what the heart-risk calculator (Framingham) and the rule engine
// read from the profile; booleans are true | false | null (not sure), labs are
// mg/dL numbers or null.
// The first name and initials are worked out from the full name, so a rename
// shows in the greeting and avatar too. Name, date of birth, sex (risk.sex) and
// the contact and address fields are what a clinic receives when booking.
//
// Everything but the health card comes from and saves to
// /api/profile (email: the contact e-mail, else the account's). Saving fires
// PROFILE_SAVED_EVENT so pending booking requests reload with the new
// details. The backend deliberately has no
// health-card field (the card is brought to the visit), so that stays in this
// browser and is merged in. Allergy/condition lists saved in this browser
// before the backend had them are shown until the first save moves them over.
const ProfileContext = createContext(null);

export const PROFILE_SAVED_EVENT = "caresync:profile-saved";

// Booking details: what a clinic asks for, all plain strings ("" = not given).
// Sex is in `risk` (the risk calculator reads it too).
const CONTACT_FIELDS = ["phone", "email", "street", "city", "province", "postalCode"];
const NO_CONTACT = Object.fromEntries(CONTACT_FIELDS.map((k) => [k, ""]));

export const EMPTY_RISK = {
  sex: null, // "male" | "female" | "other" | "prefer_not" | null
  smokingStatus: null, // "never" | "former" | "current" | null
  diabetes: null,
  knownHypertension: null,
  knownAf: null,
  knownChd: null,
  knownPd: null,
  knownDementia: null,
  familyPrematureCvd: null,
  totalChol: null, // mg/dL
  hdl: null,
  ldl: null,
  egfr: null, // mL/min/1.73m²
};
const EMPTY = {
  fullName: "",
  dateOfBirth: "",
  healthCardLast4: "",
  allergies: [],
  conditions: [],
  emergencyContact: null,
  risk: EMPTY_RISK,
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
  risk: { ...EMPTY_RISK, ...(p.risk ?? {}) },
  ...Object.fromEntries(CONTACT_FIELDS.map((k) => [k, p[k] ?? ""])),
});

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const bool = (v) => (typeof v === "boolean" ? v : null);

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
    phone: p.phone ?? "",
    email: p.contact_email || email || "",
    street: p.street ?? "",
    city: p.city ?? "",
    province: p.province ?? "",
    postalCode: p.postal_code ?? "",
    // Left out when the backend has never stored them, so the browser's lists show instead.
    ...(list(p.allergies) && { allergies: p.allergies }),
    ...(list(p.conditions) && { conditions: p.conditions }),
    risk: {
      sex: p.sex ?? null,
      smokingStatus: p.smoking_status ?? null,
      diabetes: bool(p.diabetes),
      knownHypertension: bool(p.known_hypertension),
      knownAf: bool(p.known_af),
      knownChd: bool(p.known_chd),
      knownPd: bool(p.known_pd),
      knownDementia: bool(p.known_dementia),
      familyPrematureCvd: bool(p.family_history?.premature_cvd),
      totalChol: num(p.total_chol_mg_dl),
      hdl: num(p.hdl_mg_dl),
      ldl: num(p.ldl_mg_dl),
      egfr: num(p.egfr),
    },
  };
}

// The profile sheet's fields -> a PUT /api/profile body (only these fields change).
function toBackend(p, previousContact, previousFamilyHistory) {
  const [first, ...rest] = p.fullName.split(" ");
  const r = { ...EMPTY_RISK, ...(p.risk ?? {}) };
  return {
    sex: r.sex,
    smoking_status: r.smokingStatus,
    diabetes: r.diabetes,
    known_hypertension: r.knownHypertension,
    known_af: r.knownAf,
    known_chd: r.knownChd,
    known_pd: r.knownPd,
    known_dementia: r.knownDementia,
    // Keep other family-history keys the backend may hold; only premature CVD is edited here.
    family_history: { ...(previousFamilyHistory ?? {}), premature_cvd: r.familyPrematureCvd },
    total_chol_mg_dl: r.totalChol,
    hdl_mg_dl: r.hdl,
    ldl_mg_dl: r.ldl,
    egfr: r.egfr,
    first_name: first || null,
    last_name: rest.join(" ") || null,
    display_name: first || null,
    date_of_birth: p.dateOfBirth || null,
    // Keep any contact fields the sheet doesn't edit (e.g. the contact's email).
    emergency_contact: p.emergencyContact ? { ...previousContact, ...p.emergencyContact } : null,
    allergies: p.allergies,
    conditions: p.conditions,
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
    if (!signedIn) {
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

  const profile = withDerived({
    ...EMPTY,
    ...(stored ? LOCAL_ONLY({ ...EMPTY, ...stored }) : {}),
    ...(remote
      ? fromBackend(remote, session?.email)
      : { fullName: (session?.email ?? "").split("@")[0], email: session?.email ?? "" }),
  });

  // The family doctor the backend holds ({ name, clinic, phone } or null); shown as the care team.
  const familyDoctor = remote?.family_doctor?.name ? remote.family_doctor : null;

  // Throws if the backend refuses (e.g. a phone number it can't read), so the
  // sheet can show the error and keep the draft.
  const updateProfile = async (next) => {
    const clean = editable(next);
    const saved = await saveProfile(toBackend(clean, remote?.emergency_contact, remote?.family_history));
    setRemote(saved);
    setStored(LOCAL_ONLY(clean));
    window.dispatchEvent(new Event(PROFILE_SAVED_EVENT));
  };

  // Onboarding saves backend fields as they are (it edits some the sheet doesn't:
  // height, weight, CKD, family doctor, referral). Throws like updateProfile.
  const patchProfile = async (fields) => {
    setRemote(await saveProfile(fields));
    window.dispatchEvent(new Event(PROFILE_SAVED_EVENT));
  };

  const finishOnboarding = async () => setRemote(await completeOnboarding());

  return (
    <ProfileContext.Provider
      value={{
        profile,
        raw: remote, // the backend profile as returned (null until loaded)
        familyDoctor,
        updateProfile,
        patchProfile,
        finishOnboarding,
        loading: signedIn && !remote && !loadError,
      }}
    >
      {children}
    </ProfileContext.Provider>
  );
}

export function useProfile() {
  return useContext(ProfileContext);
}
