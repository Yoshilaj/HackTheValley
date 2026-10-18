import { useEffect, useRef, useState } from "react";
import { EMPTY_RISK, useProfile } from "../context/ProfileContext.jsx";
import { ageFrom } from "../utils/dates.js";
import { CareTeamList } from "./CareTeamSheet.jsx";

// Health profile behind the avatar menu: who the user is, what clinicians need
// to know at a glance, and their care team. "Edit" turns the details into a
// form: change the name, birth date or health card, add or remove allergies
// and conditions, the contact and address details a clinic needs when booking,
// and add, change or remove the emergency contact. Nothing is saved until
// "Save"; Cancel or closing the sheet discards the draft.
const PHONE = /^[\d\s()+.-]{7,}$/;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const POSTAL = /^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ -]?\d[ABCEGHJ-NPRSTV-Z]\d$/i; // Canadian, A1A 1A1
const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local

export const PROVINCES = [
  ["AB", "Alberta"],
  ["BC", "British Columbia"],
  ["MB", "Manitoba"],
  ["NB", "New Brunswick"],
  ["NL", "Newfoundland and Labrador"],
  ["NS", "Nova Scotia"],
  ["NT", "Northwest Territories"],
  ["NU", "Nunavut"],
  ["ON", "Ontario"],
  ["PE", "Prince Edward Island"],
  ["QC", "Quebec"],
  ["SK", "Saskatchewan"],
  ["YT", "Yukon"],
];
// Sex is edited with the risk factors (draft.risk.sex); clinics receive it from there too.
const CONTACT_FIELDS = ["phone", "email", "street", "city", "province", "postalCode"];

// "m5h2n2" -> "M5H 2N2"
export const formatPostal = (v) => {
  const s = v.replace(/[\s-]/g, "").toUpperCase();
  return s.length === 6 ? `${s.slice(0, 3)} ${s.slice(3)}` : v.trim().toUpperCase();
};

// "100 Queen St W, Toronto, ON M5H 2N2" from whichever parts are filled in.
function addressLine(p) {
  const tail = [p.province, p.postalCode].filter(Boolean).join(" ");
  return [p.street, p.city, tail].filter(Boolean).join(", ");
}

function formatBorn(dateOfBirth) {
  const d = new Date(`${dateOfBirth}T00:00`);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
}

function validate(d) {
  const errors = {};
  if (!d.fullName.trim()) errors.fullName = "Enter your full name.";
  if (!d.dateOfBirth) errors.dateOfBirth = "Enter your date of birth.";
  else if (d.dateOfBirth > today() || d.dateOfBirth < "1900-01-01") errors.dateOfBirth = "Enter a real date of birth.";
  if (d.healthCardLast4 && !/^\d{4}$/.test(d.healthCardLast4)) errors.healthCardLast4 = "Enter the last 4 digits of your health card.";
  if (d.phone && !PHONE.test(d.phone)) errors.phone = "Enter a phone number, like (416) 555-0187.";
  if (d.email && !EMAIL.test(d.email)) errors.email = "Enter an e-mail address, like name@example.com.";
  if (d.postalCode && !POSTAL.test(d.postalCode)) errors.postalCode = "Enter a Canadian postal code, like M5H 2N2.";
  for (const [key, label, unit, min, max] of LABS) {
    const v = d.risk?.[key];
    if (v != null && (!Number.isFinite(v) || v < min || v > max)) errors[key] = `${label}: enter ${min}-${max} ${unit}.`;
  }
  const c = d.emergencyContact;
  if (c) {
    if (!c.name.trim()) errors.contactName = "Enter your contact's name.";
    if (!PHONE.test(c.phone.trim())) errors.contactPhone = "Enter a phone number, like (416) 555-0187.";
  }
  return errors;
}

