import { createContext, useContext, useEffect, useState } from "react";
import { DEMO_ACCOUNT, SIGNED_OUT_EVENT, getToken, login, loginWithGoogle, logout, setToken, signup } from "../api/client.js";
import useStoredState from "../hooks/useStoredState.js";

// Who is using the app. Until there is a session the landing page shows, and
// its "Log in" / "Sign up" buttons open the sign-in pop-up. The session is
// remembered in this browser. Two kinds:
//   signed in - their own account; the app shows their personal health data
//   guest     - anonymous; can browse, nothing is tailored to them
// Signing in checks the email and password with /api/auth and keeps the
// returned token; Google sign-in (useGoogleSignIn) sends Google's ID token to
// /api/auth/google. Apple isn't offered.
const SessionContext = createContext(null);

export function SessionProvider({ children }) {
  const [session, setSession] = useStoredState("caresync-session", null);
  // Which pop-up the landing page shows: null | "login" | "signup". Not saved.
  const [authPrompt, setAuthPrompt] = useState(null);

  // A signed-in session without a token can't load anything: sign in again.
  // The other way round, a token left behind without a signed-in session (storage
  // out of step) must not be sent on a guest's requests: drop it.
  useEffect(() => {
    if (session && !session.guest && !getToken()) setSession(null);
    if ((!session || session.guest) && getToken()) setToken(null);
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
    const creds = method === "demo" ? DEMO_ACCOUNT : { email, password };
    const res = newAccount && method !== "demo" ? await signup(creds.email, creds.password) : await login(creds.email, creds.password);
    setToken(res.access_token);
    setAuthPrompt(null);
    setSession({ method, email: res.email ?? creds.email, userId: res.user_id, signedInAt: Date.now() });
  };

  // Google Identity Services handed back an ID token (useGoogleSignIn). The
  // backend verifies it and answers with the account's email (shown in the
  // account menu); the token itself is never trusted here.
  const signInWithGoogle = async (credential) => {
    const res = await loginWithGoogle(credential);
    setToken(res.access_token);
    setAuthPrompt(null);
    setSession({ method: "google", email: res.email ?? null, userId: res.user_id, signedInAt: Date.now() });
  };

  const continueAsGuest = () => {
    setToken(null);
    setAuthPrompt(null);
    setSession({ guest: true, startedAt: Date.now() });
  };

  // Back to the landing page. The server ends this sign-in so the token can't
  // be used again (best effort: the local sign-out happens either way), and
  // Google's One Tap stops picking the account automatically, or it would sign
  // straight back in.
  const signOut = () => {
    if (getToken()) logout().catch(() => {});
    window.google?.accounts?.id?.disableAutoSelect();
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
        signInWithGoogle,
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
