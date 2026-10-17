import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useProfile } from "../context/ProfileContext.jsx";
import { ageFrom } from "../utils/dates.js";
import ProfileSheet from "./ProfileSheet.jsx";
import CareTeamSheet from "./CareTeamSheet.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import { useSession } from "../context/SessionContext.jsx";

// Avatar button and its menu: health profile, care team, and clearing what
// this browser has saved (chats, bookings, readings, settings).
const STORAGE_PREFIX = "caresync-";

function clearDeviceData() {
  try {
    Object.keys(localStorage)
      .filter((k) => k.startsWith(STORAGE_PREFIX))
      .forEach((k) => localStorage.removeItem(k));
  } catch {
    /* storage unavailable: nothing was saved */
  }
  // Every store reads storage when it starts, so a reload starts fresh.
  window.location.reload();
}

const Icon = ({ children }) => (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    {children}
  </svg>
);

export default function AccountMenu() {
  const [open, setOpen] = useState(false);
  const [sheet, setSheet] = useState(null); // "profile" | "care" | "clear"
  const wrapRef = useRef(null);
  const btnRef = useRef(null);
  const { pathname } = useLocation();
  const { session, signedIn, signOut, requestSignIn } = useSession();
  const { profile: patient } = useProfile();

  useEffect(() => setOpen(false), [pathname]);

  // Close on outside click or Esc.
  useEffect(() => {
    if (!open) return;
    const onPointer = (e) => {
      if (!wrapRef.current?.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      btnRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const choose = (next) => {
    setOpen(false);
    setSheet(next);
  };

  // The menu item that opened the sheet is gone, so focus returns to the avatar.
  const closeSheet = () => {
    setSheet(null);
    btnRef.current?.focus();
  };

  return (
    <div className="account-wrap" ref={wrapRef}>
      <button
        ref={btnRef}
        type="button"
        className="avatar"
        aria-label={signedIn ? `Account menu for ${patient.fullName}` : "Account menu, browsing as a guest"}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {signedIn ? (
          patient.initials
        ) : (
          <Icon>
            <circle cx="12" cy="8" r="4" />
            <path d="M4 21a8 8 0 0116 0" />
          </Icon>
        )}
      </button>

      {/* Guests: what signing in gives them, and the way back to the sign-in pop-up */}
      {open && !signedIn && (
        <div className="account-menu" role="menu" aria-label="Account">
          <div className="account-head" role="presentation">
            <strong>Guest</strong>
            <small>Sign in to see your own health record, bookings and reminders.</small>
          </div>
          <button
            type="button"
            role="menuitem"
            autoFocus
            onClick={() => {
              setOpen(false);
              requestSignIn();
            }}
          >
            <Icon>
              <path d="M9 4H6a2 2 0 00-2 2v12a2 2 0 002 2h3M14 8l4 4-4 4M18 12H8" />
            </Icon>
            Sign in
          </button>
          <div className="account-sep" role="separator" />
          <button type="button" role="menuitem" className="danger" onClick={() => choose("clear")}>
            <Icon>
              <path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" />
            </Icon>
            Clear data on this device
          </button>
        </div>
      )}

      {open && signedIn && (
        <div className="account-menu" role="menu" aria-label="Account">
          <div className="account-head" role="presentation">
            <strong>{patient.fullName}</strong>
            <small>{session?.email ?? `Signed in with ${session?.method === "apple" ? "Apple" : "Google"}`}</small>
            <small>
              Age {ageFrom(patient.dateOfBirth)} · Health card ending {patient.healthCardLast4}
            </small>
          </div>
          <button type="button" role="menuitem" autoFocus onClick={() => choose("profile")}>
            <Icon>
              <circle cx="12" cy="8" r="4" />
              <path d="M4 21a8 8 0 0116 0" />
            </Icon>
            Health profile
          </button>
          <button type="button" role="menuitem" onClick={() => choose("care")}>
            <Icon>
              <circle cx="9" cy="8" r="3.5" />
              <path d="M2.5 20a6.5 6.5 0 0113 0M16 4.5a3.5 3.5 0 010 7M18 14a6.5 6.5 0 013.5 6" />
            </Icon>
            Care team
          </button>
          <div className="account-sep" role="separator" />
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              signOut();
            }}
          >
            <Icon>
              <path d="M15 4h3a2 2 0 012 2v12a2 2 0 01-2 2h-3M10 8l-4 4 4 4M6 12h10" />
            </Icon>
            Sign out
          </button>
          <button type="button" role="menuitem" className="danger" onClick={() => choose("clear")}>
            <Icon>
              <path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" />
            </Icon>
            Clear data on this device
          </button>
        </div>
      )}

      <ProfileSheet open={sheet === "profile"} onClose={closeSheet} />
      <CareTeamSheet open={sheet === "care"} onClose={closeSheet} />
      <ConfirmDialog
        open={sheet === "clear"}
        title="Clear data on this device?"
        description="This removes the chats, bookings, logged readings and settings saved in this browser. It can't be undone."
        confirmLabel="Clear data"
        danger
        onConfirm={clearDeviceData}
        onClose={closeSheet}
      />
    </div>
  );
}
