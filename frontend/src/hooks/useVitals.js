import { useCallback, useEffect, useState } from "react";
import { getBloodPressure, getMetricsSummary, logBloodPressure, logMetric } from "../api/client.js";
import { useSession } from "../context/SessionContext.jsx";
import { VITAL_INPUTS, VITAL_ORDER, vitalText } from "../utils/vitals.js";
import { capitalize, relativeDay } from "../utils/dates.js";
import { reassessNow } from "../utils/riskEvents.js";

// Vitals tiles, the readings list and the raw series for the charts, all from
// the backend: blood pressure from /api/metrics/bp (each cuff reading, plus a
// per-day summary) and the other vitals from /api/metrics/summary (one value per
// day per metric type, with a 30-day baseline). A reading the user logs goes
// straight to the backend, the data is re-read, and the risk assessment is
// asked to run again. The backend has no edit or delete for readings.

const DAYS = 30;
const RECENT_BP = 10; // backend rows shown in the readings list
const RECENT_METRIC = 7;

const EMPTY = { bp: { readings: [], summary: {} }, summary: { metrics: {} } };

// A tile's static part: label, unit, healthy range, explanation.
const base = (id) => {
  const { label, unit, range, band, about, tips } = VITAL_INPUTS[id];
  return { id, label, unit, range, band, about, tips };
};

const row = (r) => ({
  id: r.id,
  at: r.at,
  date: capitalize(relativeDay(r.at)),
  vital: VITAL_INPUTS[r.vitalId].label,
  value: `${vitalText(r.vitalId, r.values)} ${VITAL_INPUTS[r.vitalId].unit}`,
  status: VITAL_INPUTS[r.vitalId].check(r.values),
  note: r.note,
});

// Backend blood pressure -> readings { id, vitalId, at, values, note }, oldest first.
function bpRows(bp) {
  return (bp.readings ?? [])
    .filter((r) => r.valid)
    .map((r) => ({
      id: r.id,
      vitalId: "bp",
      at: new Date(r.measured_at).getTime(),
      values: { sys: r.systolic, dia: r.diastolic, ...(r.pulse != null && { pulse: r.pulse }) },
      // Shown beside the time: which home-BP session the backend filed it under.
      note:
        [r.session === "morning" ? "Morning" : r.session === "evening" ? "Evening" : null, r.irregular_pulse_flag ? "irregular pulse" : null]
          .filter(Boolean)
          .join(", ") || undefined,
    }))
    .sort((a, b) => a.at - b.at);
}

// A metric's daily series -> readings, oldest first (one per day).
function metricRows(id, series) {
  return (series?.daily ?? [])
    .filter((d) => Number.isFinite(d.value))
    .map((d) => ({ id: `${id}-${d.date}`, vitalId: id, at: new Date(`${d.date}T12:00`).getTime(), values: { value: d.value } }));
}

export default function useVitals() {
  const { signedIn } = useSession();
  const [data, setData] = useState(EMPTY);
  const [loading, setLoading] = useState(true);
  const [reloadCount, setReloadCount] = useState(0);
  const reload = useCallback(() => setReloadCount((n) => n + 1), []);

  useEffect(() => {
    if (!signedIn) {
      setData(EMPTY);
      return;
    }
    let cancelled = false;
    Promise.all([getBloodPressure(DAYS), getMetricsSummary(DAYS)])
      .then(([bp, summary]) => !cancelled && setData({ bp: bp ?? EMPTY.bp, summary: summary ?? EMPTY.summary }))
      .catch(() => {
        /* offline: keep what we have */
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [signedIn, reloadCount]);

  const { bp, summary } = data;
  const rowsById = Object.fromEntries(
    VITAL_ORDER.map((id) => [id, id === "bp" ? bpRows(bp) : metricRows(id, summary.metrics?.[VITAL_INPUTS[id].metric])])
  );

  const vitals = VITAL_ORDER.map((id) => {
    const rows = rowsById[id];
    if (!rows.length) return { ...base(id), value: "–", status: "normal", trend: [], empty: true };
    const last = rows[rows.length - 1];
    // The BP sparkline follows the daily average of the top number when the backend has it.
    const bpDays = id === "bp" ? (bp.summary?.per_day ?? []).map((d) => d.day_mean?.systolic).filter(Number.isFinite) : [];
    const trend = (bpDays.length ? bpDays : rows.map((r) => (id === "bp" ? r.values.sys : r.values.value))).slice(-7);
    return {
      ...base(id),
      value: vitalText(id, last.values),
      status: VITAL_INPUTS[id].check(last.values),
      lastLoggedAt: last.at,
      trend: trend.map((v) => (VITAL_INPUTS[id].fields[0].decimals ? Number(v.toFixed(1)) : Math.round(v))),
    };
  });

  const readings = VITAL_ORDER.flatMap((id) => rowsById[id].slice(-(id === "bp" ? RECENT_BP : RECENT_METRIC)))
    .sort((a, b) => b.at - a.at)
    .map(row);

  // Blood pressure: { vitalId: "bp", readings: [{ sys, dia, pulse? }], at, session, irregularPulse }.
  // Other vitals: { vitalId, values: { value }, at }. at: time taken (ms). Throws if the backend refuses.
  const logReading = async ({ vitalId, values, readings, at, note, session, irregularPulse }) => {
    const recordedAt = new Date(at).toISOString();
    if (vitalId === "bp") {
      await logBloodPressure({
        readings: readings.map((r) => ({ systolic: r.sys, diastolic: r.dia, pulse: r.pulse ?? null })),
        measuredAt: recordedAt,
        session,
        irregularPulse,
      });
    } else {
      await logMetric(VITAL_INPUTS[vitalId].metric, values.value, recordedAt);
    }
    reload();
    reassessNow();
    const first = vitalId === "bp" ? readings[0] : values;
    return { id: `${vitalId}-${at}`, vitalId, values: first, readings, at, note, session, status: VITAL_INPUTS[vitalId].check(first) };
  };

  return { vitals, readings, logReading, loading, reload, bp, summary };
}
