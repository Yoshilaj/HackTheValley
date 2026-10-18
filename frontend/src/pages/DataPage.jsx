import { useCallback, useState } from "react";
import GuestGate from "../components/GuestGate.jsx";
import RiskTrendChart from "../components/RiskTrendChart.jsx";
import HeartSnapshotChart from "../components/HeartSnapshotChart.jsx";
import { ActivitySleepChart, BpChart, MetricChart } from "../components/VitalsCharts.jsx";
import { StoryHistoryChart, TappingHistoryChart, VowelHistoryChart } from "../components/TaskHistoryCharts.jsx";
import AppleWatchCard from "../components/AppleWatchCard.jsx";
import LogReadingSheet from "../components/LogReadingSheet.jsx";
import RhythmResultCard from "../components/RhythmResultCard.jsx";
import { useCareAlert } from "../context/CareAlertContext.jsx";
import { useSession } from "../context/SessionContext.jsx";
import useVitals from "../hooks/useVitals.js";
import useSeries from "../hooks/useSeries.js";
import { getTaskHistory } from "../api/client.js";
import { VITAL_INPUTS, VITAL_ORDER } from "../utils/vitals.js";
import { capitalize, relativeDay } from "../utils/dates.js";
import { reassessNow } from "../utils/riskEvents.js";

// Everything CareSync has recorded, in one place: the latest vitals, the heart
// charts, each logged vital over time, activity and sleep, the daily tasks over
// time, the risk trend, the readings list and the Apple Watch connection.
const labels = { normal: "Normal", watch: "Watch" };
const LIST_ROWS = 6; // keeps the list cards the height of the charts beside them

const TASK_NAMES = {
  finger_tap: "Finger tapping",
  sustained_vowel: "Sustained vowel",
  story_recall: "Story recall",
  word_recall: "Word recall",
  free_speech: "Voice message",
};

function summaryChip(t) {
  const s = t.summary ?? {};
  if (t.task_type === "finger_tap" && (s.left_tap_count != null || s.right_tap_count != null))
    return `${Math.round(s.left_tap_count ?? 0)} / ${Math.round(s.right_tap_count ?? 0)} taps`;
  if (t.task_type === "sustained_vowel" && typeof s.snr_db === "number") return `${Math.round(s.snr_db)} dB`;
  if (t.task_type === "story_recall" && typeof (s.score ?? t.score) === "number") return `${Math.round(s.score ?? t.score)} / 100`;
  return null;
}

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

export default function DataPage() {
  const { isGuest, signedIn } = useSession();
  const { cards } = useCareAlert().risk;
  const { readings, logReading, loading, bp, summary, reload: reloadVitals } = useVitals();
  const [form, setForm] = useState(null);
  const [justAdded, setJustAdded] = useState(null);
  const [announce, setAnnounce] = useState("");
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

  const save = async (data) => {
    const entry = await logReading(data);
    setJustAdded(entry.id);
    setAnnounce(`${VITAL_INPUTS[entry.vitalId].label} reading saved.`);
  };

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
  const loggedMetrics = VITAL_ORDER.filter((id) => id !== "bp" && metrics[VITAL_INPUTS[id].metric]?.daily?.length);
  const history = (recent.data?.items ?? []).slice(0, LIST_ROWS);

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1 tabIndex={-1}>Data</h1>
          <p className="subtitle">Everything CareSync has recorded. Hover a point for the exact value.</p>
        </div>
        <button className="btn-primary" onClick={() => setForm({})} aria-haspopup="dialog">
          Log a reading
        </button>
      </header>

      <LogReadingSheet open={form !== null} initial={form} onClose={() => setForm(null)} onSave={save} />
      <p className="sr-only" role="status">
        {announce}
      </p>

      <Section id="heart" title="Heart" sub="Resting heart rate and home blood pressure.">
        <div className="data-grid">
          <div className="span-2">
            <HeartSnapshotChart height={180} />
          </div>
          <BpChart bp={bp} />
          <RhythmResultCard />
        </div>
      </Section>

      {(loggedMetrics.length > 0 || metrics.steps || metrics.sleep_asleep_minutes) && (
        <Section id="vitals" title="Vitals, activity and sleep" sub="Each logged vital by day, with your 30-day median.">
          <div className="data-grid">
            {loggedMetrics.map((id) => (
              <MetricChart key={id} id={id} series={metrics[VITAL_INPUTS[id].metric]} />
            ))}
            <ActivitySleepChart summary={summary} />
          </div>
        </Section>
      )}

      <Section id="tasks" title="Daily tasks" sub="Tapping, voice and story results over time.">
        <div className="data-grid">
          <TappingHistoryChart items={tapping.data?.items} />
          <VowelHistoryChart items={vowel.data?.items} />
          <StoryHistoryChart items={story.data?.items} />
          <section className="card" aria-labelledby="task-history-title">
            <div className="card-head">
              <h3 id="task-history-title">Recent tasks</h3>
            </div>
            {history.length ? (
              <ul className="readings">
                {history.map((t) => {
                  const chip = summaryChip(t);
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
              <p className="field-hint">{recent.loading ? "Loading…" : "No tasks done yet."}</p>
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

      <Section id="readings" title="Readings and Apple Watch" sub="Your latest readings, and how your watch data gets here.">
        <div className="data-grid">
          <section className="card" aria-labelledby="readings-title">
            <div className="card-head">
              <h3 id="readings-title">Recent readings</h3>
            </div>
            {readings.length ? (
              <ul className="readings">
                {readings.slice(0, LIST_ROWS).map((r) => (
                  <li key={r.id} className={r.id === justAdded ? "just-added" : undefined}>
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
              <p className="field-hint">{loading ? "Loading your readings…" : "No readings yet. Log one above, or import your Apple Health export."}</p>
            )}
          </section>
          <AppleWatchCard onImported={onImported} />
        </div>
      </Section>
    </main>
  );
}
