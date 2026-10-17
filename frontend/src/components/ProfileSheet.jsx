import { useEffect, useRef } from "react";
import { careTeam, patient } from "../data/placeholder.js";
import { ageFrom } from "../utils/dates.js";
import { CareTeamList } from "./CareTeamSheet.jsx";

// Read-only health profile behind the avatar menu: who the user is, what
// clinicians need to know at a glance, and their care team.
export default function ProfileSheet({ open, onClose }) {
  const ref = useRef(null);

  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const born = new Date(`${patient.dateOfBirth}T00:00`).toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
  const contact = patient.emergencyContact;
  const familyDoctor = careTeam.find((m) => m.primary);

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
                {patient.initials}
              </span>
              <div>
                <h2 id="profile-title" className="book-title">
                  {patient.fullName}
                </h2>
                <p className="book-sub">
                  Age {ageFrom(patient.dateOfBirth)} · Born {born}
                </p>
              </div>
            </div>
            <button className="sheet-close" aria-label="Close" onClick={onClose}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </header>

          <section className="profile-section" aria-labelledby="profile-about">
            <h3 id="profile-about" className="sheet-sub">
              About you
            </h3>
            <dl className="sheet-rows">
              <div>
                <dt>Health card</dt>
                <dd>Ending {patient.healthCardLast4}</dd>
              </div>
              <div>
                <dt>Allergies</dt>
                <dd>{patient.allergies.length ? patient.allergies.join(", ") : "None recorded"}</dd>
              </div>
              <div>
                <dt>Conditions</dt>
                <dd>{patient.conditions.length ? patient.conditions.join(", ") : "None recorded"}</dd>
              </div>
              {contact && (
                <div>
                  <dt>Emergency contact</dt>
                  <dd>
                    {contact.name} ({contact.relation})
                    <a className="inline-link" href={`tel:${contact.phone.replace(/[^\d+]/g, "")}`}>
                      {contact.phone}
                    </a>
                  </dd>
                </div>
              )}
            </dl>
          </section>

          <section className="profile-section" aria-labelledby="profile-team">
            <h3 id="profile-team" className="sheet-sub">
              Care team
            </h3>
            <CareTeamList />
          </section>

          {familyDoctor && (
            <p className="autobook-note">To change these details, contact {familyDoctor.clinic}.</p>
          )}
        </div>
      )}
    </dialog>
  );
}
