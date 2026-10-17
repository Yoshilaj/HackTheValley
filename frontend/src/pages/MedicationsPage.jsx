import { useState } from "react";
import { medicationList } from "../data/placeholder.js";

// One entry per scheduled dose, e.g. Metformin at 8:00 AM and again at 6:00 PM.
const doses = medicationList.flatMap((m) =>
  m.times.map((time) => ({ key: `${m.id}-${time}`, name: m.name, dose: m.dose, time }))
);

export default function MedicationsPage() {
  const [taken, setTaken] = useState(() => new Set());

  const toggle = (key) =>
    setTaken((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1 tabIndex={-1}>Medications</h1>
          <p className="subtitle">What you're taking now, and what's due today.</p>
        </div>
        <button className="btn-primary">Request refill</button>
      </header>

      <section className="card" aria-labelledby="schedule-title">
        <div className="card-head">
          <h2 id="schedule-title">Today's schedule</h2>
          <span className="progress-text" role="status">
            {taken.size} of {doses.length} taken
          </span>
        </div>
        <div
          className="progress"
          role="progressbar"
          aria-label="Doses taken today"
          aria-valuemin={0}
          aria-valuemax={doses.length}
          aria-valuenow={taken.size}
        >
          <span style={{ width: `${(taken.size / doses.length) * 100}%` }} />
        </div>
        <ul className="schedule">
          {doses.map((d) => (
            <li key={d.key}>
              <label className="dose-check">
                <input type="checkbox" checked={taken.has(d.key)} onChange={() => toggle(d.key)} />
                <span className="dose-time">{d.time}</span>
                <span className="dose-name">
                  {d.name} <small>{d.dose}</small>
                </span>
              </label>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="current-title">
        <h2 id="current-title" className="section-title">
          Current medications <sup>{medicationList.length}</sup>
        </h2>
        <ul className="med-grid">
          {medicationList.map((m) => (
            <li className="card med-card" key={m.id}>
              <div className="med-top">
                <span className="med-glyph" aria-hidden="true">
                  {m.glyph}
                </span>
                <div>
                  <h3>{m.name}</h3>
                  <p className="appt-meta">{m.dose}</p>
                </div>
                {m.refills <= 1 && <em className="badge watch">Refill soon</em>}
              </div>
              <dl className="med-facts">
                <div>
                  <dt>How often</dt>
                  <dd>{m.frequency}</dd>
                </div>
                <div>
                  <dt>For</dt>
                  <dd>{m.purpose}</dd>
                </div>
                <div>
                  <dt>Prescribed by</dt>
                  <dd>{m.prescriber}</dd>
                </div>
                <div>
                  <dt>Refills left</dt>
                  <dd>{m.refills}</dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
