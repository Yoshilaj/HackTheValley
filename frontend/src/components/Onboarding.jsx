import { useEffect, useRef, useState } from "react";
import { grantConsent } from "../api/client.js";
import { useProfile } from "../context/ProfileContext.jsx";
import { ListEditor, PROVINCES, SEX_OPTIONS, SMOKING_OPTIONS, Segmented, YES_NO, formatPostal } from "./ProfileSheet.jsx";
import WatchPairing from "./WatchPairing.jsx";

// First-run onboarding, shown once after sign-up (OnboardingGate). It collects
// what the booking agent needs to book for the user, a health baseline, the
// permissions the agent acts on, and (optionally) the Apple Watch:
//   about    legal name, date of birth, sex: how a clinic finds the patient
//   contact  phone, e-mail, street, city, province, postal code
//   care     family doctor and referral (recommended, not required)
//   health   height, weight, smoking, known conditions, allergies (all optional)
//   consent  share_with_clinic + ai_assessment (the agent books only with both)
//            and data_processing (e-mail notifications)
//   watch    the PD Companion pairing code (optional)
// Every "Continue" saves that step (PUT /api/profile, only its fields), so
// leaving half-way keeps what was entered. "Finish later" closes it for this
// browser session; it comes back at the next sign-in until finished
// (POST /api/profile/onboarding-complete). Field names are the backend's.
const STEPS = [
  ["about", "About you"],
  ["contact", "Contact and address"],
  ["care", "Your care"],
  ["health", "Your health"],
  ["consent", "Permissions"],
  ["watch", "Apple Watch"],
];
const PHONE = /^\+?[\d\s().-]{7,24}$/;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const POSTAL = /^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ -]?\d[ABCEGHJ-NPRSTV-Z]\d$/i;
const today = () => new Date().toLocaleDateString("en-CA");

const CONDITIONS = [
  ["known_hypertension", "High blood pressure"],
  ["diabetes", "Diabetes"],
  ["ckd", "Chronic kidney disease"],
  ["known_af", "Atrial fibrillation"],
  ["known_chd", "Heart disease (angina, heart attack, stent)"],
  ["known_pd", "Parkinson's disease"],
  ["known_dementia", "Dementia"],
];
const CONSENTS = [
  ["share_with_clinic", "Contact clinics for me", "The agent may send a clinic your name, date of birth, contact details, postal code, the reason for the visit and a link to your report."],
  ["ai_assessment", "Act on my assessments", "When an assessment recommends a visit, the agent may prepare a booking request for you to approve."],
  ["data_processing", "E-mail me updates", "Booking requests, confirmations and reminders by e-mail."],
];

const str = (v) => (v == null ? "" : String(v));
const numOrNull = (t) => {
  const clean = String(t).trim().replace(",", ".");
  return clean === "" || Number.isNaN(Number(clean)) ? null : Number(clean);
};

function draftFrom(p, accountEmail) {
  const fd = p.family_doctor ?? {};
  return {
    first_name: str(p.first_name),
    last_name: str(p.last_name),
    display_name: str(p.display_name),
    date_of_birth: str(p.date_of_birth),
    sex: p.sex ?? null,
    phone: str(p.phone),
    contact_email: str(p.contact_email) || accountEmail || "",
    street: str(p.street),
    city: str(p.city),
    province: str(p.province),
    postal_code: str(p.postal_code),
    doctor_name: str(fd.name),
    doctor_clinic: str(fd.clinic),
    doctor_phone: str(fd.phone),
    has_referral: p.has_referral ?? null,
    height_cm: str(p.height_cm),
    weight_kg: str(p.weight_kg),
    smoking_status: p.smoking_status ?? null,
    ...Object.fromEntries(CONDITIONS.map(([k]) => [k, typeof p[k] === "boolean" ? p[k] : null])),
    premature_cvd: typeof p.family_history?.premature_cvd === "boolean" ? p.family_history.premature_cvd : null,
    allergies: Array.isArray(p.allergies) ? p.allergies : [],
    conditions: Array.isArray(p.conditions) ? p.conditions : [],
  };
}

