// Rendered as a <button>, so children are spans (buttons only allow phrasing content).
export default function VitalCard({ label, amount, unit, note, badge, trend, onOpen }) {
  return (
    <button
      type="button"
      className={`card vital tappable ${trend === "watch" ? "watch" : ""}`}
      onClick={onOpen}
      aria-haspopup="dialog"
    >
      <span className="label">{label}</span>
      <span className="amt">
        {amount}
        <sup>{unit}</sup>
      </span>
      <span className="vital-foot">
        <span>{note}</span>
        <em className={`badge ${trend}`}>{badge}</em>
      </span>
    </button>
  );
}
