import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import GuestGate from "../components/GuestGate.jsx";
import RiskTrendChart from "../components/RiskTrendChart.jsx";
import { BpChart, MetricChart, SleepChart, StepsChart } from "../components/VitalsCharts.jsx";
import { StoryHistoryChart, TappingHistoryChart, VowelHistoryChart } from "../components/TaskHistoryCharts.jsx";
import AppleWatchCard from "../components/AppleWatchCard.jsx";
import RhythmResultCard from "../components/RhythmResultCard.jsx";
import { useCareAlert } from "../context/CareAlertContext.jsx";
import { useSession } from "../context/SessionContext.jsx";
import useVitals from "../hooks/useVitals.js";
import useSeries from "../hooks/useSeries.js";
import { getTaskHistory } from "../api/client.js";
import { VITAL_INPUTS, VITAL_ORDER } from "../utils/vitals.js";
import { capitalize, relativeDay } from "../utils/dates.js";
import { TASK_NAMES, taskSummary } from "../utils/tasks.js";
import { reassessNow } from "../utils/riskEvents.js";

// Everything CareSync has recorded, in one place: this week's numbers at a
// glance, then the heart charts, each logged vital over time, activity and
// sleep, the daily tasks over time, the risk trend, the readings and rhythm
// results, and the Apple Watch connection.
const labels = { normal: "Normal", watch: "Watch" };
const LIST_ROWS = 6; // keeps the list cards about the height of the charts beside them
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const lastWeek = (series) => (series?.daily ?? []).filter((d) => Number.isFinite(d.value)).slice(-7).map((d) => d.value);

