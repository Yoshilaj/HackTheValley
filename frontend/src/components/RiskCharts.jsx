import { riskCharts } from "../data/placeholder.js";

// Two small charts under the overall risk card, filling the space beside the
// condition bars: the score month by month (bars) and what makes it up
// (ring). Sample data for now; the last bar follows the live score.
const RING = ["var(--primary)", "#5e8fe0", "#a8c4f0", "#d6e4fa"];

function TrendChart({ score }) {
  const data = riskCharts.trend.map((d, i, all) => (i === all.length - 1 ? { ...d, score } : d));
  const max = Math.max(100 / 3, ...data.map((d) => d.score)) * 1.15; // headroom over the tallest bar
  const summary = data.map((d) => `${d.month} ${d.score}`).join(", ");

  return (
    <section className="card chart-card" aria-labelledby="trend-title">
      <div className="chart-head">
        <h3 id="trend-title">Overall risk by month</h3>
        <span className="chart-tag">Sample</span>
      </div>
      <div className="bar-chart" role="img" aria-label={`Overall risk by month: ${summary}.`}>
        {data.map((d, i) => (
          <span key={d.month} className={`bar-col ${i === data.length - 1 ? "now" : ""}`}>
            <small>{d.score}</small>
            {/* 20px of the column is the value label above the bar */}
            <i style={{ height: `calc((100% - 20px) * ${(d.score / max).toFixed(3)})` }} />
          </span>
        ))}
      </div>
      <div className="bar-labels" aria-hidden="true">
        {data.map((d) => (
          <span key={d.month}>{d.month}</span>
        ))}
      </div>
    </section>
  );
}

function FactorsChart() {
  const { factors } = riskCharts;
  // Ring segments as dash offsets on a circle with circumference 100.
  let start = 0;
  const segments = factors.map((f, i) => {
    const seg = { ...f, color: RING[i % RING.length], offset: -start };
    start += f.share;
    return seg;
  });

  return (
    <section className="card chart-card" aria-labelledby="factors-title">
      <div className="chart-head">
        <h3 id="factors-title">What makes up your score</h3>
        <span className="chart-tag">Sample</span>
      </div>
      <div className="ring-chart">
        <svg viewBox="0 0 42 42" aria-hidden="true">
          <circle className="ring-track" cx="21" cy="21" r="15.915" />
          {segments.map((s) => (
            <circle
              key={s.name}
              cx="21"
              cy="21"
              r="15.915"
              stroke={s.color}
              strokeDasharray={`${s.share} ${100 - s.share}`}
              strokeDashoffset={s.offset}
            />
          ))}
        </svg>
        <ul className="ring-legend">
          {segments.map((s) => (
            <li key={s.name}>
              <i style={{ background: s.color }} aria-hidden="true" />
              <span>{s.name}</span>
              <strong>{s.share}%</strong>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

export default function RiskCharts({ score }) {
  return (
    <div className="risk-graphs">
      <TrendChart score={score} />
      <FactorsChart />
    </div>
  );
}
