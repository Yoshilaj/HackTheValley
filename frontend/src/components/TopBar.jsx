import { useEffect, useState } from "react";
import { getHealth } from "../api/client.js";
import { navItems, patient } from "../data/placeholder.js";

export default function TopBar() {
  const [active, setActive] = useState(navItems[0]);
  const [online, setOnline] = useState(false);

  useEffect(() => {
    getHealth()
      .then(() => setOnline(true))
      .catch(() => setOnline(false));
  }, []);

  return (
    <header className="topbar">
      <div className="logo">
        <span className="logo-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </span>
        <span>
          care<b>sync</b>
        </span>
      </div>

      <nav className="pill-nav" aria-label="Main">
        {navItems.map((item) => (
          <button
            key={item}
            className={item === active ? "active" : ""}
            aria-current={item === active ? "page" : undefined}
            onClick={() => setActive(item)}
          >
            {item}
          </button>
        ))}
      </nav>

      <div className="top-actions">
        <span className={`status-pill ${online ? "on" : ""}`} role="status">
          <span className="status-dot" aria-hidden="true" />
          {online ? "Connected" : "Offline"}
        </span>
        <button className="icon-btn" aria-label="Search">
          <svg viewBox="0 0 24 24">
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.5-3.5" />
          </svg>
        </button>
        <button className="icon-btn" aria-label="Notifications">
          <svg viewBox="0 0 24 24">
            <path d="M6 17V11a6 6 0 1112 0v6l2 2H4z" />
            <path d="M10 21h4" />
          </svg>
        </button>
        <div className="avatar" role="img" aria-label={`Signed in as ${patient.name}`}>
          {patient.initials}
        </div>
      </div>
    </header>
  );
}
