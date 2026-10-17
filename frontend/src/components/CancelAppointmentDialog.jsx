import { useEffect, useRef, useState } from "react";
import { useAppointments } from "../context/AppointmentsContext.jsx";

// Confirmation before cancelling an appointment (destructive, so it asks first).
// Native <dialog> like DetailSheet.
export default function CancelAppointmentDialog({ appointment, onClose }) {
  const ref = useRef(null);
  const { cancelVisit } = useAppointments();
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const dialog = ref.current;
    if (appointment && !dialog.open) {
      setError("");
      dialog.showModal();
    }
    if (!appointment && dialog.open) dialog.close();
  }, [appointment]);

  const cancel = async () => {
    setCancelling(true);
    setError("");
    try {
      await cancelVisit(appointment.id);
      onClose();
    } catch {
      setError("Couldn't cancel with the clinic. Try again in a moment.");
    } finally {
      setCancelling(false);
    }
  };

  const a = appointment;
  return (
    <dialog
      ref={ref}
      className="sheet confirm-sheet"
      aria-labelledby="cancel-title"
      aria-describedby="cancel-desc"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      {a && (
        <div className="sheet-body">
          <h2 id="cancel-title" className="confirm-title">
            Cancel appointment?
          </h2>
          <p id="cancel-desc" className="confirm-desc">
            {a.name}
            {a.place && a.place !== a.name ? ` at ${a.place}` : ""}, {[a.date, a.time].filter(Boolean).join(" · ")}.
            CareSync will let the clinic know.
          </p>
          {error && (
            <p className="file-error" role="alert">
              {error}
            </p>
          )}
          <div className="confirm-actions">
            <button type="button" className="btn-secondary" onClick={onClose} autoFocus>
              Keep
            </button>
            <button type="button" className="btn-danger" onClick={cancel} disabled={cancelling}>
              {cancelling ? "Cancelling…" : "Cancel appointment"}
            </button>
          </div>
        </div>
      )}
    </dialog>
  );
}