// Risk factors the heart-risk calculator and the rule engine read from the profile.
export const SEX_OPTIONS = [
  ["male", "Male"],
  ["female", "Female"],
  ["other", "Other"],
];
export const SMOKING_OPTIONS = [
  ["never", "Never"],
  ["former", "Former"],
  ["current", "Current"],
];
export const YES_NO = [
  [true, "Yes"],
  [false, "No"],
  [null, "Not sure"],
];
const KNOWN = [
  ["knownHypertension", "High blood pressure diagnosed"],
  ["diabetes", "Diabetes"],
  ["knownAf", "Atrial fibrillation diagnosed"],
  ["knownChd", "Heart disease (angina, heart attack, stent)"],
  ["knownPd", "Parkinson's disease diagnosed"],
  ["knownDementia", "Dementia diagnosed"],
  ["familyPrematureCvd", "A parent, brother or sister had a heart attack or stroke early (men under 55, women under 65)"],
];
// Labs are entered in mg/dL, the unit the backend stores (it refuses mmol/L values with a hint).
const LABS = [
  ["totalChol", "Total cholesterol", "mg/dL", 100, 405],
  ["hdl", "HDL cholesterol", "mg/dL", 20, 100],
  ["ldl", "LDL cholesterol", "mg/dL", 30, 300],
  ["egfr", "Kidney function (eGFR)", "mL/min", 1, 200],
];

// Three-way choice drawn as the app's segmented control.
export function Segmented({ name, value, options, onChange, label }) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map(([v, text]) => (
        <label key={String(v)}>
          <input type="radio" name={name} checked={value === v} onChange={() => onChange(v)} />
          <span>{text}</span>
        </label>
      ))}
    </div>
  );
}

