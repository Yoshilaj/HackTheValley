import { useState } from "react";
import useMedications from "../hooks/useMedications.js";
import useDoses from "../hooks/useDoses.js";
import useRefills from "../hooks/useRefills.js";
import RefillSheet from "../components/RefillSheet.jsx";
import MedicationSheet from "../components/MedicationSheet.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import GuestGate from "../components/GuestGate.jsx";
import { useSession } from "../context/SessionContext.jsx";

// Medications, today's ticked-off doses and refills come from hooks (the
// backend when it's on, this browser otherwise).
export default function MedicationsPage() {
  const { medications: medicationList } = useMedications();
  // One entry per scheduled dose, e.g. Metformin at 8:00 AM and again at 6:00 PM.
  const doses = medicationList.flatMap((m) =>
    m.times.map((time) => ({ key: `${m.id}-${time}`, name: m.name, dose: m.dose, time }))
  );
  const [taken, setTaken] = useDoses();
  const { request, cancel, requestFor, isRequested } = useRefills();
  const [refill, setRefill] = useState(null); // null, or { preselect?: [ids] } while the refill form is open
  const [detailId, setDetailId] = useState(null); // medication whose card was opened
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState("");

  const toggle = (key) => setTaken((t) => (t.includes(key) ? t.filter((k) => k !== key) : [...t, key]));
  const detail = medicationList.find((m) => m.id === detailId) ?? null;
  const { isGuest } = useSession();

  const confirmCancel = async () => {
    setCancelling(true);
    setCancelError("");
    try {
      await cancel(detail.id);
      setCancelOpen(false);
    } catch {
      setCancelError("Couldn't cancel the request. Try again in a moment.");
    } finally {
      setCancelling(false);
    }
  };

  if (isGuest) {
    return (
      <main className="page">
        <header className="page-head">
          <div>
            <h1 tabIndex={-1}>Medications</h1>
            <p className="subtitle">What you take, when, and when to refill.</p>
          </div>
        </header>
        <GuestGate title="Keep track of your medications">
          Sign in to see your daily schedule, tick off doses and request refills from your pharmacy.
        </GuestGate>
      </main>
    );
  }

  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1 tabIndex={-1}>Medications</h1>
          <p className="subtitle">What you're taking now, and what's due today.</p>
        </div>
        <button className="btn-primary" onClick={() => setRefill({})} aria-haspopup="dialog">
          Request refill
        </button>
      </header>

      <RefillSheet
        open={refill !== null}
        preselect={refill?.preselect}
        onClose={() => setRefill(null)}
        medications={medicationList}
        isRequested={isRequested}
        onSubmit={request}
      />
      <MedicationSheet
        med={detail}
        request={detail ? requestFor(detail.id) : null}
        onClose={() => setDetailId(null)}
        onRequestRefill={() => {
          setDetailId(null);
          setRefill({ preselect: [detail.id] });
        }}
        onCancelRequest={() => setCancelOpen(true)}
      />
      {/* Opens on top of the medication sheet */}
      <ConfirmDialog
        open={cancelOpen}
        title="Cancel refill request?"
        description={detail ? `Your ${detail.name} refill won't be sent to the pharmacy. You can request it again any time.` : ""}
        confirmLabel="Cancel request"
        cancelLabel="Keep request"
        danger
        busy={cancelling}
        error={cancelError}
        onConfirm={confirmCancel}
        onClose={() => {
          setCancelOpen(false);
          setCancelError("");
        }}
      />

      <section className="card" aria-labelledby="schedule-title">
        <div className="card-head">
          <h2 id="schedule-title">Today's schedule</h2>
          <span className="progress-text" role="status">
            {taken.length} of {doses.length} taken
          </span>
        </div>
        <div
          className="progress"
          role="progressbar"
          aria-label="Doses taken today"
          aria-valuemin={0}
          aria-valuemax={doses.length}
          aria-valuenow={taken.length}
        >
          <span style={{ width: `${doses.length ? (taken.length / doses.length) * 100 : 0}%` }} />
        </div>
        <ul className="schedule">
          {doses.map((d) => (
            <li key={d.key}>
              <label className="dose-check">
                <input type="checkbox" checked={taken.includes(d.key)} onChange={() => toggle(d.key)} />
                <span className="dose-time">{d.time}</span>
                <span className="dose-name">
                  {d.name} <small>{d.dose}</small>
                </span>
              </label>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="current-title">
        <h2 id="current-title" className="section-title">
          Current medications
        </h2>
        <ul className="med-grid">
          {medicationList.map((m) => (
            <li className="card med-card tappable" key={m.id}>
              {/* Stretched button: the whole card opens its details */}
              <button type="button" className="card-hit" onClick={() => setDetailId(m.id)} aria-haspopup="dialog">
                <span className="sr-only">{m.name} details</span>
              </button>
              <div className="med-top">
                <span className="med-glyph" aria-hidden="true">
                  {m.glyph}
                </span>
                <div>
                  <h3>{m.name}</h3>
                  <p className="appt-meta">{m.dose}</p>
                </div>
                {isRequested(m.id) ? (
                  <em className="badge requested">Refill requested</em>
                ) : (
                  m.refills <= 1 && <em className="badge watch">Refill soon</em>
                )}
              </div>
              <dl className="med-facts">
                <div>
                  <dt>How often</dt>
                  <dd>{m.frequency}</dd>
                </div>
                <div>
                  <dt>For</dt>
                  <dd>{m.purpose}</dd>
                </div>
                <div>
                  <dt>Prescribed by</dt>
                  <dd>{m.prescriber}</dd>
                </div>
                <div>
                  <dt>Refills left</dt>
                  <dd>{m.refills}</dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
