import { cancelRefill, requestRefill } from "../api/client.js";
import useStoredState from "./useStoredState.js";

// Refill requests the user has sent, one open request per medication. Kept in
// this browser until /api/refills exists; flip USE_BACKEND then.
const USE_BACKEND = false;

export default function useRefills() {
  const [requests, setRequests] = useStoredState("caresync-refills", []);

  // These throw if the backend refuses, so the sheets can show the error.
  const request = async ({ medicationIds, pharmacyId, method, note }) => {
    if (USE_BACKEND) await requestRefill({ medicationIds, pharmacyId, method, note });
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
