import { focusAreas } from "../data/placeholder.js";

// Donut built from a conic gradient with a small gap between segments.
function donutGradient(items) {
  const gap = 1.2;
  let acc = 0;
  const stops = items.map((s) => {
    const from = acc;
    const to = acc + s.pct;
    acc = to;
    return `${s.color} ${from + gap / 2}% ${to - gap / 2}%, transparent ${to - gap / 2}% ${to + gap / 2}%`;
  });
  return `conic-gradient(from 0deg, ${stops.join(", ")})`;
}

export default function HealthFocus() {
  const { items, total } = focusAreas;

  return (
    <div className="card focus">
      <div className="card-head">
        <h2>Health focus</h2>
        <button className="chip">July</button>
      </div>

      <div className="focus-body">
        <div
          className="donut"
          style={{ background: donutGradient(items) }}
          role="img"
          aria-label={`Goals met ${total}. ${items.map((s) => `${s.name} ${s.pct}%`).join(", ")}`}
        >
          <div className="donut-center">
            <span>Goals met</span>
            <strong>{total}</strong>
          </div>
        </div>

        <ul className="legend">
          {items.map((s) => (
            <li key={s.name} style={{ "--c": s.color }}>
              {s.name}
              <b>{s.pct}%</b>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
