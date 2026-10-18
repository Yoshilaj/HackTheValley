import ChartFrame from "./charts/ChartFrame.jsx";
import TrendLineChart from "./charts/TrendLineChart.jsx";
import { PRIMARY, SECONDARY } from "./charts/theme.js";

// How the assessment tasks move over time, from GET /api/tasks/history (its
// per-task `summary` numbers). items: newest first, as the backend returns them;
// only valid, complete tasks are plotted.
const usable = (items) =>
  [...(items ?? [])].reverse().filter((t) => t.valid && t.status !== "in_progress" && t.summary && Object.keys(t.summary).length);

export function TappingHistoryChart({ items }) {
  const data = usable(items).map((t) => ({
    date: t.created_at,
    left: t.summary.left_tap_count ?? null,
    right: t.summary.right_tap_count ?? null,
    leftCv: typeof t.summary.left_iti_cv === "number" ? t.summary.left_iti_cv * 100 : null,
    rightCv: typeof t.summary.right_iti_cv === "number" ? t.summary.right_iti_cv * 100 : null,
  }));
  return (
    <ChartFrame
      id="tapping-history"
      title="Finger tapping over time"
      tag={`${data.length} session${data.length === 1 ? "" : "s"}`}
      empty={data.length ? null : "Do the tapping task to start this chart."}
      note="Taps in 30 seconds per hand, and how uneven the rhythm was (lower is steadier). The assessment compares each run with your own earlier ones."
    >
      <p className="chart-sub">Taps per 30 s</p>
      <TrendLineChart
        data={data}
        series={[
          { key: "left", name: "Left hand", color: PRIMARY },
          { key: "right", name: "Right hand", color: SECONDARY },
        ]}
        height={160}
        valueFormat={(v) => Math.round(v)}
        label="Taps per 30 seconds per hand over time"
      />
      <p className="chart-sub">Rhythm variability</p>
      <TrendLineChart
        data={data}
        series={[
          { key: "leftCv", name: "Left hand", color: PRIMARY },
          { key: "rightCv", name: "Right hand", color: SECONDARY },
        ]}
        height={140}
        unit="%"
        yDomain={[0, "auto"]}
        valueFormat={(v) => Math.round(v)}
        label="Tapping rhythm variability per hand over time"
      />
    </ChartFrame>
  );
}

export function VowelHistoryChart({ items }) {
  const rows = usable(items);
  const data = rows.map((t) => ({
    date: t.created_at,
    snr: t.summary.snr_db ?? null,
    probability: typeof t.summary.probability === "number" ? t.summary.probability * 100 : null,
  }));
  const threshold = rows.find((t) => typeof t.summary.threshold === "number")?.summary.threshold;
  const hasModel = data.some((d) => Number.isFinite(d.probability));
  return (
    <ChartFrame
      id="vowel-history"
      title='Sustained "ahh" over time'
      tag="Experimental"
      empty={data.length ? null : 'Record a sustained "ahh" to start this chart.'}
      note="The research model's score is shown for interest; it is not calibrated for this microphone and does not drive your assessment. The assessment watches your own voice features for change."
    >
      {hasModel && (
        <>
          <p className="chart-sub">Research model score</p>
          <TrendLineChart
            data={data}
            series={[{ key: "probability", name: "Model probability", color: PRIMARY }]}
            height={150}
            unit="%"
            yDomain={[0, 100]}
            yTicks={[0, 25, 50, 75, 100]}
            refLines={threshold ? [{ y: threshold * 100, label: "Model threshold" }] : []}
            valueFormat={(v) => Math.round(v)}
            label="Experimental voice-model probability per recording"
          />
        </>
      )}
      <p className="chart-sub">Recording quality (signal to noise)</p>
      <TrendLineChart
        data={data}
        series={[{ key: "snr", name: "Signal to noise", color: SECONDARY }]}
        height={130}
        unit="dB"
        yDomain={[0, "auto"]}
        refLines={[{ y: 15, label: "Minimum" }]}
        valueFormat={(v) => Math.round(v)}
        label="Signal-to-noise ratio per recording"
      />
    </ChartFrame>
  );
}

export function StoryHistoryChart({ items }) {
  const data = usable(items).map((t) => {
    const s = t.summary;
    const immediate = [s.story1_recall, s.story2_recall].filter((v) => typeof v === "number");
    return {
      date: t.created_at,
      score: typeof s.score === "number" ? s.score : (typeof t.score === "number" ? t.score : null),
      immediate: immediate.length ? immediate.reduce((a, b) => a + b, 0) / immediate.length : null,
      delayed: typeof s.story1_delayed_recall === "number" ? s.story1_delayed_recall : null,
    };
  });
  // Real sessions score 0-100; older demo rows used a smaller scale, so only pin the axis when the data fills it.
  const top = Math.max(0, ...data.flatMap((d) => [d.score, d.immediate, d.delayed]).filter(Number.isFinite));
  const full = top > 20;
  return (
    <ChartFrame
      id="story-history"
      title="Story recall over time"
      tag="Experimental"
      empty={data.length ? null : "Do the story task to start this chart."}
      note="How much of each story your retelling kept (0-100): straight after hearing it, and again after the break."
    >
      <TrendLineChart
        data={data}
        series={[
          { key: "score", name: "Overall", color: PRIMARY },
          { key: "immediate", name: "Straight after", color: SECONDARY, dashed: true },
          { key: "delayed", name: "After the break", color: SECONDARY },
        ]}
        height={170}
        yDomain={full ? [0, 100] : [0, "auto"]}
        yTicks={full ? [0, 25, 50, 75, 100] : undefined}
        valueFormat={(v) => Math.round(v)}
        label="Story recall scores over time"
      />
    </ChartFrame>
  );
}
