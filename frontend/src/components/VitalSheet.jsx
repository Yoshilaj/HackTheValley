import { useEffect, useRef } from "react";
import { VITAL_INPUTS } from "../utils/vitals.js";
import { relativeDay } from "../utils/dates.js";

// Glass sheet behind a vitals tile: what the reading means, the last 7
// readings against the healthy range, low / high / average, tips, and a
// shortcut to log a new one. Native <dialog> like DetailSheet.
const labels = { normal: "Normal", watch: "Watch" };

// Line chart of the last readings with the healthy range shaded behind it.
function TrendChart({ points, band, label, unit }) {
  const w = 320;
  const h = 120;
  const padX = 10;
  const padY = 12;
  const lo = Math.min(...points, band ? band[0] : Infinity);
  const hi = Math.max(...points, band ? band[1] : -Infinity);
  const span = hi - lo || 1;
  const x = (i) => padX + (i / Math.max(1, points.length - 1)) * (w - padX * 2);
  const y = (v) => padY + (1 - (v - lo) / span) * (h - padY * 2);

  return (
    <svg
      className="trend-chart"
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`${label}, last ${points.length} readings: ${points.join(", ")} ${unit}`}
    >
      {band && <rect className="trend-band" x="0" width={w} y={y(band[1])} height={y(band[0]) - y(band[1])} rx="8" />}
      <polyline points={points.map((p, i) => `${x(i)},${y(p)}`).join(" ")} />
      {points.map((p, i) => (
        <circle key={i} cx={x(i)} cy={y(p)} r={i === points.length - 1 ? 4.5 : 3} />
      ))}
    </svg>
  );
}

export default function VitalSheet({ vital, onClose, onLog }) {
  const ref = useRef(null);

  useEffect(() => {
    const dialog = ref.current;
    if (vital && !dialog.open) dialog.showModal();
    if (!vital && dialog.open) dialog.close();
  }, [vital]);

  const v = vital;
  const isBp = v?.id === "bp";
  const decimals = v ? (VITAL_INPUTS[v.id]?.fields[0].decimals ?? 0) : 0;
  const fmt = (n) => (decimals ? n.toFixed(decimals) : String(Math.round(n)));
  const pts = v?.trend ?? [];
  const avg = pts.length ? pts.reduce((sum, p) => sum + p, 0) / pts.length : 0;

  return (
    <dialog
      ref={ref}
      className="sheet book-sheet vital-sheet"
      aria-labelledby="vital-title"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      {v && (
        <div className="sheet-body">
          <header className="sheet-head">
            <div>
              <p className="label">{v.label}</p>
              <p id="vital-title" className="sheet-value">
                {v.value}
                <sup>{v.unit}</sup>
              </p>
              <em className={`badge ${v.status}`}>{labels[v.status]}</em>
            </div>
            <button className="sheet-close" aria-label="Close" onClick={onClose}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </header>

          {v.about && <p className="sheet-about">{v.about}</p>}

          {pts.length > 1 ? (
            <figure className="trend-figure">
              <TrendChart points={pts} band={v.band} label={isBp ? `${v.label}, top number` : v.label} unit={v.unit} />
              <figcaption>
                Last {pts.length} days{isBp ? " (top number)" : ""}. {v.band ? "The shaded band is the healthy range." : ""}
              </figcaption>
            </figure>
          ) : (
            <p className="field-hint">No trend yet: log a few readings on different days to see one here.</p>
          )}

          <dl className="sheet-rows">
            <div>
              <dt>Healthy range</dt>
              <dd>{v.range}</dd>
            </div>
            {pts.length > 0 && (
              <>
                <div>
                  <dt>Lowest – highest</dt>
                  <dd>
                    {fmt(Math.min(...pts))} – {fmt(Math.max(...pts))} {v.unit}
                  </dd>
                </div>
                <div>
                  <dt>Average</dt>
                  <dd>
                    {fmt(avg)} {v.unit}
                  </dd>
                </div>
              </>
            )}
            {v.lastLoggedAt && (
              <div>
                <dt>Last logged by you</dt>
                <dd>{relativeDay(v.lastLoggedAt)}</dd>
              </div>
            )}
          </dl>

          {v.tips?.length > 0 && (
            <>
              <h3 className="sheet-sub">Tips</h3>
              <ul className="sheet-tips">
                {v.tips.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </>
          )}

          <div className="book-actions">
            <button type="button" className="btn-primary" onClick={() => onLog(v.id)}>
              Log a reading
            </button>
          </div>
        </div>
      )}
    </dialog>
  );
}
