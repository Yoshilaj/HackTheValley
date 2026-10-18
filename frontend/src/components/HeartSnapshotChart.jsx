import { Link } from "react-router-dom";
import ChartFrame from "./charts/ChartFrame.jsx";
import TrendLineChart from "./charts/TrendLineChart.jsx";
import { PRIMARY, SECONDARY } from "./charts/theme.js";
import useVitals from "../hooks/useVitals.js";

// The heart card: resting heart rate and home blood pressure side by side,
// each with its 7-day average in the panel heading and the recent weeks
// underneath. Two plots, one axis each (beats per minute and mmHg never share
// a scale). showLink: a link to the Data tab (on the Overview).
const round = (v) => (Number.isFinite(v) ? Math.round(v) : null);
const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

export default function HeartSnapshotChart({ showLink = false, height = 150 }) {
  const { bp, summary, loading } = useVitals();

  const hrDaily = (summary?.metrics?.resting_hr?.daily ?? []).filter((d) => Number.isFinite(d.value));
  const hrData = hrDaily.map((d) => ({ date: d.date, hr: d.value }));
  const hrWeek = round(mean(hrDaily.slice(-7).map((d) => d.value)));

  const bpDays = (bp?.summary?.per_day ?? []).filter((d) => d.day_mean);
  const bpData = bpDays.map((d) => ({ date: d.date, sys: d.day_mean.systolic, dia: d.day_mean.diastolic }));
  const win = bp?.summary?.window_7d?.day_mean;
  const bpWeek = win ? `${round(win.systolic)}/${round(win.diastolic)}` : null;

  const empty = !hrData.length && !bpData.length;

  return (
    <ChartFrame
      id="heart-snapshot"
      title="Heart"
      tag="7-day averages, last 30 days"
      empty={empty ? (loading ? "Loading…" : "Log blood pressure or import Apple Health data to see your heart here.") : null}
      actions={
        showLink ? (
          <Link to="/data" className="chart-link">
            All data
          </Link>
        ) : null
      }
    >
      <div className="heart-grid">
        <div className="heart-panel">
          <p className="panel-head">
            <span>Resting heart rate</span>
            <b>
              {hrWeek ?? "–"}
              <small>bpm</small>
            </b>
          </p>
          {hrData.length > 1 ? (
            <TrendLineChart
              data={hrData}
              series={[{ key: "hr", name: "Resting heart rate", color: SECONDARY }]}
              unit="bpm"
              band={[60, 100]}
              height={height}
              valueFormat={(v) => Math.round(v)}
              label="Resting heart rate per day over the last 30 days"
            />
          ) : (
            <p className="field-hint chart-empty">No heart-rate data yet.</p>
          )}
        </div>
        <div className="heart-panel">
          <p className="panel-head">
            <span>Blood pressure</span>
            <b>
              {bpWeek ?? "–"}
              <small>mmHg</small>
            </b>
          </p>
          {bpData.length > 1 ? (
            <TrendLineChart
              data={bpData}
              series={[
                { key: "sys", name: "Top (systolic)", color: PRIMARY },
                { key: "dia", name: "Bottom (diastolic)", color: PRIMARY, opacity: 0.45 },
              ]}
              unit="mmHg"
              yDomain={[60, "auto"]}
              refLines={[{ y: 120, label: "Top goal" }]}
              height={height}
              valueFormat={(v) => Math.round(v)}
              label="Daily average blood pressure over the last two weeks"
            />
          ) : (
            <p className="field-hint chart-empty">No blood pressure readings yet.</p>
          )}
        </div>
      </div>
    </ChartFrame>
  );
}
