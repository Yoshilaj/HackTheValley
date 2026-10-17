import { vitals, readings } from "../data/placeholder.js";
import ActivityChart from "../components/ActivityChart.jsx";

const labels = { normal: "Normal", watch: "Watch" };

function Sparkline({ points, label }) {
  const w = 120;
  const h = 36;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const coords = points.map((p, i) => [(i / (points.length - 1)) * w, h - 4 - ((p - min) / span) * (h - 8)]);
  const last = coords[coords.length - 1];

  return (
    <svg className="sparkline" viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${label} over the last 7 readings`}>
      <polyline points={coords.map((c) => c.join(",")).join(" ")} />
      <circle cx={last[0]} cy={last[1]} r="3.5" />
    </svg>
  );
}

export default function VitalsPage() {
  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1 tabIndex={-1}>Vitals</h1>
          <p className="subtitle">Your latest readings and how they've moved this week.</p>
        </div>
        <button className="btn-primary">Log a reading</button>
      </header>

      <section className="vitals-grid" aria-label="Latest vitals">
        {vitals.map((v) => (
          <article className={`card vital-tile ${v.status}`} key={v.id}>
            <div className="tile-head">
              <h2>{v.label}</h2>
              <em className={`badge ${v.status}`}>{labels[v.status]}</em>
            </div>
            <p className="amt">
              {v.value}
              <sup>{v.unit}</sup>
            </p>
            <Sparkline points={v.trend} label={v.label} />
            <p className="range">Healthy range: {v.range}</p>
          </article>
        ))}
      </section>

      <div className="page-split">
        <ActivityChart />

        <section className="card" aria-labelledby="readings-title">
          <div className="card-head">
            <h2 id="readings-title">Recent readings</h2>
          </div>
          <ul className="readings">
            {readings.map((r) => (
              <li key={r.id}>
                <div>
                  <b>{r.vital}</b>
                  <small>{r.date}</small>
                </div>
                <span className="reading-value">{r.value}</span>
                <em className={`badge ${r.status}`}>{labels[r.status]}</em>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </main>
  );
}
