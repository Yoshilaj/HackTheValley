import { Link } from "react-router-dom";
import { ResponsiveContainer } from "recharts";

// The card around one or more charts: a title, an optional tag ("Experimental",
// "30 days"), an empty state, and a note under the plot. Charts go inside a
// ChartBox, which gives Recharts a minimum height and lets the plot grow when
// the card is stretched to match its neighbour (the ResponsiveContainer is
// positioned over the box, so it measures the box's real size).
export function ChartBox({ height = 180, label, children }) {
  return (
    <div className="chart-box" style={{ minHeight: height }} role="img" aria-label={label}>
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  );
}

// empty: text for the empty state; emptyAction: { to, label } a link shown under it.
export default function ChartFrame({ id, title, tag, empty, emptyAction, note, actions, children }) {
  const titleId = id ? `${id}-title` : undefined;
  return (
    <section className="card chart-card" aria-labelledby={titleId}>
      <div className="chart-head">
        <h3 id={titleId}>{title}</h3>
        {tag && <span className="chart-tag">{tag}</span>}
        {actions}
      </div>
      {empty ? (
        <div className="chart-empty">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 18h16M6 15l4-5 3 3 5-7" />
          </svg>
          <p>{empty}</p>
          {emptyAction && (
            <Link to={emptyAction.to} className="btn-secondary chart-empty-action">
              {emptyAction.label}
            </Link>
          )}
        </div>
      ) : (
        children
      )}
      {note && !empty && <p className="field-hint chart-note">{note}</p>}
    </section>
  );
}
