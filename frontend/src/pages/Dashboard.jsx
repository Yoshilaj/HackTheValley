import TopBar from "../components/TopBar.jsx";
import WellnessScore from "../components/WellnessScore.jsx";
import VitalCard from "../components/VitalCard.jsx";
import ActivityChart from "../components/ActivityChart.jsx";
import HealthFocus from "../components/HealthFocus.jsx";
import Appointments from "../components/Appointments.jsx";
import CoverageStack from "../components/CoverageStack.jsx";
import Medications from "../components/Medications.jsx";
import { stats, patient } from "../data/placeholder.js";
import "../styles/dashboard.css";

export default function Dashboard() {
  return (
    <div className="app">
      <TopBar />

      <main className="grid">
        <section className="left">
          <h1>Good morning, {patient.name}</h1>
          <p className="subtitle">Here's how your health looks today.</p>

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

          <Appointments />
        </section>

        <aside className="card panel">
          <CoverageStack />
          <Medications />
        </aside>
      </main>
    </div>
  );
}
