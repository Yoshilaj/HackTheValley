import { useEffect, useRef } from "react";
import { careTeam } from "../data/placeholder.js";

// The user's doctors with call and email actions. CareTeamList is shared with
// the health profile; CareTeamSheet is the glass sheet behind "Contact care team".
const digits = (phone) => phone.replace(/[^\d+]/g, "");
const initialsOf = (name) =>
  name
    .replace(/^Dr\.?\s+/i, "")
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

export function CareTeamList({ members = careTeam }) {
  return (
    <ul className="care-team">
      {members.map((m) => (
        <li key={m.id}>
          <span className="appt-icon" aria-hidden="true">
            {initialsOf(m.name)}
          </span>
          <div>
            <p className="care-name">{m.name}</p>
            <p className="book-meta">{[m.role, m.clinic].filter(Boolean).join(" · ")}</p>
          </div>
          <div className="care-actions">
            {m.phone && (
              <a className="icon-action" href={`tel:${digits(m.phone)}`} aria-label={`Call ${m.name}, ${m.phone}`} title={m.phone}>
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M5 4h4l2 5-2.5 1.5a11 11 0 005 5L15 13l5 2v4a2 2 0 01-2 2A16 16 0 013 6a2 2 0 012-2" />
                </svg>
              </a>
            )}
            {m.email && (
              <a className="icon-action" href={`mailto:${m.email}`} aria-label={`Email ${m.name}`} title={m.email}>
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <rect x="3" y="5" width="18" height="14" rx="2" />
                  <path d="M3 7l9 6 9-6" />
                </svg>
              </a>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

export default function CareTeamSheet({ open, onClose }) {
  const ref = useRef(null);

  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="sheet book-sheet"
      aria-labelledby="care-team-title"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      {open && (
        <div className="sheet-body">
          <header className="sheet-head">
            <div>
              <h2 id="care-team-title" className="book-title">
                Your care team
              </h2>
              <p className="book-sub">Call or email your doctors' offices directly.</p>
            </div>
            <button className="sheet-close" aria-label="Close" onClick={onClose}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </header>
          <CareTeamList />
          <p className="autobook-note">In an emergency, call your local emergency number.</p>
        </div>
      )}
    </dialog>
  );
}
