import { useEffect, useId, useRef } from "react";

// Apple-style alert for actions that need a second yes. `danger` makes the
// confirm button red for destructive actions; `busy` disables the buttons while
// the action runs and `error` shows why it failed. Native <dialog> like DetailSheet.
export default function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  danger = false,
  busy = false,
  error = "",
  onConfirm,
  onClose,
}) {
  const ref = useRef(null);
  const id = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="sheet confirm-sheet"
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-desc`}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      {open && (
        <div className="sheet-body">
          <h2 id={`${id}-title`} className="confirm-title">
            {title}
          </h2>
          <p id={`${id}-desc`} className="confirm-desc">
            {description}
          </p>
          {error && (
            <p className="file-error" role="alert">
              {error}
            </p>
          )}
          <div className="confirm-actions">
            <button type="button" className="btn-secondary" onClick={onClose} disabled={busy} autoFocus>
              {cancelLabel}
            </button>
            <button type="button" className={danger ? "btn-danger" : "btn-primary"} onClick={onConfirm} disabled={busy}>
              {confirmLabel}
            </button>
          </div>
        </div>
      )}
    </dialog>
  );
}
