import { createContext, useContext, useEffect, useState } from "react";
import { DEMO_ACCOUNT, SIGNED_OUT_EVENT, USE_BACKEND, getToken, login, setToken, signup } from "../api/client.js";
import useStoredState from "../hooks/useStoredState.js";

// Who is using the app. Until there is a session the landing page shows, and
// its "Log in" / "Sign up" buttons open the sign-in pop-up. The session is
// remembered in this browser. Two kinds:
//   signed in - their own account; the app shows their personal health data
//   guest     - anonymous; can browse, nothing is tailored to them
// With the backend on (USE_BACKEND), signing in checks the email and password
// with /api/auth and keeps the returned token; Google and Apple aren't offered
// because the backend has no sign-in for them. With it off, signing in just
// records the email or provider and the app shows the demo patient.
const SessionContext = createContext(null);

export function SessionProvider({ children }) {
  const [session, setSession] = useStoredState("caresync-session", null);
  // Which pop-up the landing page shows: null | "login" | "signup". Not saved.
  const [authPrompt, setAuthPrompt] = useState(null);

  // A sign-in saved before the backend was switched on has no token, so every
  // backend call would fail and the dashboard would stay empty: sign in again.
  useEffect(() => {
    if (USE_BACKEND && session && !session.guest && !getToken()) setSession(null);
  }, []); // once, on load

  // The backend rejected the saved token (expired or revoked): back to the landing page.
  useEffect(() => {
    const onSignedOut = () => setSession(null);
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut);
  }, [setSession]);

  // method: "email" | "google" | "apple" | "demo" (dev login). Throws if the
  // backend refuses (wrong password, email taken), with the HTTP status on the error.
  const signIn = async (method, email, password, { newAccount = false } = {}) => {
    if (USE_BACKEND) {
      const creds = method === "demo" ? DEMO_ACCOUNT : { email, password };
      const res = newAccount && method !== "demo" ? await signup(creds.email, creds.password) : await login(creds.email, creds.password);
      setToken(res.access_token);
      setAuthPrompt(null);
      setSession({ method, email: creds.email, userId: res.user_id, signedInAt: Date.now() });
      return;
    }
    setAuthPrompt(null);
    setSession({ method, email: email ?? null, signedInAt: Date.now() });
  };

  const continueAsGuest = () => {
    setToken(null);
    setAuthPrompt(null);
    setSession({ guest: true, startedAt: Date.now() });
  };

  // Back to the landing page.
  const signOut = () => {
    setToken(null);
    setAuthPrompt(null);
    setSession(null);
  };

  // A guest choosing to sign in: back to the landing page with the pop-up open.
  const requestSignIn = () => {
    setAuthPrompt("login");
    setSession(null);
  };

  // Personal data shows only when signed in (not to guests, nor on the landing page).
  const signedIn = Boolean(session && !session.guest);

  return (
    <SessionContext.Provider
      value={{
        session,
        signedIn,
        isGuest: !signedIn,
        signIn,
        continueAsGuest,
        signOut,
        requestSignIn,
        authPrompt,
        openAuth: setAuthPrompt,
        closeAuth: () => setAuthPrompt(null),
      }}
    >
      {children}
    </SessionContext.Provider>
  );
}

export function useSession() {
  return useContext(SessionContext);
}
