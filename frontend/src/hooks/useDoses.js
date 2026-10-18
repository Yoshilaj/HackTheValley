import { useEffect, useRef, useState } from "react";
import { USE_BACKEND, getDoses, saveDoses } from "../api/client.js";
import { useSession } from "../context/SessionContext.jsx";
import useStoredState from "./useStoredState.js";

// Today's ticked-off doses, as keys "<medication id>-<time>".
// Backend off: saved per day in this browser (earlier days cleared out).
// Backend on: GET/PUT /api/doses/<today>, so they follow the account to any device.
// Returns [taken, setTaken] like useState; setTaken also saves.
const DOSES_PREFIX = "caresync-doses-";

export default function useDoses() {
  const day = new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local
  const { signedIn } = useSession();
  const [local, setLocal] = useStoredState(`${DOSES_PREFIX}${day}`, []);
  const [remote, setRemote] = useState([]);
  const remoteRef = useRef(remote);
  remoteRef.current = remote;

  // Backend off: clear out earlier days.
  useEffect(() => {
    if (USE_BACKEND) return;
    try {
      Object.keys(localStorage)
        .filter((k) => k.startsWith(DOSES_PREFIX) && k !== `${DOSES_PREFIX}${day}`)
        .forEach((k) => localStorage.removeItem(k));
    } catch {
      /* storage unavailable */
    }
  }, [day]);

  useEffect(() => {
    if (!USE_BACKEND || !signedIn) return;
    let cancelled = false;
    getDoses(day)
      .then((res) => !cancelled && setRemote(res.taken ?? []))
      .catch(() => {
        /* offline: start from none ticked */
      });
    return () => {
      cancelled = true;
    };
  }, [day, signedIn]);

  if (!USE_BACKEND) return [local, setLocal];

  // Update at once, save in the background; a failed save is put back.
  const setTaken = (update) => {
    const prev = remoteRef.current;
    const next = typeof update === "function" ? update(prev) : update;
    remoteRef.current = next; // a second quick tap builds on this one
    setRemote(next);
    saveDoses(day, next).catch(() => setRemote(prev));
  };
  return [remote, setTaken];
}
