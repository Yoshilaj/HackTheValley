export default function VitalCard({ label, amount, unit, note, badge, trend }) {
  return (
    <div className={`card vital ${trend === "watch" ? "watch" : ""}`}>
      <p className="label">{label}</p>
      <p className="amt">
        {amount}
        <sup>{unit}</sup>
      </p>
      <div className="vital-foot">
        <span>{note}</span>
        <em className={`badge ${trend}`}>{badge}</em>
      </div>
    </div>
  );
}
