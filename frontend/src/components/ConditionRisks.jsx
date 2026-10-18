import { useState } from "react";
import ConditionSheet from "./ConditionSheet.jsx";
import RiskBar from "./RiskBar.jsx";
import { conditionLevel, formatPct, naturalFrequency } from "../utils/risk.js";

// Per-disease estimates beside the overall index, under their own heading and
// stacked to match the big card: the % chance within the method's time frame,
// said as "about N in 100 people like you", and a banded bar with a tick for a
// typical healthy person the same age. Tapping opens the explanation.
// The heading drops down a fuller "how to read these" note on hover, click or
// tap (touch has no hover). Clicking only opens it, so a click right after
// hovering doesn't close it again; it closes when the pointer leaves, focus
// moves away (tab, or a tap elsewhere) or on Esc.
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
            Risk assessments for key conditions
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
        </h2>
        <div id="condition-group-about" className="cg-about" hidden={!aboutOpen}>
          <p>
            Each card is your estimated chance of developing that condition within the years shown, worked out from
            your chats, your doctors' diagnoses and clinical risk models.
          </p>
          <ul className="cg-legend">
            <li>
              <i className="cg-fill" aria-hidden="true" />
              The filled part of the bar is your risk.
            </li>
            <li>
              <i className="legend-tick" aria-hidden="true" />
              The tick marks a typical healthy person your age.
            </li>
            <li>
              <i className="cg-bands" aria-hidden="true" />
              The track runs from low (green) through moderate (amber) to high (red).
            </li>
          </ul>
          <p>Tap a card to see the evidence behind it. These are estimates, not a diagnosis.</p>
        </div>
      </div>
      <ul className="condition-risks">
        {items.map((c) => {
          const level = conditionLevel(c);
          // Backend pathways without a % (cognitive, Parkinson's) show their finding instead.
          const hasPct = c.risk != null;
          const freq = hasPct ? naturalFrequency(c.risk) : null;
          const typical = c.typical != null ? ` Typical for your age: ${formatPct(c.typical)}.` : "";
          return (
            <li key={c.id}>
              <button
                type="button"
                className={`card tappable condition-risk risk-tone ${level.toLowerCase()}`}
                onClick={() => setOpenId(c.id)}
                aria-haspopup="dialog"
                aria-label={
                  hasPct
                    ? `${c.name}: ${formatPct(c.risk)} chance in ${c.horizon} years, ${level.toLowerCase()} risk.${typical} Show why`
                    : `${c.name}: ${c.finding}. ${c.headline ?? ""} Show why`
                }
              >
                <span className="condition-head">
                  <span className="condition-name">{c.name}</span>
                  <span className={`condition-score ${hasPct ? "" : "condition-finding"}`}>
                    {hasPct ? formatPct(c.risk) : c.finding}
                  </span>
                </span>
                {hasPct && <RiskBar c={c} />}
                <span className="condition-foot">
                  {hasPct ? (
                    <>
                      <span className="condition-level">{level} risk</span>
                      {` · about ${freq.count} in ${freq.of.toLocaleString()} people like you within ${c.horizon} years`}
                      {c.typical != null && ` · typical for your age ${formatPct(c.typical)}`}
                    </>
                  ) : (
                    c.headline
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <ConditionSheet condition={open} age={age} updatedAt={updatedAt} basis={basis} onClose={() => setOpenId(null)} />
    </section>
  );
}
