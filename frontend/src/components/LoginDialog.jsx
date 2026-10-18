import { useEffect, useRef, useState } from "react";
import { DEMO_ACCOUNT } from "../api/client.js";
import { useSession } from "../context/SessionContext.jsx";
import useGoogleSignIn from "../hooks/useGoogleSignIn.js";
import HeartDropLogo from "./HeartDropLogo.jsx";

// Sign-in pop-up over the landing page, opened by its "Log in" or "Sign up"
// buttons: email and password for your own account, Google's own "Sign in
// with Google" button when the backend has Google set up (useGoogleSignIn; this
// pop-up lives on the landing page, so it also starts Google's One Tap, which
// signs a returning user straight in), or continue as a guest (nothing
// tailored). In dev builds a "Demo login" signs straight in as the demo
// patient. Esc, the close button or a click outside returns to the landing
// page. Native <dialog> like DetailSheet.
const DEMO_EMAIL = DEMO_ACCOUNT.email;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD = 8; // the backend's minimum for new accounts

// What went wrong, in words, from the backend's status code.
function signInError(err, mode) {
  if (err?.status === 401) return "That email and password don't match.";
  if (err?.status === 409) return "That email already has an account. Log in instead.";
  if (err?.status === 422) return "Check your email address and password.";
  if (err?.status === 429) return "Too many attempts. Wait a minute and try again.";
  return mode === "signup"
    ? "Couldn't create your account. Check your connection and try again."
    : "Couldn't sign you in. Check your connection and try again.";
}

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
  const passwordRef = useRef(null);
  const googleRef = useRef(null);
  const google = useGoogleSignIn({ oneTap: true });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldError, setFieldError] = useState(null); // { field: "email" | "password", text }
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(null); // null | "email" | "google" | "apple" | "demo"
  const open = !session && authPrompt !== null;
  const mode = authPrompt === "signup" ? "signup" : "login";
  const copy = COPY[mode];

  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog.open) {
      setError("");
      setFieldError(null);
      setPassword("");
      setBusy(null);
      dialog.showModal();
      inputRef.current?.focus();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const go = async (method) => {
    if (method === "email") {
      if (!EMAIL.test(email.trim())) {
        setFieldError({
          field: "email",
          text: email.trim() ? "Enter a full email address, like name@example.com." : "Enter your email address.",
        });
        inputRef.current?.focus();
        return;
      }
      if (!password || (mode === "signup" && password.length < MIN_PASSWORD)) {
        setFieldError({
          field: "password",
          text: !password ? "Enter your password." : `Use at least ${MIN_PASSWORD} characters.`,
        });
        passwordRef.current?.focus();
        return;
      }
    }
    setFieldError(null);
    setError("");
    setBusy(method);
    try {
      await signIn(method, method === "email" ? email.trim() : method === "demo" ? DEMO_EMAIL : undefined, password, {
        newAccount: mode === "signup",
      });
    } catch (err) {
      setError(signInError(err, mode));
      setBusy(null);
    }
  };

  // Google draws its button into the pop-up each time it opens (and when switching log in / sign up).
  useEffect(() => {
    if (open && google.available) google.renderButton(googleRef.current, mode === "signup" ? "signup_with" : "continue_with");
  }, [open, mode, google.available, google.renderButton]);

  const switchMode = () => {
    setError("");
    setFieldError(null);
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
              setFieldError(null);
              setError("");
            }}
            aria-invalid={fieldError?.field === "email" || undefined}
            aria-describedby={fieldError?.field === "email" ? "login-email-error" : undefined}
          />
          {fieldError?.field === "email" && (
            <p id="login-email-error" className="field-error" role="alert">
              {fieldError.text}
            </p>
          )}
        </div>

        <div className="form-field">
          <label htmlFor="login-password">Password</label>
          <input
            ref={passwordRef}
            id="login-password"
            className="text-input login-input"
            type="password"
            autoComplete={mode === "signup" ? "new-password" : "current-password"}
            placeholder={mode === "signup" ? `At least ${MIN_PASSWORD} characters` : ""}
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              setFieldError(null);
              setError("");
            }}
            aria-invalid={fieldError?.field === "password" || undefined}
            aria-describedby={fieldError?.field === "password" ? "login-password-error" : undefined}
          />
          {fieldError?.field === "password" && (
            <p id="login-password-error" className="field-error" role="alert">
              {fieldError.text}
            </p>
          )}
        </div>

        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}

        <button type="submit" className="btn-primary login-continue" disabled={busy !== null}>
          {busy === "email" ? working : copy.submit}
        </button>

        {/* Google's own button (Apple needs a paid developer account, so it's not offered) */}
        {google.available && (
          <>
            <p className="login-or" aria-hidden="true">
              <span>or</span>
            </p>
            <div ref={googleRef} className="login-google" />
            {google.busy && (
              <p className="login-google-status" role="status">
                Signing in with Google…
              </p>
            )}
            {google.error && (
              <p className="field-error" role="alert">
                {google.error}
              </p>
            )}
          </>
        )}

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
            {busy === "demo" ? "Signing in…" : `Demo login · ${DEMO_EMAIL}`}
          </button>
        )}
      </form>
    </dialog>
  );
}