function Section({ id, title, sub, action, children }) {
  return (
    <section className="data-section" aria-labelledby={`${id}-title`}>
      <div className="data-section-head">
        <div>
          <h2 id={`${id}-title`}>{title}</h2>
          {sub && <p className="subtitle">{sub}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

// One number from the last 7 days, with its unit and a line of context.
function Stat({ label, value, unit, sub, tone }) {
  const empty = value == null;
  return (
    <div className={`stat-tile ${empty ? "empty" : ""} ${tone ?? ""}`}>
      <p className="stat-label">{label}</p>
      <p className="stat-value">
        {empty ? "–" : value}
        {!empty && unit && <small>{unit}</small>}
      </p>
      <p className="stat-sub">{empty ? "No data yet" : sub}</p>
    </div>
  );
}

export default function DataPage() {
  const { isGuest, signedIn } = useSession();
  const { cards } = useCareAlert().risk;
  const { readings, loading, bp, summary, reload: reloadVitals } = useVitals();
  const [tick, setTick] = useState(0);
  const opts = { enabled: signedIn };
  const recent = useSeries(() => getTaskHistory(null, 20), [tick], opts);
  const tapping = useSeries(() => getTaskHistory("finger_tap", 50), [tick], opts);
  const vowel = useSeries(() => getTaskHistory("sustained_vowel", 50), [tick], opts);
  const story = useSeries(() => getTaskHistory("story_recall", 50), [tick], opts);

  const onImported = useCallback(() => {
    setTick((n) => n + 1);
    reloadVitals();
    reassessNow();
  }, [reloadVitals]);

  if (isGuest) {
    return (
      <main className="page">
        <header className="page-head">
          <div>
            <h1 tabIndex={-1}>Data</h1>
            <p className="subtitle">Everything CareSync has recorded, as charts.</p>
          </div>
        </header>
        <GuestGate title="See your data">Sign in to see your readings, check-ins and task results over time.</GuestGate>
      </main>
    );
  }

  const metrics = summary?.metrics ?? {};
  // Resting heart rate has its own card in the Heart section; the rest go under Vitals.
  const otherMetrics = VITAL_ORDER.filter((id) => id !== "bp" && id !== "hr" && metrics[VITAL_INPUTS[id].metric]?.daily?.length);
  const hasActivity = Boolean(metrics.steps?.daily?.length || metrics.sleep_asleep_minutes?.daily?.length);
  const history = (recent.data?.items ?? []).slice(0, LIST_ROWS);

  // This week at a glance.
  const hrWeek = mean(lastWeek(metrics.resting_hr));
  const hrMedian = metrics.resting_hr?.baseline_30d?.median;
  const bpWin = bp?.summary?.window_7d?.day_mean;
  const stepsWeek = mean(lastWeek(metrics.steps));
  const sleepWeek = mean(lastWeek(metrics.sleep_asleep_minutes));
  const since = Date.now() - WEEK_MS;
  const tasksWeek = (recent.data?.items ?? []).filter((t) => t.valid && t.status !== "in_progress" && new Date(t.created_at).getTime() >= since).length;

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1 tabIndex={-1}>Data</h1>
          <p className="subtitle">Everything CareSync has recorded. Hover a point for the exact value.</p>
        </div>
      </header>

      <div className="stat-strip" aria-label="This week at a glance">
        <Stat
          label="Resting heart rate"
          value={hrWeek != null ? Math.round(hrWeek) : null}
          unit="bpm"
          sub={Number.isFinite(hrMedian) ? `7-day average · 30-day median ${Math.round(hrMedian)}` : "7-day average"}
        />
        <Stat
          label="Blood pressure"
          value={bpWin ? `${Math.round(bpWin.systolic)}/${Math.round(bpWin.diastolic)}` : null}
          unit="mmHg"
          sub={
            bpWin
              ? `${bpWin.systolic >= 120 || bpWin.diastolic >= 80 ? "Above the 120/80 goal" : "Within the 120/80 goal"} · 7-day average over ${bpWin.n_days} day${bpWin.n_days === 1 ? "" : "s"}`
              : null
          }
          tone={bpWin && (bpWin.systolic >= 120 || bpWin.diastolic >= 80) ? "watch" : undefined}
        />
        <Stat label="Steps" value={stepsWeek != null ? Math.round(stepsWeek).toLocaleString() : null} unit="a day" sub="7-day average" />
        <Stat label="Sleep" value={sleepWeek != null ? (sleepWeek / 60).toFixed(1) : null} unit="h a night" sub="7-day average" />
        <Stat label="Tasks done" value={recent.data ? tasksWeek : null} unit="this week" sub="Tapping, voice and story" />
      </div>

      <Section id="heart" title="Heart" sub="Resting heart rate from your watch, and home blood pressure by session.">
        <div className="data-grid">
          <MetricChart id="hr" series={metrics.resting_hr} />
          <BpChart bp={bp} />
        </div>
      </Section>

      {(otherMetrics.length > 0 || hasActivity) && (
        <Section id="vitals" title="Vitals, activity and sleep" sub="Each logged vital by day, with your 30-day median, plus steps and sleep from your watch.">
          <div className="data-grid">
            {otherMetrics.map((id) => (
              <MetricChart key={id} id={id} series={metrics[VITAL_INPUTS[id].metric]} />
            ))}
            {hasActivity && <StepsChart summary={summary} />}
            {hasActivity && <SleepChart summary={summary} />}
          </div>
        </Section>
      )}

      <Section
        id="tasks"
        title="Daily tasks"
        sub="Tapping, voice and story results over time."
        action={
          <Link to="/tasks" className="btn-secondary">
            Do today's tasks
          </Link>
        }
      >
        <div className="data-grid">
          <TappingHistoryChart items={tapping.data?.items} />
          <VowelHistoryChart items={vowel.data?.items} />
          <StoryHistoryChart items={story.data?.items} />
          <section className="card list-card" aria-labelledby="task-history-title">
            <div className="card-head">
              <h3 id="task-history-title">Recent tasks</h3>
            </div>
            {history.length ? (
              <ul className="readings">
                {history.map((t) => {
                  const chip = taskSummary(t);
                  return (
                    <li key={t.id}>
                      <div>
                        <b>{TASK_NAMES[t.task_type] ?? t.task_type}</b>
                        <small>{capitalize(relativeDay(new Date(t.created_at).getTime()))}</small>
                      </div>
                      {chip && <span className="reading-value">{chip}</span>}
                      <em className={`badge ${t.valid ? "normal" : "watch"}`}>
                        {t.valid ? "Valid" : t.status === "in_progress" ? "In progress" : "Check"}
                      </em>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="field-hint list-empty">{recent.loading ? "Loading…" : "No tasks done yet."}</p>
            )}
          </section>
        </div>
      </Section>

      <Section id="risk" title="Risk over time" sub="Follow-up priority per area, one point per week, from the nightly assessment.">
        <div className="data-grid">
          <div className="span-2">
            <RiskTrendChart cards={cards} />
          </div>
        </div>
      </Section>

      <Section id="readings" title="Readings" sub="The latest readings you logged, and heart-rhythm results from your watch or cuff.">
        <div className="data-grid">
          <section className="card list-card" aria-labelledby="readings-title">
            <div className="card-head">
              <h3 id="readings-title">Recent readings</h3>
            </div>
            {readings.length ? (
              <ul className="readings">
                {readings.slice(0, LIST_ROWS).map((r) => (
                  <li key={r.id}>
                    <div>
                      <b>{r.vital}</b>
                      <small>
                        {r.date}
                        {r.note && ` · ${r.note}`}
                      </small>
                    </div>
                    <span className="reading-value">{r.value}</span>
                    <em className={`badge ${r.status}`}>{labels[r.status]}</em>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="field-hint list-empty">{loading ? "Loading your readings…" : "No readings yet. Log blood pressure on the Daily tasks page, or import your Apple Health export."}</p>
            )}
          </section>
          <RhythmResultCard />
        </div>
      </Section>

      <Section id="watch" title="Connect a watch" sub="Bring in steps, sleep, resting heart rate and rhythm results from your Apple Watch.">
        <AppleWatchCard onImported={onImported} />
      </Section>
    </main>
  );
}
