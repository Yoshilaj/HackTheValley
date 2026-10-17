// Horizontal bar for one condition estimate: the track is tinted by risk band
// (low / moderate / high), the fill is the user's risk, and a tick marks a
// typical healthy person the same age. Decorative unless given a `label`.
export default function RiskBar({ c, label }) {
  const bands = c.bands ?? null;
  const max = bands ? bands[1] * 1.5 : Math.max(c.risk, c.typical ?? 0, 1) * 2;
  const at = (v) => `${Math.min(100, Math.max(0, (v / max) * 100))}%`;
  const zones = bands ? { "--m": at(bands[0]), "--h": at(bands[1]) } : { "--m": "100%", "--h": "100%" };
  const a11y = label ? { role: "img", "aria-label": label } : { "aria-hidden": true };

  return (
    <span className="risk-bar" style={zones} {...a11y}>
      <span className="risk-bar-fill" style={{ width: at(c.risk) }} />
      {c.typical != null && <span className="risk-bar-typical" style={{ left: at(c.typical) }} />}
    </span>
  );
}
