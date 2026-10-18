import ChartFrame from "./charts/ChartFrame.jsx";
import TrendLineChart from "./charts/TrendLineChart.jsx";
import BarsChart from "./charts/BarsChart.jsx";
import { PRIMARY, SECONDARY, SERIES } from "./charts/theme.js";
import { VITAL_INPUTS, VITAL_ORDER } from "../utils/vitals.js";

// The Data page's vitals charts, from the same two backend calls as the tiles:
// blood pressure per day (morning and evening occasions) and each daily metric
// with its 30-day median. Only series with data are drawn.

export function BpChart({ bp }) {
  const data = (bp?.summary?.per_day ?? []).map((d) => ({
    date: d.date,
    sysMorning: d.morning?.systolic ?? null,
    sysEvening: d.evening?.systolic ?? null,
    diaMorning: d.morning?.diastolic ?? null,
    diaEvening: d.evening?.diastolic ?? null,
  }));
  const has = (k) => data.some((d) => Number.isFinite(d[k]));
  const series = [
    { key: "sysMorning", name: "Top, morning", color: PRIMARY },
    { key: "sysEvening", name: "Top, evening", color: SECONDARY },
    { key: "diaMorning", name: "Bottom, morning", color: PRIMARY, dashed: true },
    { key: "diaEvening", name: "Bottom, evening", color: SECONDARY, dashed: true },
  ].filter((s) => has(s.key));
  const window = bp?.summary?.window_7d?.day_mean;
  return (
    <ChartFrame
      id="bp-chart"
      title="Blood pressure by day"
      tag="30 days"
      empty={series.length ? null : "Log morning and evening readings to see them here."}
      emptyAction={series.length ? null : { to: "/tasks#task-bp", label: "Log a reading" }}
      note={
        window
          ? `7-day average ${Math.round(window.systolic)}/${Math.round(window.diastolic)} mmHg over ${window.n_days} day${window.n_days === 1 ? "" : "s"}${window.sufficient ? "" : " (5 days are needed for the assessment)"}. Dashed lines are the bottom number; the goal is under 120/80.`
          : "Each point is the average of that session's readings. Dashed lines are the bottom number; the goal is under 120/80."
      }
    >
      <TrendLineChart
        data={data}
        series={series}
        unit="mmHg"
        yDomain={[60, "auto"]}
        refLines={[
          { y: 120, label: "Top goal" },
          { y: 80, label: "Bottom goal" },
        ]}
        height={220}
        valueFormat={(v) => Math.round(v)}
        label="Blood pressure per day, morning and evening"
      />
    </ChartFrame>
  );
}

// A y-range that always shows the whole healthy band, so a few readings
// clustered inside it draw as the flat line they are rather than a jagged one.
export function domainFor(values, band) {
  if (!values.length) return ["auto", "auto"];
  const lo = Math.min(...values, band?.[0] ?? Infinity);
  const hi = Math.max(...values, band?.[1] ?? -Infinity);
  const pad = Math.max((hi - lo) * 0.12, 1);
  return [Math.floor(lo - pad), Math.ceil(hi + pad)];
}

export function MetricChart({ id, series, height = 220 }) {
  const input = VITAL_INPUTS[id];
  const data = (series?.daily ?? []).filter((d) => Number.isFinite(d.value)).map((d) => ({ date: d.date, value: d.value }));
  const median = series?.baseline_30d?.median;
  const decimals = input.fields[0].decimals ?? 0;
  const fmt = (v) => (decimals ? Number(v).toFixed(decimals) : Math.round(v));
  return (
    <ChartFrame
      id={`metric-${id}`}
      title={`${input.label} by day`}
      tag="30 days"
      empty={data.length ? null : `No ${input.label.toLowerCase()} readings yet.`}
      note={
        input.band
          ? `The shaded band is the healthy range (${input.range}); the dashed line is your 30-day median${Number.isFinite(median) ? `, ${fmt(median)} ${input.unit}` : ""}.`
          : `The dashed line is your 30-day median${Number.isFinite(median) ? `, ${fmt(median)} ${input.unit}` : ""}.`
      }
    >
      <TrendLineChart
        data={data}
        series={[{ key: "value", name: input.label, color: PRIMARY }]}
        unit={input.unit}
        band={input.band ?? undefined}
        yDomain={domainFor(
          data.map((d) => d.value),
          input.band
        )}
        refLines={Number.isFinite(median) ? [{ y: median, label: "30-day median" }] : []}
        height={height}
        valueFormat={fmt}
        label={`${input.label} per day over the last 30 days`}
      />
    </ChartFrame>
  );
}

