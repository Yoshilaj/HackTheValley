import { useEffect, useRef, useState } from "react";
import { useSession } from "../context/SessionContext.jsx";
import HeartDropLogo from "./HeartDropLogo.jsx";

// Sign-in pop-up over the landing page, opened by its "Log in" or "Sign up"
// buttons: email, Google or Apple for your own account, or continue as a
// guest (nothing tailored). In dev builds a "Demo login" signs straight in as
// the demo patient. Esc, the close button or a click outside returns to the
// landing page. Native <dialog> like DetailSheet.
const DEMO_EMAIL = "micky.larson@example.com";
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const COPY = {
  login: {
    title: "Welcome back",
    sub: "Log in to see your health, appointments and care team.",
    submit: "Continue",
    provider: "Continue with",
    switchText: "New to CareSync?",
    switchLabel: "Create an account",
  },
  signup: {
    title: "Create your account",
    sub: "Your readings, medications and visits in one place, with an assistant that knows your history.",
    submit: "Create account",
    provider: "Sign up with",
    switchText: "Already have an account?",
    switchLabel: "Log in",
  },
};

export default function LoginDialog() {
  const { session, signIn, continueAsGuest, authPrompt, openAuth, closeAuth } = useSession();
  const ref = useRef(null);
  const inputRef = useRef(null);
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(null); // null | "email" | "google" | "apple" | "demo"
  const open = !session && authPrompt !== null;
  const mode = authPrompt === "signup" ? "signup" : "login";
  const copy = COPY[mode];

  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog.open) {
      setError("");
      setBusy(null);
      dialog.showModal();
      inputRef.current?.focus();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const go = async (method) => {
    if (method === "email" && !EMAIL.test(email.trim())) {
      setError(email.trim() ? "Enter a full email address, like name@example.com." : "Enter your email address.");
      inputRef.current?.focus();
      return;
    }
    setError("");
    setBusy(method);
    try {
      await signIn(method, method === "email" ? email.trim() : method === "demo" ? DEMO_EMAIL : undefined, {
        newAccount: mode === "signup",
      });
    } catch {
      setError(
        mode === "signup"
          ? "Couldn't create your account. Check your connection and try again."
          : "Couldn't sign you in. Check your connection and try again."
      );
      setBusy(null);
    }
  };

  const switchMode = () => {
    setError("");
    openAuth(mode === "signup" ? "login" : "signup");
    inputRef.current?.focus();
  };

  const working = busy === "demo" || mode === "login" ? "Signing in…" : "Creating account…";

  return (
    <dialog
      ref={ref}
      className="login-dialog"
      aria-labelledby="login-title"
      aria-describedby="login-sub"
      onCancel={(e) => {
        e.preventDefault();
        closeAuth();
      }}
      onClick={(e) => e.target === ref.current && closeAuth()}
    >
      <form
        className="login-body"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          go("email");
        }}
      >
        <div className="login-top">
          {/* Same looping heart drop as the landing page logos */}
          <span className="login-logo">
            <HeartDropLogo className="playing" />
          </span>
          <button type="button" className="sheet-close" onClick={closeAuth} aria-label="Close">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        <div>
          <h2 id="login-title" className="login-title">
            {copy.title}
          </h2>
          <p id="login-sub" className="login-sub">
            {copy.sub}
          </p>
        </div>

        <div className="form-field">
          <label htmlFor="login-email">Email</label>
          <input
            ref={inputRef}
            id="login-email"
            className="text-input login-input"
            type="email"
            autoComplete="email"
            placeholder="name@example.com"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              setError("");
            }}
            aria-invalid={Boolean(error) || undefined}
            aria-describedby={error ? "login-error" : undefined}
          />
          {error && (
            <p id="login-error" className="field-error" role="alert">
              {error}
            </p>
          )}
        </div>

        <button type="submit" className="btn-primary login-continue" disabled={busy !== null}>
          {busy === "email" ? working : copy.submit}
        </button>

        <p className="login-or" aria-hidden="true">
          <span>or</span>
        </p>

        <button type="button" className="login-provider" onClick={() => go("google")} disabled={busy !== null}>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5a5.6 5.6 0 01-2.4 3.6v3h3.9c2.3-2.1 3.5-5.2 3.5-8.8z" />
            <path fill="#34A853" d="M12 24c3.2 0 6-1.1 8-2.9l-3.9-3c-1.1.7-2.5 1.2-4.1 1.2-3.1 0-5.8-2.1-6.7-5H1.3v3.1A12 12 0 0012 24z" />
            <path fill="#FBBC05" d="M5.3 14.3a7.2 7.2 0 010-4.6V6.6H1.3a12 12 0 000 10.8l4-3.1z" />
            <path fill="#EA4335" d="M12 4.8c1.8 0 3.4.6 4.6 1.8l3.4-3.4A12 12 0 001.3 6.6l4 3.1c.9-2.9 3.6-4.9 6.7-4.9z" />
          </svg>
          {busy === "google" ? working : `${copy.provider} Google`}
        </button>
        <button type="button" className="login-provider" onClick={() => go("apple")} disabled={busy !== null}>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              fill="currentColor"
              d="M16.4 12.7c0-2.6 2.1-3.8 2.2-3.9a4.8 4.8 0 00-3.8-2c-1.6-.2-3.1.9-3.9.9-.8 0-2-.9-3.3-.9a4.9 4.9 0 00-4.2 2.5c-1.8 3.1-.5 7.7 1.3 10.2.8 1.2 1.8 2.6 3.1 2.5 1.3-.1 1.7-.8 3.3-.8 1.5 0 1.9.8 3.3.8 1.3 0 2.2-1.2 3-2.5a10 10 0 001.4-2.8 4.4 4.4 0 01-2.4-4zM13.9 5.1A4.4 4.4 0 0015 1.9a4.6 4.6 0 00-3 1.5 4.2 4.2 0 00-1.1 3.1 3.8 3.8 0 003-1.4z"
            />
          </svg>
          {busy === "apple" ? working : `${copy.provider} Apple`}
        </button>

        <p className="login-switch">
          {copy.switchText}{" "}
          <button type="button" className="login-switch-btn" onClick={switchMode} disabled={busy !== null}>
            {copy.switchLabel}
          </button>
        </p>

        <div className="login-guest">
          <button type="button" className="login-guest-btn" onClick={continueAsGuest} disabled={busy !== null}>
            Continue as guest
          </button>
          <p>Browse and chat without an account. Nothing is tailored to you.</p>
        </div>

        {/* Dev builds only: one click into the demo patient's account */}
        {import.meta.env.DEV && (
          <button type="button" className="login-dev" onClick={() => go("demo")} disabled={busy !== null}>
            <span className="login-dev-tag">Dev</span>
            {busy === "demo" ? "Signing in…" : "Demo login · Micky Larson"}
          </button>
        )}
      </form>
    </dialog>
  );
}
