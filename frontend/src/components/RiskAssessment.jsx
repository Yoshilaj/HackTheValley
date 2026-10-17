import { Link } from "react-router-dom";
import { basisText, changeSince } from "../utils/risk.js";
import { relativeDay } from "../utils/dates.js";

// Card colour follows risk: green when low, through yellow, to red, then black
// from 90 up. Colours blend smoothly between the stops. Each stop is a deep
// shade so the white text keeps at least 4.5:1 contrast at every score.
const lerp = (a, b, t) => a + (b - a) * t;

// [score, hue, saturation %, lightness %]
// Checked: white text stays at 4.95:1 or better across the whole 0-100 range.
const STOPS = [
  [0, 145, 63, 22], // green
  [45, 45, 92, 20], // yellow (deep gold, so white text stays readable)
  [80, 0, 72, 34], // red
  [90, 0, 0, 9], // black
];

function colourAt(score) {
  const s = Math.min(100, Math.max(0, score));
  const i = STOPS.findIndex(([at], k) => k === STOPS.length - 1 || s < STOPS[k + 1][0]);
  const [at, h, sat, l] = STOPS[i];
  const next = STOPS[i + 1];
  if (!next) return [h, sat, l];
  const t = (s - at) / (next[0] - at);
  return [lerp(h, next[1], t), lerp(sat, next[2], t), lerp(l, next[3], t)];
}

export function riskColors(score) {
  const [h, s, l] = colourAt(score);
  // The gradient's second colour is the same shade, a little lighter.
  return { "--r-from": `hsl(${h}, ${s}%, ${l}%)`, "--r-to": `hsl(${h}, ${s}%, ${l + 4}%)` };
}

// The overall risk index (0-100), with what it combines, what it's based on,
// when it was updated and how it moved since last month.
export default function RiskAssessment({ score, level, previous, updatedAt, basis, conditionCount, onOpen, onContact }) {
  const change = changeSince(score, previous?.score);
  const combines = conditionCount ? `Combines ${conditionCount} condition estimates, based on ` : "Based on ";

  return (
    <div className="card risk tappable" style={riskColors(score)}>
      {/* Stretched button: the whole card opens details, while the action
          buttons below sit above it and stay separately clickable. */}
      <button type="button" className="card-hit" onClick={onOpen} aria-haspopup="dialog">
        <span className="sr-only">How your overall risk is worked out</span>
      </button>
      <div className="risk-top">
        <p className="label">Overall risk</p>
        <span className="risk-chip">{level}</span>
      </div>
      <p className="big">
        {score}
        <sup>/100</sup>
      </p>
      <p className="risk-meta">
        {[change, updatedAt && `Updated ${relativeDay(updatedAt)}`].filter(Boolean).join(" · ")}
      </p>
      <p className="risk-meta">
        {combines}
        {basisText(basis)}.
      </p>
      <div className="risk-actions">
        <Link to="/appointments#book" className="btn-dark">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z" />
            <path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z" />
          </svg>
          Let CareSync book a visit
        </Link>
        <button type="button" className="btn-light" onClick={onContact} aria-haspopup="dialog">
          <span>Contact care team</span>
          <i className="dial" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M5 4h4l2 5-2.5 1.5a11 11 0 005 5L15 13l5 2v4a2 2 0 01-2 2A16 16 0 013 6a2 2 0 012-2" />
            </svg>
          </i>
        </button>
      </div>
    </div>
  );
}