const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

export function StepsChart({ summary, height = 200 }) {
  const steps = (summary?.metrics?.steps?.daily ?? []).filter((d) => Number.isFinite(d.value)).map((d) => ({ date: d.date, steps: Math.round(d.value) }));
  const week = mean(steps.slice(-7).map((d) => d.steps));
  return (
    <ChartFrame
      id="steps-chart"
      title="Steps by day"
      tag="30 days"
      empty={steps.length ? null : "Connect your Apple Watch or import your Health export to see steps here."}
      note={week ? `Average ${Math.round(week).toLocaleString()} steps a day over the last 7 days, from your Apple Health data.` : "From your Apple Health data."}
    >
      <BarsChart data={steps} series={[{ key: "steps", name: "Steps", color: SERIES[2] }]} height={height} valueFormat={(v) => v.toLocaleString()} label="Steps per day" />
    </ChartFrame>
  );
}

export function SleepChart({ summary, height = 200 }) {
  const sleep = (summary?.metrics?.sleep_asleep_minutes?.daily ?? [])
    .filter((d) => Number.isFinite(d.value))
    .map((d) => ({ date: d.date, hours: Math.round((d.value / 60) * 10) / 10 }));
  const week = mean(sleep.slice(-7).map((d) => d.hours));
  return (
    <ChartFrame
      id="sleep-chart"
      title="Sleep by night"
      tag="30 days"
      empty={sleep.length ? null : "Sleep appears here from your Apple Health data or your check-ins."}
      note={week ? `Average ${week.toFixed(1)} hours a night over the last 7 nights. Most adults need 7 to 9.` : "From your Apple Health data or your check-ins."}
    >
      <BarsChart data={sleep} series={[{ key: "hours", name: "Asleep", color: SERIES[3] }]} unit="h" height={height} label="Hours asleep per night" />
    </ChartFrame>
  );
}

// Steps and sleep in one card (kept for pages that want the compact form).
export function ActivitySleepChart({ summary }) {
  const steps = (summary?.metrics?.steps?.daily ?? []).filter((d) => Number.isFinite(d.value)).map((d) => ({ date: d.date, steps: Math.round(d.value) }));
  const sleep = (summary?.metrics?.sleep_asleep_minutes?.daily ?? [])
    .filter((d) => Number.isFinite(d.value))
    .map((d) => ({ date: d.date, hours: Math.round((d.value / 60) * 10) / 10 }));
  if (!steps.length && !sleep.length) return null;
  return (
    <ChartFrame id="activity-sleep" title="Activity and sleep" tag="30 days" note="Steps from your Apple Health export; sleep from the export or your check-ins.">
      {steps.length > 0 && (
        <>
          <p className="chart-sub">Steps</p>
          <BarsChart data={steps} series={[{ key: "steps", name: "Steps", color: SERIES[2] }]} height={140} valueFormat={(v) => v.toLocaleString()} label="Steps per day" />
        </>
      )}
      {sleep.length > 0 && (
        <>
          <p className="chart-sub">Sleep</p>
          <BarsChart data={sleep} series={[{ key: "hours", name: "Asleep", color: SERIES[3] }]} unit="h" height={140} label="Hours asleep per night" />
        </>
      )}
    </ChartFrame>
  );
}

// The whole column, in the order of the tiles.
export default function VitalsCharts({ bp, summary }) {
  const metrics = summary?.metrics ?? {};
  return (
    <>
      <BpChart bp={bp} />
      {VITAL_ORDER.filter((id) => id !== "bp" && metrics[VITAL_INPUTS[id].metric]?.daily?.length).map((id) => (
        <MetricChart key={id} id={id} series={metrics[VITAL_INPUTS[id].metric]} />
      ))}
      <ActivitySleepChart summary={summary} />
    </>
  );
}
