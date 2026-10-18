import { useState } from "react";
import { createDeviceToken, getDeviceTokens, revokeDeviceToken } from "../api/client.js";
import { useSession } from "../context/SessionContext.jsx";
import useSeries from "../hooks/useSeries.js";
import AppleHealthImport from "./AppleHealthImport.jsx";
import { timeAgo } from "../utils/dates.js";

// Connecting an Apple Watch. A website can't read the watch directly, so there
// are two routes, both into the same daily metrics (POST /api/metrics):
//   1. Live: an iOS Shortcuts automation on the paired iPhone reads today's
//      Health data every day and posts it with a device token (X-Device-Token).
//      The token is created here and shown once.
//   2. History: the Health app's export.zip, uploaded below and parsed in the
//      background (AppleHealthImport).
// The device-token list shows each connected phone and when it last synced.
const API_URL = `${window.location.origin}/api/metrics`;
const LOCAL = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(window.location.hostname);

function CopyField({ value, label }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch {
      /* clipboard blocked: the value is still selectable */
    }
  };
  return (
    <div className="copy-field">
      <code>{value}</code>
      <button type="button" className="btn-secondary" onClick={copy} aria-label={`Copy ${label}`}>
        {done ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

export default function AppleWatchCard({ onImported }) {
  const { signedIn } = useSession();
  const devices = useSeries(getDeviceTokens, [], { enabled: signedIn });
  const [fresh, setFresh] = useState(null); // { id, token, label }, shown once
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const connect = async () => {
    setBusy(true);
    setError("");
    try {
      setFresh(await createDeviceToken("iPhone Shortcut"));
      devices.reload();
    } catch (err) {
      setError(err?.detail ? String(err.detail) : "Couldn't create a device token. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id) => {
    try {
      await revokeDeviceToken(id);
      if (fresh?.id === id) setFresh(null);
      devices.reload();
    } catch {
      setError("Couldn't disconnect that phone. Try again in a moment.");
    }
  };

  const list = devices.data ?? [];
  const synced = list.filter((d) => d.last_used_at);

  return (
    <section className="card watch-card" aria-labelledby="watch-title">
      <div className="watch-head">
        <span className="watch-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <rect x="7" y="6" width="10" height="12" rx="3" />
            <path d="M9 6l.6-3h4.8l.6 3M9 18l.6 3h4.8l.6-3M17 10h1.5v4H17" />
          </svg>
        </span>
        <div className="watch-head-text">
          <h3 id="watch-title">Apple Watch</h3>
          <p>
            Steps, resting heart rate, sleep, walking speed and rhythm results feed your assessment. A watch can't talk to a website, so your iPhone
            sends today's Health data here once a day.
          </p>
        </div>
        {synced.length > 0 ? <em className="badge normal">Syncing</em> : list.length > 0 ? <em className="badge watch">Not synced yet</em> : null}
      </div>

      <ol className="watch-steps">
        <li>
          <span className="watch-step-no" aria-hidden="true">
            1
          </span>
          <div className="watch-step-body">
            <b>Connect your iPhone</b>
            <p>Create a device token and keep it somewhere safe; it's shown once.</p>
            {fresh ? (
              <div className="watch-token">
                <CopyField value={fresh.token} label="device token" />
                <p className="watch-note">This is the only time the token is shown. Paste it into the Shortcut as the X-Device-Token header.</p>
              </div>
            ) : (
              <button type="button" className="btn-primary watch-connect" onClick={connect} disabled={busy}>
                {busy ? "Creating…" : list.length ? "Create another token" : "Create a device token"}
              </button>
            )}
          </div>
        </li>
        <li>
          <span className="watch-step-no" aria-hidden="true">
            2
          </span>
          <div className="watch-step-body">
            <b>Add the Shortcut</b>
            <p>
              In the Shortcuts app, make an automation that runs each morning: read today's Health samples, then "Get contents of URL" with a POST to
              this address, header <code>X-Device-Token</code> and a JSON body of samples. The exact body is in <code>docs/shortcut.md</code>.
            </p>
            <CopyField value={API_URL} label="API address" />
            {LOCAL && <p className="watch-note">This address is this computer only. On the phone, use your deployed site's address.</p>}
          </div>
        </li>
        <li>
          <span className="watch-step-no" aria-hidden="true">
            3
          </span>
          <div className="watch-step-body">
            <b>Bring in your history</b>
            <p>
              Optional. On the iPhone: Health → your picture → Export All Health Data, then upload the export.zip here. The last months of walking,
              sleep, heart rate and rhythm results are imported in the background.
            </p>
            <AppleHealthImport onImported={onImported} embedded />
          </div>
        </li>
      </ol>

      {list.length > 0 && (
        <ul className="watch-devices" aria-label="Connected phones">
          {list.map((d) => (
            <li key={d.id}>
              <div>
                <b>{d.label || "iPhone"}</b>
                <small>{d.last_used_at ? `Last synced ${timeAgo(new Date(d.last_used_at).getTime())}` : "Not synced yet"}</small>
              </div>
              <button type="button" className="row-action danger" onClick={() => revoke(d.id)} aria-label={`Disconnect ${d.label || "iPhone"}`}>
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
