import { appointmentList } from "../data/placeholder.js";

function AppointmentCard({ a }) {
  return (
    <li className="card appt-card">
      <span className="appt-icon" aria-hidden="true">
        {a.glyph}
      </span>
      <div className="appt-main">
        <h3>{a.name}</h3>
        <p className="appt-meta">
          {a.specialty} · {a.place}
        </p>
        <p className="appt-note">{a.note}</p>
      </div>
      <div className="appt-when">
        <b>{a.date}</b>
        <span>
          {a.time} · {a.length}
        </span>
      </div>
      <span className={`status ${a.status.toLowerCase()}`}>{a.status}</span>
      <button className="dots" aria-label={`More options for ${a.name}`}>
        ⋮
      </button>
    </li>
  );
}

export default function AppointmentsPage() {
  const { upcoming, past } = appointmentList;

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1 tabIndex={-1}>Appointments</h1>
          <p className="subtitle">Your doctor visits and lab bookings in one place.</p>
        </div>
        <button className="btn-primary">Book appointment</button>
      </header>

      <section aria-labelledby="upcoming-title">
        <h2 id="upcoming-title" className="section-title">
          Upcoming <sup>{upcoming.length}</sup>
        </h2>
        <ul className="appt-list">
          {upcoming.map((a) => (
            <AppointmentCard key={a.id} a={a} />
          ))}
        </ul>
      </section>

      <section aria-labelledby="past-title">
        <h2 id="past-title" className="section-title">
          Past visits <sup>{past.length}</sup>
        </h2>
        <ul className="appt-list">
          {past.map((a) => (
            <AppointmentCard key={a.id} a={a} />
          ))}
        </ul>
      </section>
    </main>
  );
}
