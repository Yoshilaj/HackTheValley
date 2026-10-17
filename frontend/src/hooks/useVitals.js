import { vitals as baseVitals, readings as baseReadings } from "../data/placeholder.js";
import { logReading as postReading, updateReading as patchReading, deleteReading as removeReading } from "../api/client.js";
import useStoredState from "./useStoredState.js";
import { VITAL_INPUTS, vitalText, vitalTrendValue } from "../utils/vitals.js";
import { capitalize, relativeDay } from "../utils/dates.js";

// Vitals tiles and the readings list, with readings the user logs layered on
// top of the history: a new reading becomes the tile's latest value and joins
// its sparkline. The user can correct or delete their own readings. Logged
// readings are kept in this browser until /api/readings exists; flip
// USE_BACKEND then.
const USE_BACKEND = false;

const makeId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);

export default function useVitals() {
  const [logged, setLogged] = useStoredState("caresync-readings", []);
  const oldestFirst = [...logged].sort((a, b) => a.at - b.at);

  const vitals = baseVitals.map((v) => {
    const mine = oldestFirst.filter((r) => r.vitalId === v.id);
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

  // `entry` is set on the user's own readings, which can be edited or deleted.
  const readings = [
    ...[...oldestFirst].reverse().map((r) => ({
      id: r.id,
      date: capitalize(relativeDay(r.at)),
      vital: VITAL_INPUTS[r.vitalId].label,
      value: `${vitalText(r.vitalId, r.values)} ${VITAL_INPUTS[r.vitalId].unit}`,
      status: r.status,
      note: r.note,
      entry: r,
    })),
    ...baseReadings,
  ];

  // values: { value } or { sys, dia }; at: time taken (ms). These throw if the backend refuses.
  const logReading = async ({ vitalId, values, at, note }) => {
    if (USE_BACKEND) await postReading({ vitalId, values, at: new Date(at).toISOString(), note });
    const entry = { id: makeId(), vitalId, values, at, note, status: VITAL_INPUTS[vitalId].check(values) };
    setLogged((list) => [...list, entry]);
    return entry;
  };

  const updateReading = async (id, { vitalId, values, at, note }) => {
    if (USE_BACKEND) await patchReading(id, { vitalId, values, at: new Date(at).toISOString(), note });
    const entry = { id, vitalId, values, at, note, status: VITAL_INPUTS[vitalId].check(values) };
    setLogged((list) => list.map((r) => (r.id === id ? entry : r)));
    return entry;
  };

  const deleteReading = async (id) => {
    if (USE_BACKEND) await removeReading(id);
    setLogged((list) => list.filter((r) => r.id !== id));
  };

  return { vitals, readings, logReading, updateReading, deleteReading };
}
