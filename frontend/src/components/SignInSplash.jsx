import { useEffect } from "react";
import HeartDropLogo from "./HeartDropLogo.jsx";

// Brief logo moment between the landing page and the dashboard: the heart
// drops gently into the still hand, little hearts burst out as it lands, then
// the cover fades away. About 1 s, purely decorative (the dashboard is already
// mounted beneath it). Plays once: no "playing" class, so not the landing loop.
const DURATION = 1100;

export default function SignInSplash({ onDone }) {
  useEffect(() => {
    const t = setTimeout(onDone, DURATION);
    return () => clearTimeout(t);
  }, [onDone]);

  return (
    <div className="signin-splash" aria-hidden="true">
      <HeartDropLogo />
    </div>
  );
}
