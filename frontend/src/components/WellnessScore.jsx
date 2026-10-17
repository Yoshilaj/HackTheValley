import { wellness } from "../data/placeholder.js";

export default function WellnessScore() {
  return (
    <div className="card wellness">
      <p className="label">Wellness score</p>
      <p className="big">
        {wellness.score}
        <sup>/100</sup>
      </p>
      <p className="small">{wellness.note}</p>
      <div className="wellness-actions">
        <button className="btn-dark">Book a visit</button>
        <button className="btn-light">
          <span>Message care team</span>
          <i className="dial" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M5 6h14v9H9l-4 3z" />
            </svg>
          </i>
        </button>
      </div>
    </div>
  );
}
