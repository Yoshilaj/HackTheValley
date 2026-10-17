import { useEffect, useRef, useState } from "react";
import { useProfile } from "../context/ProfileContext.jsx";
import { ageFrom } from "../utils/dates.js";
import { CareTeamList } from "./CareTeamSheet.jsx";

// Health profile behind the avatar menu: who the user is, what clinicians need
// to know at a glance, and their care team. "Edit" turns the details into a
// form: change the name, birth date or health card, add or remove allergies
// and conditions, and add, change or remove the emergency contact. Nothing is
// saved until "Save"; Cancel or closing the sheet discards the draft.
const PHONE = /^[\d\s()+.-]{7,}$/;
const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local

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
  const c = d.emergencyContact;
  if (c) {
    if (!c.name.trim()) errors.contactName = "Enter your contact's name.";
    if (!PHONE.test(c.phone.trim())) errors.contactPhone = "Enter a phone number, like (416) 555-0187.";
  }
  return errors;
}

// Editable list (allergies, conditions): each item has a remove button, and
// the box below adds a new one with Enter or the Add button.
function ListEditor({ id, label, items, onChange, text, onText, placeholder }) {
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
    });
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
    } catch {
      setSaveError("Couldn't save your changes. Check your connection and try again.");
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
                  {draft ? "Changes apply once you save." : `Age ${ageFrom(profile.dateOfBirth)}${born ? ` · Born ${born}` : ""}`}
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
