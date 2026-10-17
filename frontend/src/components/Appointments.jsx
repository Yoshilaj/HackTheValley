import { Link } from "react-router-dom";
import { appointments } from "../data/placeholder.js";

export default function Appointments() {
  return (
    <div className="card appointments">
      <div className="card-head">
        <h2>
          Upcoming appointments
        </h2>
        <Link to="/appointments" className="see-all">
          See all ›
        </Link>
      </div>

      {appointments.rows.map((t) => (
        <div className="appt-row" key={t.id}>
          <span className="appt-icon" style={{ background: t.color, color: t.ink }} aria-hidden="true">
            {t.glyph}
          </span>
          <span className="appt-name">{t.name}</span>
          <span className={`status ${t.status.toLowerCase()}`}>{t.status}</span>
          <span className="appt-date">{t.date}</span>
          <span className="appt-place">{t.place}</span>
          <span className="appt-amt">{t.amount}</span>
          <button className="dots" aria-label={`More options for ${t.name}`}>⋮</button>
        </div>
      ))}
    </div>
  );
}
