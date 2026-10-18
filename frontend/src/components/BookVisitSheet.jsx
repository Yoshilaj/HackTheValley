import { useEffect, useMemo, useRef, useState } from "react";
import { useAppointments } from "../context/AppointmentsContext.jsx";
import { describeSlot } from "../utils/slots.js";
import { pickVisit } from "../utils/visitMatch.js";
import { distanceNote } from "../hooks/useRecommendations.js";

// "Let CareSync book a visit": a liquid glass sheet in steps.
//   prefs   - optional days, time of day, max distance, and a free-text
//             "describe what you need" for CareSync to pick from
//   options - every open slot that fits, to choose from
//   pick    - CareSync's pick for the description, with its reason
//   confirm - "Book this appointment?" final check
//   done    - booked, now in Upcoming
// Uses native <dialog> (focus trap, Esc, backdrop) like DetailSheet.

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const TIMES = [
  { id: "morning", label: "Morning", hint: "Before 12 PM", fits: (h) => h < 12 },
  { id: "afternoon", label: "Afternoon", hint: "12 – 5 PM", fits: (h) => h >= 12 && h < 17 },
  { id: "evening", label: "Evening", hint: "After 5 PM", fits: (h) => h >= 17 },
];
const DISTANCES = [
  { km: null, label: "Any" },
  { km: 2, label: "2 km" },
  { km: 5, label: "5 km" },
  { km: 10, label: "10 km" },
];
const NO_PREFS = { days: [], times: [], maxKm: null };

const toggle = (list, v) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

// Empty preferences match everything. Soonest first.
function findOptions(recs, { days, times, maxKm }) {
  return recs
    .flatMap((rec) =>
      (rec.slots ?? []).map((iso) => ({ id: `${rec.id}@${iso}`, iso, rec, ...describeSlot(iso) }))
    )
    .filter(
      (o) =>
        (!days.length || days.includes(o.day)) &&
        (!times.length || TIMES.some((t) => times.includes(t.id) && t.fits(o.hour))) &&
        (maxKm == null || o.rec.distanceKm <= maxKm)
    )
    .sort((a, b) => a.iso.localeCompare(b.iso));
}

function summarize({ days, times, maxKm }) {
  const parts = [];
  if (days.length) parts.push(DAYS.filter((d) => days.includes(d)).join(", "));
  if (times.length) parts.push(TIMES.filter((t) => times.includes(t.id)).map((t) => t.label).join(" or "));
  if (maxKm != null) parts.push(`within ${maxKm} km`);
  return parts.length ? parts.join(" · ") : "Any day, any time, any distance";
}

const glyphOf = (rec) => rec.glyph ?? rec.name.slice(0, 2).toUpperCase();
const whenOf = (o) => `${o.day}, ${o.date} · ${o.time}`;

function BackButton({ onClick, children }) {
  return (
    <button type="button" className="book-back" onClick={onClick}>
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M15 5l-7 7 7 7" />
      </svg>
      {children}
    </button>
  );
}

