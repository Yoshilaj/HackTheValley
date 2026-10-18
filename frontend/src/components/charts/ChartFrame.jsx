import { ResponsiveContainer } from "recharts";

// The card around one or more charts: a title, an optional tag ("Experimental",
// "30 days"), an empty state, and a note under the plot. Charts go inside a
// ChartBox, which gives Recharts a fixed height and a shrinkable width (a flex
// or grid child without min-width: 0 measures as 0 wide).
export function ChartBox({ height = 180, label, children }) {
  return (
    <div className="chart-box" style={{ height }} role="img" aria-label={label}>
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  );
}

export default function ChartFrame({ id, title, tag, empty, note, actions, children }) {
  const titleId = id ? `${id}-title` : undefined;
  return (
    <section className="card chart-card" aria-labelledby={titleId}>
      <div className="chart-head">
        <h3 id={titleId}>{title}</h3>
        {tag && <span className="chart-tag">{tag}</span>}
        {actions}
      </div>
      {empty ? <p className="field-hint chart-empty">{empty}</p> : children}
      {note && !empty && <p className="field-hint chart-note">{note}</p>}
    </section>
  );
}
