import { useCallback, useEffect, useRef, useState } from "react";
import { triggerDemo } from "../api/client.js";
import { useAppointments } from "../context/AppointmentsContext.jsx";
import { AUTO_BOOK_THRESHOLD, useCareAlert } from "../context/CareAlertContext.jsx";
import { useSession } from "../context/SessionContext.jsx";
import useStoredState from "../hooks/useStoredState.js";

// Dev-only "Dev access" button (rendered only in `npm run dev`) with two views:
//   User view - the app exactly as a user sees it: no dev tools, and the button
//               dims to stay out of the way. Any risk override stays applied, so
//               you can set up a scenario in dev view and present it here.
//   Dev view  - the risk override (slider, presets, reset) and what the app is
//               running on.
// Both views have the presentation triggers: a real AI call / booking e-mail
// for the latest appointment (to the sandbox phone / inbox in sandbox mode).
// The slider commits on release so dragging past the threshold doesn't fire
// the pop-up mid-drag. The chosen view is remembered in this browser.
// The button can be dragged anywhere, even mostly off screen (a strip stays
// visible to drag it back); a click without dragging opens the panel. Where it
// sits is remembered too.
const PRESETS = [14, 50, 88];
const DEMO_LABELS = { phone: "Trigger demo call", email: "Trigger demo email" };
const KEEP_VISIBLE = 16; // px of the button that stay on screen
const DRAG_START = 4; // px of movement before a press counts as a drag

// Keeps `offset` (from the button's home corner) leaving KEEP_VISIBLE px on screen.
function clamp(offset, home, size) {
  const limit = (v, homeStart, length, viewport) =>
    Math.min(viewport - KEEP_VISIBLE - homeStart, Math.max(KEEP_VISIBLE - length - homeStart, v));
  return {
    x: limit(offset.x, home.x, size.w, window.innerWidth),
    y: limit(offset.y, home.y, size.h, window.innerHeight),
  };
}

// Position of the button with no offset, and its size.
function measure(el, offset) {
  const r = el.getBoundingClientRect();
  return { home: { x: r.left - offset.x, y: r.top - offset.y }, size: { w: r.width, h: r.height } };
}

