import { useState } from "react";
import ConditionSheet from "./ConditionSheet.jsx";
import RiskBar from "./RiskBar.jsx";
import { conditionLevel, formatPct, naturalFrequency } from "../utils/risk.js";

// Per-disease estimates beside the overall index, under their own heading and
// stacked to match the big card: the % chance within the method's time frame,
// said as "about N in 100 people like you", and a banded bar with a tick for a
// typical healthy person the same age. Tapping opens the explanation.
export default function ConditionRisks({ items, age, updatedAt, basis }) {
  const [openId, setOpenId] = useState(null);
  if (!items?.length) return null;
  const open = items.find((c) => c.id === openId) ?? null;

  return (
    <section className="condition-group" aria-labelledby="condition-group-title">
      <div className="condition-group-head">
        <h2 id="condition-group-title">Risk assessments for key conditions</h2>
        <p>
          Your chance of each over the years shown. The <span className="legend-tick" aria-hidden="true" />
          tick marks a typical person your age. Tap one to see why.
        </p>
      </div>
      <ul className="condition-risks">
        {items.map((c) => {
          const level = conditionLevel(c);
          const freq = naturalFrequency(c.risk);
          const typical = c.typical != null ? ` Typical for your age: ${formatPct(c.typical)}.` : "";
          return (
            <li key={c.id}>
              <button
                type="button"
                className={`card tappable condition-risk risk-tone ${level.toLowerCase()}`}
                onClick={() => setOpenId(c.id)}
                aria-haspopup="dialog"
                aria-label={`${c.name}: ${formatPct(c.risk)} chance in ${c.horizon} years, ${level.toLowerCase()} risk.${typical} Show why`}
              >
                <span className="condition-head">
                  <span className="condition-name">{c.name}</span>
                  <span className="condition-score">{formatPct(c.risk)}</span>
                </span>
                <RiskBar c={c} />
                <span className="condition-foot">
                  <span className="condition-level">{level} risk</span>
                  {` · about ${freq.count} in ${freq.of.toLocaleString()} people like you within ${c.horizon} years`}
                  {c.typical != null && ` · typical for your age ${formatPct(c.typical)}`}
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
