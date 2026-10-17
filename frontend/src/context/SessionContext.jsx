import { createContext, useContext, useState } from "react";
import { signInWithEmail } from "../api/client.js";
import useStoredState from "../hooks/useStoredState.js";

// Who is using the app. Until there is a session the landing page shows, and
// its "Log in" / "Sign up" buttons open the sign-in pop-up. The session is
// remembered in this browser. Two kinds:
//   signed in - their own account; the app shows their personal health data
//               (for now always the demo patient, Micky Larson)
//   guest     - anonymous; can browse and chat, nothing is tailored to them
// Until the backend's auth exists (flip USE_BACKEND), signing in just records
// the email or provider - there is no real account check.
const USE_BACKEND = false;

const SessionContext = createContext(null);

export function SessionProvider({ children }) {
  const [session, setSession] = useStoredState("caresync-session", null);
  // Which pop-up the landing page shows: null | "login" | "signup". Not saved.
  const [authPrompt, setAuthPrompt] = useState(null);

  // method: "email" | "google" | "apple" | "demo" (dev login). Throws if the backend refuses.
  const signIn = async (method, email, { newAccount = false } = {}) => {
    if (USE_BACKEND && method !== "demo") {
      if (method !== "email") {
        // OAuth redirect; the backend returns here signed in
        window.location.assign(`/api/auth/${method}${newAccount ? "?signup=1" : ""}`);
        return;
      }
      await signInWithEmail(email, newAccount);
    }
    setAuthPrompt(null);
    setSession({ method, email: email ?? null, signedInAt: Date.now() });
  };

  const continueAsGuest = () => {
    setAuthPrompt(null);
    setSession({ guest: true, startedAt: Date.now() });
  };

  // Back to the landing page.
  const signOut = () => {
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
