import { useCallback, useEffect, useRef, useState } from "react";
import { USE_BACKEND, getGoogleConfig } from "../api/client.js";
import { useSession } from "../context/SessionContext.jsx";

// Sign in with Google through Google Identity Services: the official button
// (renderButton) and One Tap, Google's account pop-up, which signs a returning
// user straight in (auto_select). Google hands back an ID token; SessionContext
// sends it to /api/auth/google, which finds or creates the account.
// Only with the backend on and GOOGLE_CLIENT_ID set on the backend
// (/api/auth/google/config); otherwise `available` stays false.
const SCRIPT = "https://accounts.google.com/gsi/client";

let configPromise = null; // the backend's { enabled, client_id }, asked once
let scriptPromise = null; // Google's script, loaded once
let initializedFor = null; // client id google.accounts.id was initialized with
let handleCredential = null; // the latest mounted hook's handler

function loadConfig() {
  configPromise ??= getGoogleConfig().catch(() => ({ enabled: false }));
  return configPromise;
}

function loadScript() {
  scriptPromise ??= new Promise((resolve, reject) => {
    if (window.google?.accounts?.id) return resolve();
    const tag = document.createElement("script");
    tag.src = SCRIPT;
    tag.async = true;
    tag.onload = () => resolve();
    tag.onerror = () => {
      scriptPromise = null;
      reject(new Error("Google sign-in script didn't load"));
    };
    document.head.appendChild(tag);
  });
  return scriptPromise;
}

function initialize(clientId) {
  if (initializedFor === clientId) return;
  window.google.accounts.id.initialize({
    client_id: clientId,
    callback: (response) => handleCredential?.(response),
    auto_select: true, // a returning user with one Google session is signed in without a click
    cancel_on_tap_outside: true,
    context: "signin",
    itp_support: true,
    use_fedcm_for_prompt: true,
  });
  initializedFor = clientId;
}

// oneTap: show Google's pop-up (and auto sign-in) while nobody is signed in.
export default function useGoogleSignIn({ oneTap = false } = {}) {
  const { session, signInWithGoogle } = useSession();
  const [clientId, setClientId] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const handler = useRef(null);

  handler.current = async ({ credential }) => {
    setBusy(true);
    setError("");
    try {
      await signInWithGoogle(credential);
    } catch (err) {
      setError(
        err?.status === 503
          ? "Google sign-in isn't available right now. Use your email and password."
          : err?.status === 429
            ? "Too many attempts. Wait a minute and try again."
            : "Couldn't sign you in with Google. Try again, or use your email."
      );
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!USE_BACKEND) return;
    let cancelled = false;
    loadConfig()
      .then((cfg) => (cfg.enabled && cfg.client_id ? loadScript().then(() => cfg.client_id) : null))
      .then((id) => {
        if (cancelled || !id) return;
        initialize(id);
        setClientId(id);
      })
      .catch(() => {
        /* no Google sign-in: email and password still work */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Route Google's answer to this hook while it's mounted.
  useEffect(() => {
    if (!clientId) return;
    const mine = (response) => handler.current?.(response);
    handleCredential = mine;
    return () => {
      if (handleCredential === mine) handleCredential = null;
    };
  }, [clientId]);

  useEffect(() => {
    if (!(oneTap && clientId && !session)) return;
    window.google.accounts.id.prompt((moment) => {
      // Dev builds: say why One Tap didn't show (most often the page's origin isn't among the
      // OAuth client's "Authorized JavaScript origins", or the user dismissed it recently).
      if (import.meta.env.DEV && moment.isNotDisplayed?.()) {
        console.warn(`Google One Tap not shown: ${moment.getNotDisplayedReason?.() ?? "unknown reason"}`);
      }
    });
  }, [oneTap, clientId, session]);

  // Draws Google's own button into `el` ("Continue with Google" / "Sign up with Google").
  // Any earlier button in `el` is removed first, so re-rendering never stacks two.
  const renderButton = useCallback(
    (el, text = "continue_with") => {
      if (!el || !clientId) return;
      el.replaceChildren();
      window.google.accounts.id.renderButton(el, {
        type: "standard",
        theme: "outline",
        size: "large",
        shape: "rectangular",
        text,
        logo_alignment: "center",
        width: Math.min(400, el.clientWidth || 360),
      });
    },
    [clientId]
  );

  return { available: Boolean(clientId), renderButton, error, busy };
}
