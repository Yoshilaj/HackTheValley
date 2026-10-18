import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import GuestGate from "../components/GuestGate.jsx";
import TaskCard from "../components/TaskCard.jsx";
import TappingTask from "../components/TappingTask.jsx";
import VowelTask from "../components/VowelTask.jsx";
import StoryRecallTask from "../components/StoryRecallTask.jsx";
import LogReadingSheet from "../components/LogReadingSheet.jsx";
import { useSession } from "../context/SessionContext.jsx";
import useVitals from "../hooks/useVitals.js";
import useSeries from "../hooks/useSeries.js";
import { getTaskHistory } from "../api/client.js";
import { capitalize, relativeDay } from "../utils/dates.js";
import { lastTask, taskSummary } from "../utils/tasks.js";
import { reassessNow } from "../utils/riskEvents.js";

// Everything CareSync asks for in a day, as large cards that run the task in
// place: a blood pressure reading, the keyboard tapping test, the sustained
// vowel and the story recall. Each card says whether it's done today and when
// it was last done; every result feeds the next risk assessment.
const todayIso = () => new Date().toLocaleDateString("en-CA");
const isToday = (iso) => new Date(iso).toLocaleDateString("en-CA") === todayIso();

const TASKS = [
  { id: "bp", type: null },
  { id: "tapping", type: "finger_tap" },
  { id: "vowel", type: "sustained_vowel" },
  { id: "story", type: "story_recall" },
];

// "Oct 12 · 142 / 138 taps" for a task's last valid run.
function lastLine(items, type) {
  const t = lastTask(items, type);
  if (!t) return null;
  const chip = taskSummary(t);
  return `${capitalize(relativeDay(new Date(t.created_at).getTime()))}${chip ? ` · ${chip}` : ""}`;
}

