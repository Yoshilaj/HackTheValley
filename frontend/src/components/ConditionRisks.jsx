import { useState } from "react";
import ConditionSheet from "./ConditionSheet.jsx";
import RiskBar from "./RiskBar.jsx";

// One compact card per condition beside the overall index. Every card has the
// same shape: the name, a level chip, a bar and one line of context. The bar is
// the follow-up priority (0-100) the rules assigned, banded at "watch" (25) and
// "follow-up recommended" (60). Tapping a card opens the evidence behind it.
const PRIORITY_BANDS = [25, 60];

const LEVEL_CHIP = {
  insufficient_data: "Not enough data",
  invalid_measurement: "Check readings",
  no_finding: "No findings",
  watch: "Keep an eye on",
  followup_recommended: "Follow-up recommended",
};

export default function ConditionRisks({ items, age, updatedAt, basis }) {
  const [openId, setOpenId] = useState(null);
  const [aboutOpen, setAboutOpen] = useState(false);
  if (!items?.length) return null;
  const open = items.find((c) => c.id === openId) ?? null;

  return (
    <section className="condition-group" aria-labelledby="condition-group-title">
      <div
        className="condition-group-head"
        onMouseEnter={() => setAboutOpen(true)}
        onMouseLeave={() => setAboutOpen(false)}
        onBlur={(e) => !e.currentTarget.contains(e.relatedTarget) && setAboutOpen(false)}
        onKeyDown={(e) => e.key === "Escape" && setAboutOpen(false)}
      >
        <h2 id="condition-group-title">
          <button
            type="button"
            className="cg-toggle"
            aria-expanded={aboutOpen}
            aria-controls="condition-group-about"
            onClick={() => setAboutOpen(true)}
          >
            Key conditions
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
        </h2>
        <div id="condition-group-about" className="cg-about" hidden={!aboutOpen}>
          <p>
            Each bar is how much follow-up your recent data calls for in that area (0-100), from your readings,
            check-ins and daily tasks checked against clinical rules.
          </p>
          <ul className="cg-legend">
            <li>
              <i className="cg-fill" aria-hidden="true" />
              The filled part of the bar is your follow-up priority.
            </li>
            <li>
              <i className="cg-bands" aria-hidden="true" />
              The track runs from low (green) through watch (amber) to high (red).
            </li>
          </ul>
          <p>Tap a card to see the evidence behind it. These are estimates, not a diagnosis.</p>
        </div>
      </div>
      <ul className="condition-risks">
        {items.map((c) => {
          const level = c.tone ?? "Low";
          const chip = LEVEL_CHIP[c.level] ?? c.finding;
          const barItem = { risk: c.priority ?? 0, bands: PRIORITY_BANDS, typical: null };
          const foot = c.headline || "Nothing in your recent data points to this.";
          return (
            <li key={c.id}>
              <button
                type="button"
                className={`card tappable condition-risk risk-tone ${level.toLowerCase()}`}
                onClick={() => setOpenId(c.id)}
                aria-haspopup="dialog"
                aria-label={`${c.name}: ${chip}. ${foot} Show why`}
              >
                <span className="condition-head">
                  <span className="condition-name">{c.name}</span>
                  <span className="condition-right">
                    <span className="condition-chip">{chip}</span>
                  </span>
                </span>
                <RiskBar c={barItem} />
                <span className="condition-foot">{foot}</span>
              </button>
            </li>
          );
        })}
      </ul>
      <ConditionSheet condition={open} age={age} updatedAt={updatedAt} basis={basis} onClose={() => setOpenId(null)} />
    </section>
  );
}
