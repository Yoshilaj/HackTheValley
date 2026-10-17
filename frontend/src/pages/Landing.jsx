import { useEffect, useRef } from "react";
import HeartDropLogo from "../components/HeartDropLogo.jsx";
import LoginDialog from "../components/LoginDialog.jsx";
import { useSession } from "../context/SessionContext.jsx";
import "../styles/landing.css";

// Front door shown until there is a session: what CareSync does, with
// "Log in" and "Sign up" in the top right opening the sign-in pop-up.
// Signing in or continuing as a guest swaps this for the dashboard (App.jsx).
const FEATURES = [
  {
    title: "Know your risk",
    text: "One clear score, plus your chances for stroke and heart disease, Alzheimer's and Parkinson's, explained in plain words.",
    icon: <path d="M3 12h4l2-5 4 10 2-5h6" />,
  },
  {
    title: "Book the right visit",
    text: "Tell CareSync when you're free and how far you'll travel. It suggests nearby appointments and books with one tap.",
    icon: (
      <>
        <rect x="4" y="5" width="16" height="15" rx="2" />
        <path d="M8 3v4M16 3v4M4 10h16" />
      </>
    ),
  },
  {
    title: "Stay on top of medications",
    text: "Today's doses at a glance, refill reminders before you run out, and what to watch for with each one.",
    icon: (
      <>
        <rect x="3" y="9" width="18" height="6" rx="3" transform="rotate(-45 12 12)" />
        <path d="M9.5 9.5l5 5" />
      </>
    ),
  },
  {
    title: "Ask your care assistant",
    text: "Questions about a reading, a visit or a prescription, answered with your own history in mind.",
    icon: <path d="M5 18l-1.5 3.5L8 20a9 9 0 10-3-2z" />,
  },
];

const STEPS = [
  { title: "Create your account", text: "Sign up with email, Google or Apple. It takes a minute." },
  { title: "Add your health", text: "Log readings, list your medications and chat about how you're feeling." },
  { title: "Get a plan", text: "See where you stand, and book follow-ups when something needs a closer look." },
];

