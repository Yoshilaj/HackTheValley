import { useEffect, useState } from "react";
import { vitals as baseVitals, readings as baseReadings } from "../data/placeholder.js";
import { USE_BACKEND, getBloodPressure, getMetricsSummary, logBloodPressure, logMetric } from "../api/client.js";
import { useSession } from "../context/SessionContext.jsx";
import useStoredState from "./useStoredState.js";
import { VITAL_INPUTS, vitalText, vitalTrendValue } from "../utils/vitals.js";
import { capitalize, relativeDay } from "../utils/dates.js";

// Vitals tiles and the readings list, with readings the user logs layered on
// top of the history: a new reading becomes the tile's latest value and joins
// its sparkline. The user can correct or delete their own readings.
//
// Backend off: every vital is placeholder history plus readings kept in this browser.
// Backend on (signed in): blood pressure and resting heart rate come from the
// backend (/api/metrics/bp, /api/metrics/summary) and new readings of those two
// are saved there. The backend can't edit or delete them, so those rows have no
// `entry`. Glucose, weight, temperature and blood oxygen have no backend data
// yet and keep working as before.

const FROM_BACKEND = new Set(["bp", "hr"]);
const RECENT = { bp: 10, hr: 7 }; // backend rows shown in the readings list

const makeId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);

// Backend responses -> { bp: [reading], hr: [reading] }, oldest first, where a
// reading is { id, vitalId, at (ms), values }; plus each tile's 7-point trend.
function fromBackend(bp, summary) {
  const bpRows = (bp.readings ?? [])
    .filter((r) => r.valid)
    .map((r) => ({ id: r.id, vitalId: "bp", at: new Date(r.measured_at).getTime(), values: { sys: r.systolic, dia: r.diastolic } }))
    .sort((a, b) => a.at - b.at);
  const hrDaily = summary.metrics?.resting_hr?.daily ?? [];
  const hrRows = hrDaily.map((d) => ({
    id: `hr-${d.date}`,
    vitalId: "hr",
    at: new Date(`${d.date}T12:00`).getTime(),
    values: { value: Math.round(d.value) },
  }));
  // The BP sparkline follows the daily average of the top number.
  const bpDays = (bp.summary?.per_day ?? []).map((d) => d.day_mean?.systolic).filter(Number.isFinite);
  return {
    rows: { bp: bpRows, hr: hrRows },
    trend: {
      bp: (bpDays.length ? bpDays : bpRows.map((r) => r.values.sys)).slice(-7).map(Math.round),
      hr: hrRows.map((r) => r.values.value).slice(-7),
    },
  };
}

const row = (r, entry) => ({
  id: r.id,
  date: capitalize(relativeDay(r.at)),
  vital: VITAL_INPUTS[r.vitalId].label,
  value: `${vitalText(r.vitalId, r.values)} ${VITAL_INPUTS[r.vitalId].unit}`,
  status: VITAL_INPUTS[r.vitalId].check(r.values),
  note: r.note,
  entry,
});

export default function useVitals() {
  const { signedIn } = useSession();
  const live = USE_BACKEND && signedIn;
  const [logged, setLogged] = useStoredState("caresync-readings", []);
  const [server, setServer] = useState(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!live) return;
    let cancelled = false;
    Promise.all([getBloodPressure(), getMetricsSummary()])
      .then(([bp, summary]) => !cancelled && setServer(fromBackend(bp, summary)))
      .catch(() => {
        /* offline: keep what we have */
      });
    return () => {
      cancelled = true;
    };
  }, [live, reload]);

  const onBackend = (id) => live && FROM_BACKEND.has(id) && server?.rows[id]?.length > 0;
  // Readings kept in this browser (all of them, or only the vitals the backend doesn't hold).
  const local = [...logged].filter((r) => !(live && FROM_BACKEND.has(r.vitalId))).sort((a, b) => a.at - b.at);

  const vitals = baseVitals.map((v) => {
    if (onBackend(v.id)) {
      const series = server.rows[v.id];
      const last = series[series.length - 1];
      return {
        ...v,
        value: vitalText(v.id, last.values),
        status: VITAL_INPUTS[v.id].check(last.values),
        lastLoggedAt: last.at,
        trend: server.trend[v.id].length ? server.trend[v.id] : v.trend,
      };
    }
    const mine = local.filter((r) => r.vitalId === v.id);
    if (!mine.length) return v;
    const last = mine[mine.length - 1];
    return {
      ...v,
      value: vitalText(v.id, last.values),
      status: last.status,
      lastLoggedAt: last.at,
      trend: [...v.trend, ...mine.map((r) => vitalTrendValue(v.id, r.values))].slice(-7),
    };
  });

  // `entry` is set on the user's own browser-kept readings, which can be edited or deleted.
  const backendRows = live && server
    ? [...FROM_BACKEND].flatMap((id) => server.rows[id].slice(-RECENT[id])).sort((a, b) => b.at - a.at).map((r) => row(r))
    : [];
  const shownOnBackend = new Set([...FROM_BACKEND].filter(onBackend).map((id) => VITAL_INPUTS[id].label));
  const readings = [
    ...backendRows,
    ...[...local].reverse().map((r) => ({ ...row(r, r), status: r.status })),
    ...baseReadings.filter((r) => !shownOnBackend.has(r.vital)),
  ];

  // values: { value } or { sys, dia }; at: time taken (ms). These throw if the backend refuses.
  const logReading = async ({ vitalId, values, at, note }) => {
    if (live && FROM_BACKEND.has(vitalId)) {
      const measuredAt = new Date(at).toISOString();
      if (vitalId === "bp") await logBloodPressure({ systolic: values.sys, diastolic: values.dia, measuredAt });
      else await logMetric("resting_hr", values.value, measuredAt);
      setReload((n) => n + 1);
      return { id: makeId(), vitalId, values, at, note, status: VITAL_INPUTS[vitalId].check(values) };
    }
    const entry = { id: makeId(), vitalId, values, at, note, status: VITAL_INPUTS[vitalId].check(values) };
    setLogged((list) => [...list, entry]);
    return entry;
  };

  const updateReading = async (id, { vitalId, values, at, note }) => {
    const entry = { id, vitalId, values, at, note, status: VITAL_INPUTS[vitalId].check(values) };
    setLogged((list) => list.map((r) => (r.id === id ? entry : r)));
    return entry;
  };

  const deleteReading = async (id) => {
    setLogged((list) => list.filter((r) => r.id !== id));
  };

  return { vitals, readings, logReading, updateReading, deleteReading };
}
