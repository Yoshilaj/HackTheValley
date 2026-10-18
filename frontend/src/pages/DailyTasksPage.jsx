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
import { reassessNow } from "../utils/riskEvents.js";

// Everything CareSync asks for in a day, as large cards that run the task in
// place: a blood pressure reading, the keyboard tapping test, the sustained
// vowel and the story recall. Each card says whether it's done today;
// every result feeds the next risk assessment.
const todayIso = () => new Date().toLocaleDateString("en-CA");
const isToday = (iso) => new Date(iso).toLocaleDateString("en-CA") === todayIso();

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

  const doneToday = (type) => (tasks.data?.items ?? []).some((t) => t.task_type === type && t.valid && isToday(t.created_at));
  const todayBp = (bp?.summary?.per_day ?? []).find((d) => d.date === todayIso());
  const sessionsDone = ["morning", "evening"].filter((s) => todayBp?.[s]);
  const readings = (bp?.readings ?? []).filter((r) => r.valid).slice(-3).reverse();

  const done = {
    bp: sessionsDone.length === 2,
    tapping: doneToday("finger_tap"),
    vowel: doneToday("sustained_vowel"),
    story: doneToday("story_recall"),
  };
  const doneCount = Object.values(done).filter(Boolean).length;

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1 tabIndex={-1}>Daily tasks</h1>
          <p className="subtitle">Four short checks. Each one is read by your next assessment, which runs every night.</p>
        </div>
        <span className="task-progress" role="status">
          <i style={{ width: `${(doneCount / 4) * 100}%` }} aria-hidden="true" />
          <span>{doneCount} of 4 done today</span>
        </span>
      </header>

      <LogReadingSheet open={bpForm} initial={bpForm ? { vitalId: "bp", lock: true } : null} onClose={() => setBpForm(false)} onSave={saveBp} />

      <div className="task-grid">
        <div id="task-bp">
          <TaskCard id="bp" title="Blood pressure" sub="Two readings in the morning and two in the evening" time="1 min" done={done.bp}>
            <div className="task-body">
              <div className="bp-sessions" aria-label="Today's sessions">
                {["morning", "evening"].map((s) => (
                  <span key={s} className={`bp-session ${todayBp?.[s] ? "done" : ""}`}>
                    <b>{capitalize(s)}</b>
                    <small>{todayBp?.[s] ? `${Math.round(todayBp[s].systolic)}/${Math.round(todayBp[s].diastolic)} mmHg` : "Not yet"}</small>
                  </span>
                ))}
              </div>
              <p className="field-hint">
                Seated, after a minute of rest: morning within an hour of waking, before breakfast; evening before bed.
                The assessment needs readings on 5 of the last 7 days.
              </p>
              {bpSaved && (
                <p className="field-hint" role="status">
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
          <TaskCard id="tapping" title="Finger tapping" sub="Alternate S and ; for 30 seconds, each hand" time="1 min" done={done.tapping}>
            <TappingTask onSubmitted={onSubmitted} embedded />
          </TaskCard>
        </div>

        <div id="task-vowel">
          <TaskCard id="vowel" title="Sustained vowel" sub='Three steady "ahh" sounds, about five seconds each' time="1 min" done={done.vowel}>
            <VowelTask onSubmitted={onSubmitted} embedded />
          </TaskCard>
        </div>

        <div id="task-story">
          <TaskCard id="story" title="Story recall" sub="Hear two short stories, retell them, then retell the first again" time="8 min" done={done.story}>
            <StoryRecallTask onSubmitted={onSubmitted} embedded />
          </TaskCard>
        </div>
      </div>
    </main>
  );
}
