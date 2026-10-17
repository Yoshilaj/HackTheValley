import { Link, NavLink } from "react-router-dom";
import { navItems, patient } from "../data/placeholder.js";

export default function TopBar() {
  return (
    <header className="topbar">
      <Link to="/" className="logo" aria-label="CareSync home">
        <span className="logo-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </span>
        <span>
          care<b>sync</b>
        </span>
      </Link>

      <nav className="pill-nav" aria-label="Main">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) => (isActive ? "active" : "")}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="top-actions">
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