export default function DailyTasksPage() {
  const { isGuest, signedIn } = useSession();
  const { hash } = useLocation();
  const { bp, logReading } = useVitals();
  const [bpForm, setBpForm] = useState(false);
  const [bpSaved, setBpSaved] = useState(null);
  const [tick, setTick] = useState(0);
  const tasks = useSeries(() => getTaskHistory(null, 30), [tick], { enabled: signedIn });

  // A task was saved: refresh "done today" and ask for a fresh assessment.
  const onSubmitted = useCallback(() => {
    setTick((n) => n + 1);
    reassessNow();
  }, []);

  // Deep links to one card (#task-bp, #task-tapping, #task-vowel, #task-story).
  useEffect(() => {
    if (!hash?.startsWith("#task-")) return;
    const t = setTimeout(() => document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    return () => clearTimeout(t);
  }, [hash]);

  const saveBp = async (data) => {
    const entry = await logReading(data);
    setBpSaved(entry);
    setTick((n) => n + 1);
  };

  if (isGuest) {
    return (
      <main className="page">
        <header className="page-head">
          <div>
            <h1 tabIndex={-1}>Daily tasks</h1>
            <p className="subtitle">A few short checks that help spot changes early.</p>
          </div>
        </header>
        <GuestGate title="Do today's tasks">Sign in to log blood pressure and do the tapping, voice and story tasks.</GuestGate>
      </main>
    );
  }

  const items = tasks.data?.items ?? [];
  const doneToday = (type) => items.some((t) => t.task_type === type && t.valid && isToday(t.created_at));
  const todayBp = (bp?.summary?.per_day ?? []).find((d) => d.date === todayIso());
  const sessionsDone = ["morning", "evening"].filter((s) => todayBp?.[s]);
  const readings = (bp?.readings ?? [])
    .filter((r) => r.valid)
    .sort((a, b) => new Date(b.measured_at) - new Date(a.measured_at))
    .slice(0, 3);
  const lastBp = readings[0];

  const done = {
    bp: sessionsDone.length === 2,
    tapping: doneToday("finger_tap"),
    vowel: doneToday("sustained_vowel"),
    story: doneToday("story_recall"),
  };
  const doneCount = TASKS.filter((t) => done[t.id]).length;
  const last = {
    bp: lastBp ? `${capitalize(relativeDay(new Date(lastBp.measured_at).getTime()))} · ${lastBp.systolic}/${lastBp.diastolic} mmHg` : null,
    tapping: lastLine(items, "finger_tap"),
    vowel: lastLine(items, "sustained_vowel"),
    story: lastLine(items, "story_recall"),
  };
  const today = new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1 tabIndex={-1}>Daily tasks</h1>
          <p className="subtitle">Four short checks. Each one is read by your next assessment, which runs every night.</p>
        </div>
      </header>

      <div className="task-progress" role="status" aria-label={`${doneCount} of ${TASKS.length} tasks done today`}>
        <div className="task-progress-text">
          <b>
            {doneCount === TASKS.length ? "All done for today" : `${doneCount} of ${TASKS.length} done today`}
          </b>
          <span>{today}</span>
        </div>
        <div className="task-progress-segments" aria-hidden="true">
          {TASKS.map((t) => (
            <i key={t.id} className={done[t.id] ? "on" : ""} />
          ))}
        </div>
      </div>

      <LogReadingSheet open={bpForm} initial={bpForm ? { vitalId: "bp", lock: true } : null} onClose={() => setBpForm(false)} onSave={saveBp} />

      <div className="task-grid">
        <div id="task-bp">
          <TaskCard id="bp" title="Blood pressure" sub="Two readings in the morning and two in the evening" time="1 min" done={done.bp} last={last.bp}>
            <div className="task-body">
              <div className="bp-sessions" aria-label="Today's sessions">
                {["morning", "evening"].map((s) => (
                  <span key={s} className={`bp-session ${todayBp?.[s] ? "done" : ""}`}>
                    <b>{capitalize(s)}</b>
                    <small>{todayBp?.[s] ? `${Math.round(todayBp[s].systolic)}/${Math.round(todayBp[s].diastolic)} mmHg` : "Not yet"}</small>
                  </span>
                ))}
              </div>
              <p className="task-hint">
                Seated, after a minute of rest. Morning within an hour of waking, before breakfast; evening before bed. The assessment needs readings on 5 of
                the last 7 days.
              </p>
              {bpSaved && (
                <p className="task-result" role="status">
                  Saved {bpSaved.readings.map((r) => `${r.sys}/${r.dia}`).join(" and ")} mmHg
                  {bpSaved.session && bpSaved.session !== "other" ? ` (${bpSaved.session})` : ""}.
                </p>
              )}
              {readings.length > 0 && (
                <ul className="readings compact">
                  {readings.map((r) => (
                    <li key={r.id}>
                      <div>
                        <b>
                          {r.systolic}/{r.diastolic} mmHg
                        </b>
                        <small>
                          {capitalize(relativeDay(new Date(r.measured_at).getTime()))}
                          {r.session && r.session !== "other" ? ` · ${r.session}` : ""}
                        </small>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <div className="book-actions">
                <button type="button" className="btn-primary" onClick={() => setBpForm(true)} aria-haspopup="dialog">
                  Log a reading
                </button>
              </div>
            </div>
          </TaskCard>
        </div>

        <div id="task-tapping">
          <TaskCard id="tapping" title="Finger tapping" sub="Alternate S and ; for 30 seconds, each hand" time="1 min" done={done.tapping} last={last.tapping}>
            <TappingTask onSubmitted={onSubmitted} embedded />
          </TaskCard>
        </div>

        <div id="task-vowel">
          <TaskCard id="vowel" title="Sustained vowel" sub='Three steady "ahh" sounds, about five seconds each' time="1 min" done={done.vowel} last={last.vowel}>
            <VowelTask onSubmitted={onSubmitted} embedded />
          </TaskCard>
        </div>

        <div id="task-story">
          <TaskCard id="story" title="Story recall" sub="Hear two short stories, retell them, then retell the first again" time="8 min" done={done.story} last={last.story}>
            <StoryRecallTask onSubmitted={onSubmitted} embedded />
          </TaskCard>
        </div>
      </div>
    </main>
  );
}