// Editable list (allergies, conditions): each item has a remove button, and
// the box below adds a new one with Enter or the Add button.
export function ListEditor({ id, label, items, onChange, text, onText, placeholder }) {
  const add = () => {
    const value = text.trim();
    if (!value) return;
    if (!items.some((i) => i.toLowerCase() === value.toLowerCase())) onChange([...items, value]);
    onText("");
  };

  return (
    <fieldset className="form-field profile-list">
      <legend>{label}</legend>
      {items.length > 0 ? (
        <ul className="tag-list">
          {items.map((item) => (
            <li key={item} className="tag">
              <span>{item}</span>
              <button type="button" aria-label={`Remove ${item}`} onClick={() => onChange(items.filter((i) => i !== item))}>
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M7 7l10 10M17 7L7 17" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="profile-none">None recorded</p>
      )}
      <div className="tag-add">
        <label htmlFor={id} className="sr-only">
          Add to {label.toLowerCase()}
        </label>
        <input
          id={id}
          className="text-input"
          value={text}
          placeholder={placeholder}
          onChange={(e) => onText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            e.preventDefault(); // add, don't submit the whole form
            add();
          }}
        />
        <button type="button" className="btn-secondary" onClick={add} disabled={!text.trim()}>
          Add
        </button>
      </div>
    </fieldset>
  );
}

export default function ProfileSheet({ open, onClose }) {
  const ref = useRef(null);
  const editBtnRef = useRef(null);
  const nameRef = useRef(null);
  const formRef = useRef(null);
  const { profile, updateProfile } = useProfile();
  const [draft, setDraft] = useState(null); // null = viewing; otherwise the form's values
  const [pending, setPending] = useState({ allergies: "", conditions: "" }); // typed but not yet added
  const [labText, setLabText] = useState({}); // lab boxes as typed; parsed into draft.risk on change
  const [errors, setErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
    if (!open) {
      setDraft(null);
      setSaved(false);
    }
  }, [open]);

  const startEdit = () => {
    setDraft({
      fullName: profile.fullName,
      dateOfBirth: profile.dateOfBirth,
      healthCardLast4: profile.healthCardLast4 ?? "",
      allergies: [...profile.allergies],
      conditions: [...profile.conditions],
      emergencyContact: profile.emergencyContact ? { ...profile.emergencyContact } : null,
      ...Object.fromEntries(CONTACT_FIELDS.map((k) => [k, profile[k] ?? ""])),
      risk: { ...EMPTY_RISK, ...(profile.risk ?? {}) },
    });
    setLabText(Object.fromEntries(LABS.map(([k]) => [k, profile.risk?.[k] != null ? String(profile.risk[k]) : ""])));
    setPending({ allergies: "", conditions: "" });
    setErrors({});
    setSaveError("");
    setSaved(false);
    // Entering edit mode puts the cursor in the name box.
    requestAnimationFrame(() => nameRef.current?.focus());
  };

  const stopEdit = () => {
    setDraft(null);
    requestAnimationFrame(() => editBtnRef.current?.focus());
  };

  const set = (field, value) => {
    setDraft((d) => ({ ...d, [field]: value }));
    setErrors((e) => ({ ...e, [field]: undefined }));
  };
  const setRisk = (field, value) => {
    setDraft((d) => ({ ...d, risk: { ...d.risk, [field]: value } }));
    setErrors((e) => ({ ...e, [field]: undefined }));
  };
  const setLab = (field, text) => {
    setLabText((t) => ({ ...t, [field]: text }));
    const clean = text.trim().replace(",", ".");
    setRisk(field, clean === "" ? null : Number(clean));
  };
  const setContact = (field, value) => {
    setDraft((d) => ({ ...d, emergencyContact: { ...d.emergencyContact, [field]: value } }));
    setErrors((e) => ({ ...e, [field === "name" ? "contactName" : "contactPhone"]: undefined }));
  };

  // Anything typed in an "add" box but not added yet is kept on save.
  const withPending = (list, text) => {
    const value = text.trim();
    return value && !list.some((i) => i.toLowerCase() === value.toLowerCase()) ? [...list, value] : list;
  };

  const save = async (e) => {
    e.preventDefault();
    const next = {
      ...draft,
      fullName: draft.fullName.trim().replace(/\s+/g, " "),
      healthCardLast4: draft.healthCardLast4.trim(),
      phone: draft.phone.trim(),
      email: draft.email.trim(),
      street: draft.street.trim(),
      city: draft.city.trim(),
      postalCode: draft.postalCode.trim() && formatPostal(draft.postalCode),
      allergies: withPending(draft.allergies, pending.allergies),
      conditions: withPending(draft.conditions, pending.conditions),
      emergencyContact: draft.emergencyContact && {
        name: draft.emergencyContact.name.trim(),
        relation: draft.emergencyContact.relation.trim(),
        phone: draft.emergencyContact.phone.trim(),
      },
    };
    const found = validate(next);
    setErrors(found);
    if (Object.keys(found).length) {
      formRef.current?.querySelector('[aria-invalid="true"]')?.focus();
      return;
    }
    setSaving(true);
    setSaveError("");
    try {
      await updateProfile(next);
      setSaved(true);
      stopEdit();
    } catch (e) {
      setSaveError(
        e?.status === 422
          ? "Some details weren't accepted. Check the phone numbers (like +1 416-555-0187), the e-mail, the postal code and the date of birth."
          : "Couldn't save your changes. Check your connection and try again."
      );
    } finally {
      setSaving(false);
    }
  };

  const contact = profile.emergencyContact;
  const born = formatBorn(profile.dateOfBirth);
  const err = (key) =>
    errors[key] && (
      <p id={`profile-${key}-error`} className="field-error" role="alert">
        {errors[key]}
      </p>
    );
  const invalid = (key) => ({
    "aria-invalid": Boolean(errors[key]) || undefined,
    "aria-describedby": errors[key] ? `profile-${key}-error` : undefined,
  });

  return (
    <dialog
      ref={ref}
      className="sheet book-sheet profile-sheet"
      aria-labelledby="profile-title"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      {open && (
        <div className="sheet-body">
          <header className="sheet-head">
            <div className="profile-who">
              <span className="avatar" aria-hidden="true">
                {profile.initials}
              </span>
              <div>
                <h2 id="profile-title" className="book-title">
                  {draft ? "Edit profile" : profile.fullName}
                </h2>
                <p className="book-sub">
                  {draft
                    ? "Changes apply once you save."
                    : born
                      ? `Age ${ageFrom(profile.dateOfBirth)} · Born ${born}`
                      : "Add your date of birth with Edit."}
                </p>
              </div>
            </div>
            <div className="profile-head-actions">
              {!draft && (
                <button ref={editBtnRef} type="button" className="btn-secondary" onClick={startEdit}>
                  Edit
                </button>
              )}
              <button className="sheet-close" aria-label="Close" onClick={onClose}>
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>
          </header>

          {saved && !draft && (
            <p className="profile-saved" role="status">
              Profile updated.
            </p>
          )}

          {draft ? (
            <form ref={formRef} className="profile-form" noValidate onSubmit={save}>
              <section className="profile-section" aria-labelledby="profile-edit-about">
                <h3 id="profile-edit-about" className="sheet-sub">
                  About you
                </h3>
                <div className="form-field">
                  <label htmlFor="profile-name">Full name</label>
                  <input
                    ref={nameRef}
                    id="profile-name"
                    className="text-input"
                    autoComplete="name"
                    value={draft.fullName}
                    onChange={(e) => set("fullName", e.target.value)}
                    {...invalid("fullName")}
                  />
                  {err("fullName")}
                </div>
                <div className="profile-row">
                  <div className="form-field">
                    <label htmlFor="profile-dob">Date of birth</label>
                    <input
                      id="profile-dob"
                      className="text-input"
                      type="date"
                      max={today()}
                      value={draft.dateOfBirth}
                      onChange={(e) => set("dateOfBirth", e.target.value)}
                      {...invalid("dateOfBirth")}
                    />
                    {err("dateOfBirth")}
                  </div>
                  <div className="form-field">
                    <label htmlFor="profile-card">Health card, last 4 digits</label>
                    <input
                      id="profile-card"
                      className="text-input"
                      inputMode="numeric"
                      maxLength={4}
                      value={draft.healthCardLast4}
                      onChange={(e) => set("healthCardLast4", e.target.value.replace(/\D/g, ""))}
                      {...invalid("healthCardLast4")}
                    />
                    {err("healthCardLast4")}
                  </div>
                </div>
                <ListEditor
                  id="profile-allergy"
                  label="Allergies"
                  items={draft.allergies}
                  onChange={(v) => set("allergies", v)}
                  text={pending.allergies}
                  onText={(t) => setPending((p) => ({ ...p, allergies: t }))}
                  placeholder="e.g. Peanuts"
                />
                <ListEditor
                  id="profile-condition"
                  label="Conditions"
                  items={draft.conditions}
                  onChange={(v) => set("conditions", v)}
                  text={pending.conditions}
                  onText={(t) => setPending((p) => ({ ...p, conditions: t }))}
                  placeholder="e.g. Asthma"
                />
              </section>

              <section className="profile-section" aria-labelledby="profile-edit-booking">
                <h3 id="profile-edit-booking" className="sheet-sub">
                  Contact and address
                </h3>
                <p className="autobook-note">
                  Clinics ask for these when you book. CareSync shares them with a clinic only after you approve a
                  request.
                </p>
                <div className="profile-row">
                  <div className="form-field">
                    <label htmlFor="profile-phone">Phone</label>
                    <input
                      id="profile-phone"
                      className="text-input"
                      type="tel"
                      autoComplete="tel"
                      placeholder="(416) 555-0187"
                      value={draft.phone}
                      onChange={(e) => set("phone", e.target.value)}
                      {...invalid("phone")}
                    />
                    {err("phone")}
                  </div>
                  <div className="form-field">
                    <label htmlFor="profile-email">E-mail</label>
                    <input
                      id="profile-email"
                      className="text-input"
                      type="email"
                      autoComplete="email"
                      value={draft.email}
                      onChange={(e) => set("email", e.target.value)}
                      {...invalid("email")}
                    />
                    {err("email")}
                  </div>
                </div>
                <div className="form-field">
                  <label htmlFor="profile-street">Street address</label>
                  <input
                    id="profile-street"
                    className="text-input"
                    autoComplete="street-address"
                    placeholder="100 Queen St W, Unit 4"
                    value={draft.street}
                    onChange={(e) => set("street", e.target.value)}
                  />
                </div>
                <div className="profile-row">
                  <div className="form-field">
                    <label htmlFor="profile-city">City</label>
                    <input
                      id="profile-city"
                      className="text-input"
                      autoComplete="address-level2"
                      value={draft.city}
                      onChange={(e) => set("city", e.target.value)}
                    />
                  </div>
                  <div className="form-field">
                    <label htmlFor="profile-province">Province</label>
                    <select
                      id="profile-province"
                      className="text-input"
                      autoComplete="address-level1"
                      value={draft.province}
                      onChange={(e) => set("province", e.target.value)}
                    >
                      <option value="">Choose…</option>
                      {PROVINCES.map(([code, name]) => (
                        <option key={code} value={code}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="form-field profile-postal">
                  <label htmlFor="profile-postal">Postal code</label>
                  <input
                    id="profile-postal"
                    className="text-input"
                    autoComplete="postal-code"
                    placeholder="M5H 2N2"
                    maxLength={7}
                    value={draft.postalCode}
                    onChange={(e) => set("postalCode", e.target.value)}
                    {...invalid("postalCode")}
                  />
                  {err("postalCode")}
                </div>
              </section>

              <section className="profile-section profile-risk" aria-labelledby="profile-edit-risk">
                <h3 id="profile-edit-risk" className="sheet-sub">
                  Risk factors
                </h3>
                <p className="field-hint reading-hint">Used for your heart-risk estimate and the assessment. Leave anything you don't know.</p>
                <div className="form-field">
                  <span className="form-label">Sex</span>
                  <Segmented name="profile-sex" label="Sex" value={draft.risk.sex} options={SEX_OPTIONS} onChange={(v) => setRisk("sex", v)} />
                </div>
                <div className="form-field">
                  <span className="form-label">Smoking</span>
                  <Segmented name="profile-smoking" label="Smoking" value={draft.risk.smokingStatus} options={SMOKING_OPTIONS} onChange={(v) => setRisk("smokingStatus", v)} />
                </div>
                {KNOWN.map(([key, label]) => (
                  <div className="form-field" key={key}>
                    <span className="form-label">{label}</span>
                    <Segmented name={`profile-${key}`} label={label} value={draft.risk[key]} options={YES_NO} onChange={(v) => setRisk(key, v)} />
                  </div>
                ))}
                <div className="profile-row">
                  {LABS.map(([key, label, unit]) => (
                    <div className="form-field" key={key}>
                      <label htmlFor={`profile-${key}`}>
                        {label} <span className="book-optional">Optional</span>
                      </label>
                      <div className={`input-unit ${errors[key] ? "invalid" : ""}`}>
                        <input
                          id={`profile-${key}`}
                          type="text"
                          inputMode="decimal"
                          value={labText[key] ?? ""}
                          onChange={(e) => setLab(key, e.target.value)}
                          {...invalid(key)}
                        />
                        <span className="unit">{unit}</span>
                      </div>
                      {err(key)}
                    </div>
                  ))}
                </div>
              </section>

              <section className="profile-section" aria-labelledby="profile-edit-contact">
                <h3 id="profile-edit-contact" className="sheet-sub">
                  Emergency contact
                </h3>
                {draft.emergencyContact ? (
                  <>
                    <div className="profile-row">
                      <div className="form-field">
                        <label htmlFor="profile-contact-name">Name</label>
                        <input
                          id="profile-contact-name"
                          className="text-input"
                          value={draft.emergencyContact.name}
                          onChange={(e) => setContact("name", e.target.value)}
                          {...invalid("contactName")}
                        />
                        {err("contactName")}
                      </div>
                      <div className="form-field">
                        <label htmlFor="profile-contact-relation">
                          Relationship <span className="book-optional">Optional</span>
                        </label>
                        <input
                          id="profile-contact-relation"
                          className="text-input"
                          placeholder="e.g. Partner"
                          value={draft.emergencyContact.relation}
                          onChange={(e) => setContact("relation", e.target.value)}
                        />
                      </div>
                    </div>
                    <div className="form-field">
                      <label htmlFor="profile-contact-phone">Phone</label>
                      <input
                        id="profile-contact-phone"
                        className="text-input"
                        type="tel"
                        autoComplete="tel"
                        value={draft.emergencyContact.phone}
                        onChange={(e) => setContact("phone", e.target.value)}
                        {...invalid("contactPhone")}
                      />
                      {err("contactPhone")}
                    </div>
                    <button
                      type="button"
                      className="btn-ghost btn-secondary-danger profile-remove"
                      onClick={() => {
                        set("emergencyContact", null);
                        setErrors((e) => ({ ...e, contactName: undefined, contactPhone: undefined }));
                      }}
                    >
                      Remove emergency contact
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="btn-secondary profile-add-contact"
                    onClick={() => set("emergencyContact", { name: "", relation: "", phone: "" })}
                  >
                    Add emergency contact
                  </button>
                )}
              </section>

              {saveError && (
                <p className="field-error" role="alert">
                  {saveError}
                </p>
              )}
              <div className="book-actions">
                <button type="button" className="btn-secondary" onClick={stopEdit} disabled={saving}>
                  Cancel
                </button>
                <button type="submit" className="btn-primary" disabled={saving}>
                  {saving ? "Saving…" : "Save"}
                </button>
              </div>
            </form>
          ) : (
            <section className="profile-section" aria-labelledby="profile-about">
              <h3 id="profile-about" className="sheet-sub">
                About you
              </h3>
              <dl className="sheet-rows">
                <div>
                  <dt>Health card</dt>
                  <dd>{profile.healthCardLast4 ? `Ending ${profile.healthCardLast4}` : "Not added"}</dd>
                </div>
                <div>
                  <dt>Allergies</dt>
                  <dd>{profile.allergies.length ? profile.allergies.join(", ") : "None recorded"}</dd>
                </div>
                <div>
                  <dt>Conditions</dt>
                  <dd>{profile.conditions.length ? profile.conditions.join(", ") : "None recorded"}</dd>
                </div>
                <div>
                  <dt>Phone</dt>
                  <dd>
                    {profile.phone ? (
                      <a className="inline-link" href={`tel:${profile.phone.replace(/[^\d+]/g, "")}`}>
                        {profile.phone}
                      </a>
                    ) : (
                      "Not added"
                    )}
                  </dd>
                </div>
                <div>
                  <dt>E-mail</dt>
                  <dd>{profile.email || "Not added"}</dd>
                </div>
                <div>
                  <dt>Address</dt>
                  <dd>{addressLine(profile) || "Not added"}</dd>
                </div>
                <div>
                  <dt>Risk factors</dt>
                  <dd>
                    {[
                      profile.risk?.sex && SEX_OPTIONS.find(([v]) => v === profile.risk.sex)?.[1],
                      profile.risk?.smokingStatus && `${SMOKING_OPTIONS.find(([v]) => v === profile.risk.smokingStatus)?.[1]} smoker`,
                      ...KNOWN.filter(([k]) => profile.risk?.[k] === true).map(([, label]) => label.split(" (")[0]),
                    ]
                      .filter(Boolean)
                      .join(", ") || "Not added"}
                  </dd>
                </div>
                <div>
                  <dt>Labs</dt>
                  <dd>
                    {LABS.filter(([k]) => profile.risk?.[k] != null)
                      .map(([k, label, unit]) => `${label.split(" ")[0]} ${profile.risk[k]} ${unit}`)
                      .join(", ") || "Not added"}
                  </dd>
                </div>
                <div>
                  <dt>Emergency contact</dt>
                  <dd>
                    {contact ? (
                      <>
                        {contact.name}
                        {contact.relation && ` (${contact.relation})`}
                        <a className="inline-link" href={`tel:${contact.phone.replace(/[^\d+]/g, "")}`}>
                          {contact.phone}
                        </a>
                      </>
                    ) : (
                      "Not added"
                    )}
                  </dd>
                </div>
              </dl>
            </section>
          )}

          {!draft && (
            <section className="profile-section" aria-labelledby="profile-team">
              <h3 id="profile-team" className="sheet-sub">
                Care team
              </h3>
              <CareTeamList />
              <p className="autobook-note">Your care team is kept up to date by your clinics.</p>
            </section>
          )}
        </div>
      )}
    </dialog>
  );
}