export default function DevRiskControl() {
  const { risk, devScore, setDevScore, resetAlert } = useCareAlert();
  const { session } = useSession();
  const { refresh: refreshAppointments } = useAppointments();
  const [demoBusy, setDemoBusy] = useState(null); // "phone" | "email" | null
  const [demoNote, setDemoNote] = useState("");
  const [open, setOpen] = useState(false);
  const [view, setView] = useStoredState("caresync-dev-view", "dev"); // "user" | "dev"
  const [draft, setDraft] = useState(null);
  const value = draft ?? risk.score;
  const who = session?.guest ? "Guest" : session?.email ?? (session ? `Signed in with ${session.method}` : "Signed out");

  const [offset, setOffset] = useStoredState("caresync-dev-offset", { x: 0, y: 0 });
  const pillRef = useRef(null);
  const drag = useRef(null);
  const dragged = useRef(false);

  const commit = () => {
    if (draft == null) return;
    setDevScore(draft);
    setDraft(null);
  };

  const runDemo = async (channel) => {
    setDemoBusy(channel);
    setDemoNote("");
    try {
      const out = await triggerDemo(channel);
      const sent = out.status === "requested" || out.status === "confirmed";
      setDemoNote(
        sent
          ? `${channel === "phone" ? "Calling" : "E-mail sent"} for ${out.clinic}.`
          : `Not sent: ${out.reason || out.status}`,
      );
      refreshAppointments?.();
    } catch (err) {
      setDemoNote(err.status === 401 ? "Sign in first." : `Failed: ${err.detail || err.message}`);
    } finally {
      setDemoBusy(null);
    }
  };

  const demoControls = (
    <div className="dev-demo" role="group" aria-label="Presentation triggers">
      <div className="dev-risk-foot">
        {Object.entries(DEMO_LABELS).map(([channel, label]) => (
          <button key={channel} type="button" disabled={demoBusy != null} onClick={() => runDemo(channel)}>
            {demoBusy === channel ? "Sending…" : label}
          </button>
        ))}
      </div>
      {demoNote && <p role="status">{demoNote}</p>}
    </div>
  );

  // A smaller window can't strand the button off screen.
  const refit = useCallback(() => {
    if (!pillRef.current) return;
    setOffset((o) => {
      const { home, size } = measure(pillRef.current, o);
      const next = clamp(o, home, size);
      return next.x === o.x && next.y === o.y ? o : next;
    });
  }, [setOffset]);

  useEffect(() => {
    refit();
    window.addEventListener("resize", refit);
    return () => window.removeEventListener("resize", refit);
  }, [refit]);

  const onPointerDown = (e) => {
    if (e.button !== 0) return;
    drag.current = { startX: e.clientX, startY: e.clientY, from: offset, moved: false, ...measure(pillRef.current, offset) };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved && Math.hypot(dx, dy) < DRAG_START) return;
    d.moved = true;
    setOffset(clamp({ x: d.from.x + dx, y: d.from.y + dy }, d.home, d.size));
  };

  const onPointerUp = () => {
    dragged.current = Boolean(drag.current?.moved); // the click that follows a drag doesn't toggle the panel
    drag.current = null;
  };

  const onClick = () => {
    if (dragged.current) {
      dragged.current = false;
      return;
    }
    setOpen((o) => !o);
  };

  return (
    <div
      className={`dev-risk ${view === "user" ? "user-view" : ""}`}
      style={{ transform: `translate(${offset.x}px, ${offset.y}px)` }}
    >
      {open && (
        <div className="dev-risk-panel" role="group" aria-label="Developer access">
          <div className="dev-view-toggle" role="group" aria-label="View">
            <button type="button" aria-pressed={view === "user"} onClick={() => setView("user")}>
              User view
            </button>
            <button type="button" aria-pressed={view === "dev"} onClick={() => setView("dev")}>
              Dev view
            </button>
          </div>

          {view === "user" ? (
            <p>
              You're seeing the app as a user would, with no dev tools.
              {devScore != null && ` The risk score is still set to ${devScore}.`}
            </p>
          ) : (
            <>
              <div className="dev-risk-head">
                <label htmlFor="dev-risk-range">Overall risk</label>
                <output htmlFor="dev-risk-range">{value}</output>
              </div>
              <input
                id="dev-risk-range"
                type="range"
                min="0"
                max="100"
                value={value}
                onChange={(e) => setDraft(Number(e.target.value))}
                onPointerUp={commit}
                onKeyUp={commit}
                onBlur={commit}
              />
              <div className="dev-risk-presets">
                {PRESETS.map((n) => (
                  <button
                    key={n}
                    type="button"
                    aria-pressed={value === n}
                    onClick={() => {
                      setDraft(null);
                      setDevScore(n);
                    }}
                  >
                    {n}
                  </button>
                ))}
              </div>
              <div className="dev-risk-foot">
                <button type="button" onClick={() => setDevScore(null)} disabled={devScore == null}>
                  Use real score
                </button>
                <button type="button" onClick={resetAlert}>
                  Reset alert
                </button>
              </div>
              <p>Auto-booking triggers at {AUTO_BOOK_THRESHOLD}+.</p>
              <dl className="dev-info">
                <div>
                  <dt>Session</dt>
                  <dd>{who}</dd>
                </div>
                <div>
                  <dt>Score</dt>
                  <dd>{devScore != null ? `Override ${devScore}` : "Real"}</dd>
                </div>
                <div>
                  <dt>Data</dt>
                  <dd>Placeholder (backend off)</dd>
                </div>
              </dl>
            </>
          )}
          {demoControls}
        </div>
      )}
      <button
        ref={pillRef}
        type="button"
        className="dev-risk-pill"
        aria-expanded={open}
        title="Drag to move"
        onClick={onClick}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        Dev access
      </button>
    </div>
  );
}
