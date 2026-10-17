import WellnessScore from "../components/WellnessScore.jsx";
import VitalCard from "../components/VitalCard.jsx";
import ActivityChart from "../components/ActivityChart.jsx";
import HealthFocus from "../components/HealthFocus.jsx";
import { stats, patient } from "../data/placeholder.js";

export default function Overview() {
  return (
    <main className="page">
      <div>
        <h1 tabIndex={-1}>Good morning, {patient.name}</h1>
        <p className="subtitle">Here's how your health looks today.</p>
      </div>

      <div className="row-top">
        <WellnessScore />
        <div className="stack">
          {stats.map((s) => (
            <VitalCard key={s.id} {...s} />
          ))}
        </div>
      </div>

      <div className="row-mid">
        <ActivityChart />
        <HealthFocus />
      </div>
    </main>
  );
}