// providers: recommendations plus directory entries; any with `slots` become openings.
export default function BookVisitSheet({ open, onClose, providers, location, distanceFrom, locate, locating }) {
  const ref = useRef(null);
  const { bookVisit } = useAppointments();
  const [step, setStep] = useState("prefs");
  const [prefs, setPrefs] = useState(NO_PREFS);
  const [description, setDescription] = useState("");
  const [pick, setPick] = useState(null); // { option, reason }
  const [matching, setMatching] = useState(false);
  const [matchError, setMatchError] = useState("");
  const [chosen, setChosen] = useState(null); // option being confirmed / booked
  const [backTo, setBackTo] = useState("options");
  const [booking, setBooking] = useState(false);
  const [bookError, setBookError] = useState("");

  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog.open) {
      // Each opening starts at the preferences; the choices and description are kept.
      setStep("prefs");
      setPick(null);
      setChosen(null);
      setMatchError("");
      setBookError("");
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const options = useMemo(() => findOptions(providers, prefs), [providers, prefs]);
  const hasPrefs = prefs.days.length > 0 || prefs.times.length > 0 || prefs.maxKm != null;
  const described = description.trim().length > 0;

  const findBest = async () => {
    setMatching(true);
    setMatchError("");
    try {
      const result = await pickVisit({ description: description.trim(), prefs, location, options });
      if (!result) {
        setMatchError("CareSync couldn't find a match. Try widening your preferences.");
        return;
      }
      setPick(result);
      setStep("pick");
    } catch {
      setMatchError("CareSync couldn't pick right now. Try again, or choose from the openings.");
    } finally {
      setMatching(false);
    }
  };

  const askConfirm = (o, from) => {
    setChosen(o);
    setBackTo(from);
    setBookError("");
    setStep("confirm");
  };

  const book = async () => {
    setBooking(true);
    setBookError("");
    try {
      await bookVisit(chosen.rec, chosen.iso, pick?.option === chosen ? pick.reason : undefined);
      setStep("done");
    } catch {
      setBookError("Couldn't book with the clinic. Try again in a moment.");
    } finally {
      setBooking(false);
    }
  };

  const showHeader = step !== "confirm" && step !== "done";

  return (
    <dialog
      ref={ref}
      className={`sheet book-sheet ${step === "confirm" ? "confirming" : ""}`}
      aria-labelledby="book-title"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      <div className="sheet-body">
        {showHeader && (
          <header className="sheet-head">
            <div>
              <h2 id="book-title" className="book-title">
                {step === "pick" ? "CareSync's pick" : "Book a visit"}
              </h2>
              {step === "prefs" && (
                <p className="book-sub">Tell CareSync what works for you. Everything is optional.</p>
              )}
            </div>
            <button className="sheet-close" aria-label="Close" onClick={onClose}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </header>
        )}

        {step === "prefs" && (
          <>
            <fieldset className="book-field">
              <legend>
                Days that work <span className="book-optional">Optional</span>
              </legend>
              <div className="book-chips">
                {DAYS.map((d) => (
                  <button
                    key={d}
                    type="button"
                    className="book-chip"
                    aria-pressed={prefs.days.includes(d)}
                    onClick={() => setPrefs((p) => ({ ...p, days: toggle(p.days, d) }))}
                  >
                    {d}
                  </button>
                ))}
              </div>
            </fieldset>

            <fieldset className="book-field">
              <legend>
                Time of day <span className="book-optional">Optional</span>
              </legend>
              <div className="book-chips book-times">
                {TIMES.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className="book-chip"
                    aria-pressed={prefs.times.includes(t.id)}
                    onClick={() => setPrefs((p) => ({ ...p, times: toggle(p.times, t.id) }))}
                  >
                    {t.label}
                    <small>{t.hint}</small>
                  </button>
                ))}
              </div>
            </fieldset>

            <fieldset className="book-field">
              <legend>
                How far you'll travel <span className="book-optional">Optional</span>
              </legend>
              <div className="segmented">
                {DISTANCES.map((d) => (
                  <label key={d.label}>
                    <input
                      type="radio"
                      name="book-distance"
                      checked={prefs.maxKm === d.km}
                      onChange={() => setPrefs((p) => ({ ...p, maxKm: d.km }))}
                    />
                    <span>{d.km == null ? d.label : `≤ ${d.label}`}</span>
                  </label>
                ))}
              </div>
              <div className="book-from">
                <span>{distanceNote(distanceFrom, location)}</span>
                {!location && (
                  <button type="button" className="book-link" onClick={locate} disabled={locating}>
                    {locating ? "Finding you…" : "Use my location"}
                  </button>
                )}
              </div>
            </fieldset>

            <div className="book-field book-describe">
              <label htmlFor="book-describe" className="book-describe-label">
                Describe what you need <span className="book-optional">Optional</span>
              </label>
              <textarea
                id="book-describe"
                rows={3}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="e.g. My blood pressure has been high and I get headaches in the afternoon"
                aria-describedby="book-describe-hint"
              />
              <p id="book-describe-hint" className="book-hint">
                CareSync picks the best appointment for you, within the days, times and distance above. You'll
                still confirm before anything is booked.
              </p>
              {matchError && (
                <p className="file-error" role="alert">
                  {matchError}
                </p>
              )}
            </div>

            <div className="book-actions">
              {hasPrefs && (
                <button type="button" className="btn-ghost" onClick={() => setPrefs(NO_PREFS)}>
                  Clear
                </button>
              )}
              <button
                type="button"
                className={described ? "btn-secondary" : "btn-primary"}
                onClick={() => setStep("options")}
                disabled={options.length === 0}
              >
                {options.length === 0
                  ? "No openings match"
                  : `See ${options.length} opening${options.length === 1 ? "" : "s"}`}
              </button>
              {described && (
                <button
                  type="button"
                  className="btn-primary"
                  onClick={findBest}
                  disabled={matching || options.length === 0}
                >
                  {matching ? "Finding the best fit…" : "Let CareSync pick"}
                </button>
              )}
            </div>
          </>
        )}

        {step === "options" && (
          <>
            <div className="book-options-head">
              <BackButton onClick={() => setStep("prefs")}>Preferences</BackButton>
              <p className="book-summary">{summarize(prefs)}</p>
            </div>
            <ul className="book-options" aria-label="Available openings">
              {options.map((o) => (
                <li key={o.id} className="book-option">
                  <span className="appt-icon" aria-hidden="true">
                    {glyphOf(o.rec)}
                  </span>
                  <div>
                    <p className="book-when">{whenOf(o)}</p>
                    <h3>{o.rec.name}</h3>
                    <p className="book-meta">
                      {[o.rec.specialty, o.rec.clinic, o.rec.distance].filter(Boolean).join(" · ")}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => askConfirm(o, "options")}
                    aria-label={`Book ${o.rec.name}, ${o.day} ${o.date} at ${o.time}`}
                  >
                    Book
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}

        {step === "pick" && pick && (
          <>
            <BackButton onClick={() => setStep("prefs")}>Edit request</BackButton>
            <div className="book-pick">
              <div className="book-pick-who">
                <span className="appt-icon" aria-hidden="true">
                  {glyphOf(pick.option.rec)}
                </span>
                <div>
                  <p className="book-when">{whenOf(pick.option)}</p>
                  <h3>{pick.option.rec.name}</h3>
                  <p className="book-meta">
                    {[pick.option.rec.specialty, pick.option.rec.clinic, pick.option.rec.distance]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
              </div>
              <div className="book-detail-why">
                <h3>Why this one</h3>
                <p>{pick.reason}</p>
              </div>
              <p className="book-summary">
                You asked: <q>{description.trim()}</q>
              </p>
            </div>
            <div className="book-actions">
              <button type="button" className="btn-secondary" onClick={() => setStep("options")}>
                See other openings
              </button>
              <button type="button" className="btn-primary" onClick={() => askConfirm(pick.option, "pick")}>
                Book this
              </button>
            </div>
          </>
        )}

        {/* Final check, Apple alert style */}
        {step === "confirm" && chosen && (
          <div className="confirm-body">
            <h2 id="book-title" className="confirm-title">
              Book this appointment?
            </h2>
            <p className="confirm-desc">
              {chosen.rec.name}
              {chosen.rec.clinic ? ` at ${chosen.rec.clinic}` : ""}
              <br />
              <b className="confirm-when">{whenOf(chosen)}</b>
            </p>
            {bookError && (
              <p className="file-error" role="alert">
                {bookError}
              </p>
            )}
            <div className="confirm-actions">
              <button type="button" className="btn-secondary" onClick={() => setStep(backTo)} disabled={booking}>
                Go back
              </button>
              <button type="button" className="btn-primary" onClick={book} disabled={booking} autoFocus>
                {booking ? "Booking…" : "Yes, book"}
              </button>
            </div>
          </div>
        )}

        {step === "done" && chosen && (
          <div className="book-done" role="status">
            <span className="book-check" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M5 12.5l4.5 4.5L19 7.5" />
              </svg>
            </span>
            <h2 id="book-title" className="book-title">
              Appointment booked
            </h2>
            <p className="book-when">{whenOf(chosen)}</p>
            <p>
              {chosen.rec.name}
              {chosen.rec.clinic ? `, ${chosen.rec.clinic}` : ""}
            </p>
            <p className="book-meta">
              Added to your upcoming appointments. CareSync will confirm with the clinic and send you a notification.
            </p>
            <button type="button" className="btn-primary" onClick={onClose}>
              Done
            </button>
          </div>
        )}
      </div>
    </dialog>
  );
}