function validate(step, d) {
  const e = {};
  if (step === "about") {
    if (!d.first_name.trim()) e.first_name = "Enter your first name as it appears on your health card.";
    if (!d.last_name.trim()) e.last_name = "Enter your last name.";
    if (!d.date_of_birth) e.date_of_birth = "Enter your date of birth.";
    else if (d.date_of_birth >= today() || d.date_of_birth < "1900-01-01") e.date_of_birth = "Enter a date of birth in the past.";
    if (!d.sex) e.sex = "Choose the sex on your health card.";
  }
  if (step === "contact") {
    if (!PHONE.test(d.phone.trim())) e.phone = "Enter a phone number like +1 416-555-0142.";
    if (!EMAIL.test(d.contact_email.trim())) e.contact_email = "Enter an e-mail address like name@example.com.";
    if (!d.street.trim()) e.street = "Enter your street address.";
    if (!d.city.trim()) e.city = "Enter your city.";
    if (!d.province) e.province = "Choose your province.";
    if (!POSTAL.test(d.postal_code.trim())) e.postal_code = "Enter a postal code like M5H 2N2.";
  }
  if (step === "care" && d.doctor_phone.trim() && !PHONE.test(d.doctor_phone.trim())) e.doctor_phone = "Enter a phone number like +1 416-555-0106.";
  if (step === "health") {
    if (d.height_cm !== "" && !(numOrNull(d.height_cm) > 50 && numOrNull(d.height_cm) < 260)) e.height_cm = "Enter your height in centimetres.";
    if (d.weight_kg !== "" && !(numOrNull(d.weight_kg) > 20 && numOrNull(d.weight_kg) < 400)) e.weight_kg = "Enter your weight in kilograms.";
  }
  return e;
}

// The PUT body for one step: only that step's fields.
function payload(step, d, raw) {
  const t = (v) => v.trim() || null;
  if (step === "about")
    return {
      first_name: t(d.first_name),
      last_name: t(d.last_name),
      display_name: t(d.display_name) || t(d.first_name),
      date_of_birth: d.date_of_birth,
      sex: d.sex,
    };
  if (step === "contact")
    return {
      phone: t(d.phone),
      contact_email: t(d.contact_email),
      street: t(d.street),
      city: t(d.city),
      province: d.province,
      postal_code: formatPostal(d.postal_code),
    };
  if (step === "care") {
    const doctor = { name: t(d.doctor_name), clinic: t(d.doctor_clinic), phone: t(d.doctor_phone) };
    return { family_doctor: doctor.name || doctor.clinic || doctor.phone ? doctor : null, has_referral: d.has_referral };
  }
  if (step === "health")
    return {
      height_cm: numOrNull(d.height_cm),
      weight_kg: numOrNull(d.weight_kg),
      smoking_status: d.smoking_status,
      ...Object.fromEntries(CONDITIONS.map(([k]) => [k, d[k]])),
      family_history: { ...(raw?.family_history ?? {}), premature_cvd: d.premature_cvd },
      allergies: d.allergies,
      conditions: d.conditions,
    };
  return null;
}