export default function Landing() {
  const { openAuth, continueAsGuest } = useSession();

  const rootRef = useRef(null);

  useEffect(() => {
    document.title = "CareSync | Your health, in sync";
    window.scrollTo(0, 0);
  }, []);

  // Scroll reveal: anything marked data-reveal fades and rises in the first
  // time it scrolls into view; --i staggers items within a group. Content is
  // only hidden once this runs (reveal-ready), and never with reduced motion.
  useEffect(() => {
    const root = rootRef.current;
    if (!("IntersectionObserver" in window) || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    root.classList.add("reveal-ready");
    const io = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (!e.isIntersecting) return;
          e.target.classList.add("revealed");
          io.unobserve(e.target);
        }),
      { rootMargin: "0px 0px -8% 0px", threshold: 0.12 }
    );
    root.querySelectorAll("[data-reveal]").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);

  return (
    <div className="landing" id="top" ref={rootRef}>
      <p className="landing-banner">
        <strong>New:</strong> risk check-ins for stroke, Alzheimer's and Parkinson's. <a href="#features">Learn more</a>
      </p>

      <header className="landing-nav">
        <a href="#top" className="landing-brand" aria-label="CareSync home">
          {/* Same heart drop as the footer logo, playing from page load */}
          <HeartDropLogo className="playing" />
          <span>CareSync</span>
        </a>
        <nav className="landing-links" aria-label="Sections">
          <a href="#features">Features</a>
          <a href="#how">How it works</a>
          <a href="#privacy">Privacy</a>
        </nav>
        <div className="landing-auth">
          <button type="button" className="landing-login" onClick={() => openAuth("login")} aria-haspopup="dialog">
            Log in
          </button>
          <button type="button" className="landing-signup" onClick={() => openAuth("signup")} aria-haspopup="dialog">
            Sign up
          </button>
        </div>
      </header>

      <main>
        <section className="landing-hero" aria-labelledby="hero-title">
          <span className="landing-pill" data-reveal style={{ "--i": 0 }}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path className="pill-star" d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />
              <path className="pill-glint" d="M19 15.5l.6 1.9 1.9.6-1.9.6-.6 1.9-.6-1.9-1.9-.6 1.9-.6z" />
            </svg>
            Care assistant
          </span>
          <h1 id="hero-title" className="landing-title" data-reveal style={{ "--i": 1 }}>
            Your health,
            <br />
            <span className="landing-accent">in sync.</span>
          </h1>
          <p className="landing-lede" data-reveal style={{ "--i": 2 }}>
            Readings, medications and appointments in one place, with an assistant that knows your history and helps
            you act early.
          </p>
          <div className="landing-ctas" data-reveal style={{ "--i": 3 }}>
            <button type="button" className="btn-primary landing-cta" onClick={() => openAuth("signup")} aria-haspopup="dialog">
              Get started free
            </button>
            <button type="button" className="btn-secondary landing-cta" onClick={continueAsGuest}>
              Continue as guest
            </button>
          </div>
        </section>

        {/* Illustration of the dashboard, not live data */}
        <figure className="landing-peek">
          <figcaption className="sr-only">Preview of the CareSync dashboard</figcaption>
          <div className="peek-frame" aria-hidden="true" data-reveal>
            <div className="peek-risk">
              <small>Overall risk</small>
              <strong>14</strong>
              <span>Low · down 2 since last month</span>
            </div>
            <div className="peek-stack">
              <div className="peek-card">
                <small>Next visit</small>
                <strong>Annual check-up</strong>
                <span>Tue · 9:30 AM · 1.2 km away</span>
              </div>
              <div className="peek-card">
                <small>Today</small>
                <strong>2 of 3 doses taken</strong>
                <span className="peek-bar">
                  <i />
                </span>
              </div>
            </div>
            <div className="peek-chat">
              <p className="peek-msg user">Is my blood pressure okay?</p>
              <p className="peek-msg">
                Your last 7 readings average 128/82, a little above your goal. Want me to find a time to check in with
                your doctor?
              </p>
            </div>
          </div>
        </figure>

        <section id="features" className="landing-section" aria-labelledby="features-title">
          <h2 id="features-title" className="landing-h2" data-reveal>
            Everything about your care, together
          </h2>
          <ul className="landing-features">
            {FEATURES.map((f, i) => (
              <li key={f.title} className="landing-feature" data-reveal style={{ "--i": i }}>
                <span className="landing-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24">{f.icon}</svg>
                </span>
                <h3>{f.title}</h3>
                <p>{f.text}</p>
              </li>
            ))}
          </ul>
        </section>

        <section id="how" className="landing-section" aria-labelledby="how-title">
          <h2 id="how-title" className="landing-h2" data-reveal>
            How it works
          </h2>
          <ol className="landing-steps">
            {STEPS.map((s, i) => (
              <li key={s.title} data-reveal style={{ "--i": i }}>
                <span className="landing-step-num" aria-hidden="true">
                  {i + 1}
                </span>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
              </li>
            ))}
          </ol>
        </section>

        <section id="privacy" className="landing-section landing-privacy" aria-labelledby="privacy-title" data-reveal>
          <span className="landing-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z" />
              <path d="M9 12l2 2 4-4" />
            </svg>
          </span>
          <div>
            <h2 id="privacy-title" className="landing-h2">
              Private by default
            </h2>
            <p>
              Your information is used only to support your care. Browse as a guest without an account, and clear what's
              saved on this device any time.
            </p>
          </div>
        </section>

        <section className="landing-final" aria-labelledby="final-title">
          <h2 id="final-title" className="landing-h2" data-reveal>
            Ready when you are.
          </h2>
          <button
            type="button"
            className="btn-primary landing-cta"
            onClick={() => openAuth("signup")}
            aria-haspopup="dialog"
            data-reveal
            style={{ "--i": 1 }}
          >
            Create your account
          </button>
        </section>
      </main>

      {/* Branded footer: link columns, the large logo and wordmark over soft
          blue hills, then the copyright line. Every link goes somewhere real. */}
      <footer className="landing-footer">
        <div className="footer-inner">
          <div className="footer-cols">
            <div>
              <h2>Product</h2>
              <ul>
                <li><a href="#features">Features</a></li>
                <li><a href="#how">How it works</a></li>
                <li><a href="#privacy">Privacy</a></li>
              </ul>
            </div>
            <div>
              <h2>Account</h2>
              <ul>
                <li>
                  <button type="button" onClick={() => openAuth("login")} aria-haspopup="dialog">
                    Log in
                  </button>
                </li>
                <li>
                  <button type="button" onClick={() => openAuth("signup")} aria-haspopup="dialog">
                    Sign up
                  </button>
                </li>
                <li>
                  <button type="button" onClick={continueAsGuest}>
                    Continue as guest
                  </button>
                </li>
              </ul>
            </div>
            <div className="footer-note">
              <h2>Good to know</h2>
              <ul>
                <li>CareSync isn't a substitute for medical advice.</li>
                <li>In an emergency, call your local emergency number.</li>
                <li>Clear what's saved on this device any time.</li>
              </ul>
            </div>
          </div>

          <div className="footer-brand" aria-hidden="true" data-reveal>
            {/* The heart drops in on a loop; little hearts burst out as it lands */}
            <HeartDropLogo />
            <span>CareSync</span>
          </div>

          <div className="footer-bottom">
            <p>© {new Date().getFullYear()} CareSync. All rights reserved.</p>
            <a href="#top">Back to top</a>
          </div>
        </div>
      </footer>

      <LoginDialog />
    </div>
  );
}
