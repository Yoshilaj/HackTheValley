import { activity } from "../data/placeholder.js";

export default function ActivityChart() {
  return (
    <div className="card activity">
      <div className="card-head">
        <h2>Daily activity</h2>
        <span className="chip chip-static">This week</span>
      </div>

      <div className="bars">
        {activity.map((r) => (
          <div className="bar-col" key={r.day}>
            {r.tip && (
              <div className="tip">
                <i className="tip-dot" aria-hidden="true">↗</i>
                <div>
                  <b>{r.tip.amount}</b>
                  <em>{r.tip.change}</em>
                </div>
              </div>
            )}
            <div className={`bar ${r.active ? "active" : ""}`} style={{ height: `${r.height}%` }}>
              {r.active && <span className="ring" />}
              <span className="bar-val">{r.value}</span>
            </div>
            <span className="bar-label">{r.day}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