function Field({ id, label, optional, error, children }) {
  return (
    <div className="form-field">
      <label htmlFor={id}>
        {label} {optional && <span className="book-optional">Optional</span>}
      </label>
      {children}
      {error && (
        <p id={`${id}-error`} className="field-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export default function Onboarding({ accountEmail, onLater, onDone }) {
  const ref = useRef(null);
  const bodyRef = useRef(null);
  const { raw, patchProfile, finishOnboarding } = useProfile();
  const [index, setIndex] = useState(0);
  const [draft, setDraft] = useState(() => draftFrom(raw ?? {}, accountEmail));
  const [pending, setPending] = useState({ allergies: "", conditions: "" });
  const [consents, setConsents] = useState({ share_with_clinic: false, ai_assessment: false, data_processing: false });
  const [errors, setErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [finished, setFinished] = useState(false);
  const [watchPaired, setWatchPaired] = useState(false);

  const [step, title] = STEPS[index];

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog.open) dialog.showModal();
  }, []);

  // New step: back to the top, cursor in the first box.
  useEffect(() => {
    bodyRef.current?.scrollTo?.({ top: 0 });
    requestAnimationFrame(() => bodyRef.current?.querySelector("input, select, button.btn-primary")?.focus());
  }, [index, finished]);

  const set = (field, value) => {
    setDraft((d) => ({ ...d, [field]: value }));
    setErrors((e) => ({ ...e, [field]: undefined }));
  };
  const invalid = (key) => ({
    "aria-invalid": Boolean(errors[key]) || undefined,
    "aria-describedby": errors[key] ? `onb-${key}-error` : undefined,
  });
  const input = (key, props = {}) => (
    <input id={`onb-${key}`} className="text-input" value={draft[key]} onChange={(e) => set(key, e.target.value)} {...invalid(key)} {...props} />
  );

  // Anything typed in an "add" box but not added yet is kept.
  const withPending = (list, text) => {
    const value = text.trim();
    return value && !list.some((i) => i.toLowerCase() === value.toLowerCase()) ? [...list, value] : list;
  };

  const next = async () => {
    const d =
      step === "health"
        ? { ...draft, allergies: withPending(draft.allergies, pending.allergies), conditions: withPending(draft.conditions, pending.conditions) }
        : draft;
    const found = validate(step, d);
    setErrors(found);
    if (Object.keys(found).length) {
      requestAnimationFrame(() => bodyRef.current?.querySelector('[aria-invalid="true"]')?.focus());
      return;
    }
    setSaving(true);
    setSaveError("");
    try {
      if (step === "consent") {
        for (const [purpose] of CONSENTS) if (consents[purpose]) await grantConsent(purpose);
      } else if (step === "watch") {
        await finishOnboarding();
        setFinished(true);
        return;
      } else {
        await patchProfile(payload(step, d, raw));
        if (step === "health") {
          setDraft(d);
          setPending({ allergies: "", conditions: "" });
        }
      }
      setIndex((i) => i + 1);
    } catch (e) {
      setSaveError(
        e?.status === 422
          ? "Some details weren't accepted. Check the phone numbers, the e-mail, the postal code and the date of birth."
          : "Couldn't save. Check your connection and try again."
      );
    } finally {
      setSaving(false);
    }
  };

  const allConsents = CONSENTS.every(([k]) => consents[k]);
  const bookingAllowed = consents.share_with_clinic && consents.ai_assessment;

  return (
    <dialog ref={ref} className="sheet onboarding-sheet" aria-labelledby="onb-title" onCancel={(e) => (e.preventDefault(), onLater())}>
      <div className="sheet-body" ref={bodyRef}>
        {finished ? (
          <div className="onb-done">
            <span className="onb-done-mark" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M5 12.5l4.5 4.5L19 7.5" />
              </svg>
            </span>
            <h2 id="onb-title" className="book-title">
              You're all set
            </h2>
            <p className="book-sub">
              {bookingAllowed
                ? "When an assessment recommends a visit, CareSync's agent can now ask a clinic for a time. You approve every request first."
                : "Your details are saved. To let the agent book visits for you, allow it from the booking pop-up when the time comes."}
              {watchPaired ? " Your Apple Watch data is syncing." : ""}
            </p>
            <p className="field-hint">You can change any of this later from your profile.</p>
            <button type="button" className="btn-primary" onClick={onDone}>
              Go to my dashboard
            </button>
          </div>
        ) : (
          <>
            <header className="sheet-head">
              <div>
                <p className="onb-count">
                  Step {index + 1} of {STEPS.length}
                </p>
                <h2 id="onb-title" className="book-title">
                  {title}
                </h2>
              </div>
              <button type="button" className="btn-secondary onb-later" onClick={onLater}>
                Finish later
              </button>
            </header>
            <ol className="onb-progress" aria-hidden="true">
              {STEPS.map(([k], i) => (
                <li key={k} className={i < index ? "done" : i === index ? "current" : ""} />
              ))}
            </ol>

            <form
              className="profile-form onb-form"
              noValidate
              onSubmit={(e) => {
                e.preventDefault();
                next();
              }}
            >
              {step === "about" && (
                <>
                  <p className="book-sub">CareSync's agent books visits under these details, so use them exactly as on your health card.</p>
                  <div className="profile-row">
                    <Field id="onb-first_name" label="Legal first name" error={errors.first_name}>
                      {input("first_name", { autoComplete: "given-name" })}
                    </Field>
                    <Field id="onb-last_name" label="Last name" error={errors.last_name}>
                      {input("last_name", { autoComplete: "family-name" })}
                    </Field>
                  </div>
                  <Field id="onb-display_name" label="What should we call you?" optional>
                    {input("display_name", { autoComplete: "nickname", placeholder: draft.first_name || "e.g. Bob" })}
                  </Field>
                  <Field id="onb-date_of_birth" label="Date of birth" error={errors.date_of_birth}>
                    {input("date_of_birth", { type: "date", max: today(), autoComplete: "bday" })}
                  </Field>
                  <div className="form-field">
                    <span className="form-label">Sex on your health card</span>
                    <Segmented name="onb-sex" label="Sex on your health card" value={draft.sex} options={SEX_OPTIONS} onChange={(v) => set("sex", v)} />
                    {errors.sex && (
                      <p className="field-error" role="alert">
                        {errors.sex}
                      </p>
                    )}
                  </div>
                </>
              )}

              {step === "contact" && (
                <>
                  <p className="book-sub">Where a clinic reaches you, and how the agent finds clinics near you.</p>
                  <div className="profile-row">
                    <Field id="onb-phone" label="Phone" error={errors.phone}>
                      {input("phone", { type: "tel", autoComplete: "tel", placeholder: "+1 416-555-0142" })}
                    </Field>
                    <Field id="onb-contact_email" label="E-mail" error={errors.contact_email}>
                      {input("contact_email", { type: "email", autoComplete: "email" })}
                    </Field>
                  </div>
                  <Field id="onb-street" label="Street address" error={errors.street}>
                    {input("street", { autoComplete: "street-address", placeholder: "100 Queen St W, Unit 4" })}
                  </Field>
                  <div className="profile-row">
                    <Field id="onb-city" label="City" error={errors.city}>
                      {input("city", { autoComplete: "address-level2" })}
                    </Field>
                    <Field id="onb-province" label="Province" error={errors.province}>
                      <select
                        id="onb-province"
                        className="text-input"
                        autoComplete="address-level1"
                        value={draft.province}
                        onChange={(e) => set("province", e.target.value)}
                        {...invalid("province")}
                      >
                        <option value="">Choose…</option>
                        {PROVINCES.map(([code, name]) => (
                          <option key={code} value={code}>
                            {name}
                          </option>
                        ))}
                      </select>
                    </Field>
                  </div>
                  <Field id="onb-postal_code" label="Postal code" error={errors.postal_code}>
                    {input("postal_code", { autoComplete: "postal-code", placeholder: "M5H 2N2", maxLength: 7 })}
                  </Field>
                  <p className="field-hint">Your health card number is never collected; bring the card to the visit.</p>
                </>
              )}

              {step === "care" && (
                <>
                  <p className="book-sub">Specialists in Canada usually need a referral from your family doctor. Skip anything you don't have.</p>
                  <Field id="onb-doctor_name" label="Family doctor" optional>
                    {input("doctor_name", { placeholder: "Dr. Anita Shah" })}
                  </Field>
                  <div className="profile-row">
                    <Field id="onb-doctor_clinic" label="Their clinic" optional>
                      {input("doctor_clinic")}
                    </Field>
                    <Field id="onb-doctor_phone" label="Clinic phone" optional error={errors.doctor_phone}>
                      {input("doctor_phone", { type: "tel" })}
                    </Field>
                  </div>
                  <div className="form-field">
                    <span className="form-label">Do you already have a referral to a specialist?</span>
                    <Segmented name="onb-referral" label="Referral" value={draft.has_referral} options={YES_NO} onChange={(v) => set("has_referral", v)} />
                  </div>
                </>
              )}

              {step === "health" && (
                <>
                  <p className="book-sub">Your starting point for the risk assessment. Leave anything you're not sure about.</p>
                  <div className="profile-row">
                    <Field id="onb-height_cm" label="Height (cm)" optional error={errors.height_cm}>
                      {input("height_cm", { inputMode: "decimal", placeholder: "170" })}
                    </Field>
                    <Field id="onb-weight_kg" label="Weight (kg)" optional error={errors.weight_kg}>
                      {input("weight_kg", { inputMode: "decimal", placeholder: "70" })}
                    </Field>
                  </div>
                  <div className="form-field">
                    <span className="form-label">Smoking</span>
                    <Segmented name="onb-smoking" label="Smoking" value={draft.smoking_status} options={SMOKING_OPTIONS} onChange={(v) => set("smoking_status", v)} />
                  </div>
                  <fieldset className="onb-conditions">
                    <legend className="form-label">Has a doctor told you that you have…</legend>
                    {CONDITIONS.map(([key, label]) => (
                      <div className="onb-condition" key={key}>
                        <span>{label}</span>
                        <Segmented name={`onb-${key}`} label={label} value={draft[key]} options={YES_NO} onChange={(v) => set(key, v)} />
                      </div>
                    ))}
                    <div className="onb-condition">
                      <span>A parent, brother or sister had a heart attack or stroke early (men under 55, women under 65)</span>
                      <Segmented name="onb-premature" label="Family history" value={draft.premature_cvd} options={YES_NO} onChange={(v) => set("premature_cvd", v)} />
                    </div>
                  </fieldset>
                  <ListEditor
                    id="onb-allergy"
                    label="Allergies"
                    items={draft.allergies}
                    onChange={(v) => set("allergies", v)}
                    text={pending.allergies}
                    onText={(t) => setPending((p) => ({ ...p, allergies: t }))}
                    placeholder="e.g. Penicillin"
                  />
                  <ListEditor
                    id="onb-condition"
                    label="Other conditions"
                    items={draft.conditions}
                    onChange={(v) => set("conditions", v)}
                    text={pending.conditions}
                    onText={(t) => setPending((p) => ({ ...p, conditions: t }))}
                    placeholder="e.g. Asthma"
                  />
                </>
              )}

              {step === "consent" && (
                <>
                  <p className="book-sub">What CareSync's booking agent may do for you. It never confirms a visit without your tap.</p>
                  <ul className="onb-consents">
                    {CONSENTS.map(([key, label, detail]) => (
                      <li key={key}>
                        <label>
                          <input type="checkbox" checked={consents[key]} onChange={(e) => setConsents((c) => ({ ...c, [key]: e.target.checked }))} />
                          <span>
                            <b>{label}</b>
                            <small>{detail}</small>
                          </span>
                        </label>
                      </li>
                    ))}
                  </ul>
                  {!allConsents && (
                    <button
                      type="button"
                      className="btn-secondary onb-allow-all"
                      onClick={() => setConsents(Object.fromEntries(CONSENTS.map(([k]) => [k, true])))}
                    >
                      Allow all
                    </button>
                  )}
                  {!bookingAllowed && <p className="field-hint">Without the first two, the agent won't book for you. You can allow it later.</p>}
                </>
              )}

              {step === "watch" && (
                <>
                  <p className="book-sub">
                    Optional. Steps, heart rate, sleep and walking speed from your Apple Watch make the assessment more accurate. You can also do this
                    later on the Data page.
                  </p>
                  <div className="onb-watch">
                    <WatchPairing onPaired={() => setWatchPaired(true)} />
                  </div>
                </>
              )}

              {saveError && (
                <p className="field-error" role="alert">
                  {saveError}
                </p>
              )}

              <div className="book-actions onb-actions">
                {index > 0 && (
                  <button type="button" className="btn-secondary" onClick={() => setIndex((i) => i - 1)} disabled={saving}>
                    Back
                  </button>
                )}
                <button type="submit" className="btn-primary" disabled={saving}>
                  {saving ? "Saving…" : step === "watch" ? (watchPaired ? "Finish" : "Skip and finish") : "Continue"}
                </button>
              </div>
            </form>
          </>
        )}
      </div>
    </dialog>
  );
}
