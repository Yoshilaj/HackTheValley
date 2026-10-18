import { useEffect, useState } from "react";
import { USE_BACKEND, cancelRefill, getRefills, requestRefill } from "../api/client.js";
import { useSession } from "../context/SessionContext.jsx";
import useStoredState from "./useStoredState.js";

// Refill requests the user has sent, one open request per medication:
// [{ medId, pharmacyId, method, requestedAt }].
// Backend off: kept in this browser. Backend on: /api/refills, loaded after sign-in.

const fromBackend = (r) => ({
  medId: r.medication_id,
  pharmacyId: r.pharmacy_id,
  method: r.method,
  requestedAt: new Date(r.created_at).getTime(),
});

export default function useRefills() {
  const { signedIn } = useSession();
  const [local, setLocal] = useStoredState("caresync-refills", []);
  const [remote, setRemote] = useState([]);
  const requests = USE_BACKEND ? remote : local;
  const setRequests = USE_BACKEND ? setRemote : setLocal;

  useEffect(() => {
    if (!USE_BACKEND || !signedIn) return;
    let cancelled = false;
    getRefills()
      .then((list) => !cancelled && setRemote(list.map(fromBackend)))
      .catch(() => {
        /* offline: show none open */
      });
    return () => {
      cancelled = true;
    };
  }, [signedIn]);

  // These throw if the backend refuses, so the sheets can show the error.
  const request = async ({ medicationIds, pharmacyId, method, note }) => {
    if (USE_BACKEND) {
      const saved = await requestRefill({ medicationIds, pharmacyId, method, note });
      setRequests((rs) => [...rs.filter((r) => !medicationIds.includes(r.medId)), ...saved.map(fromBackend)]);
      return;
    }
    const requestedAt = Date.now();
    setRequests((rs) => [
      ...rs.filter((r) => !medicationIds.includes(r.medId)),
      ...medicationIds.map((medId) => ({ medId, pharmacyId, method, requestedAt })),
    ]);
  };

  const cancel = async (medId) => {
    if (USE_BACKEND) await cancelRefill(medId);
    setRequests((rs) => rs.filter((r) => r.medId !== medId));
  };

  const requestFor = (medId) => requests.find((r) => r.medId === medId) ?? null;
  const isRequested = (medId) => requestFor(medId) !== null;

  return { requests, request, cancel, requestFor, isRequested };
}
