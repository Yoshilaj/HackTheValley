import { useSession } from "../context/SessionContext.jsx";

// Stands in for a personal section while browsing as a guest: says what
// signing in unlocks, with a button back to the sign-in pop-up.
export default function GuestGate({ title, children }) {
  const { requestSignIn } = useSession();
  return (
    <section className="card guest-gate">
      <span className="guest-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <rect x="5" y="10.5" width="14" height="10" rx="2.5" />
          <path d="M8 10.5V8a4 4 0 018 0v2.5" />
        </svg>
      </span>
      <h2>{title}</h2>
      <p>{children}</p>
      <button type="button" className="btn-primary" onClick={requestSignIn} aria-haspopup="dialog">
        Sign in
      </button>
    </section>
  );
}
