import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { createDeviceToken, getDeviceTokens } from "../api/client.js";

// Pairing the PD Companion iPhone app (ios/), used by the Apple Watch card and
// the onboarding. "Show pairing code" issues a device token and shows it as a
// QR code holding pdcompanion://pair?api=…&token=…, scanned in the app or the
// Camera (on an iPhone, "Open in the app" follows the same link). The app then
// posts Health data with that token (X-Device-Token), so this polls the token
// list until the token's first use and then says Connected.
const LOCAL = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(window.location.hostname);
// The app posts to {api}/api/metrics. Vercel forwards /api to the backend, so the
// site's own origin works in production; VITE_PAIR_API_URL overrides it (e.g. a
// tunnel or LAN address while testing a phone against a local backend).
const PAIR_API = import.meta.env.VITE_PAIR_API_URL || window.location.origin;
const IS_IPHONE = /iPhone|iPod/.test(navigator.userAgent);
const POLL_MS = 3000;

function pairUrl(token) {
  return `pdcompanion://pair?api=${encodeURIComponent(PAIR_API)}&token=${encodeURIComponent(token)}`;
}

function PairQr({ value }) {
  const [svg, setSvg] = useState("");
  useEffect(() => {
    let live = true;
    QRCode.toString(value, { type: "svg", margin: 1, errorCorrectionLevel: "M" })
      .then((out) => live && setSvg(out))
      .catch(() => live && setSvg(""));
    return () => {
      live = false;
    };
  }, [value]);
  return <div className="watch-qr" role="img" aria-label="Pairing QR code" dangerouslySetInnerHTML={{ __html: svg }} />;
}

// onChange: called when a token is created and when the app first syncs, so a
// parent showing the device list can reload it. onPaired: the first sync.
export default function WatchPairing({ onChange, onPaired }) {
  const [pairing, setPairing] = useState(null); // { id, token, label } until the app's first sync
  const [paired, setPaired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const start = async () => {
    setBusy(true);
    setError("");
    try {
      setPairing(await createDeviceToken("iPhone app"));
      onChange?.();
    } catch (err) {
      setError(err?.detail ? String(err.detail) : "Couldn't create a pairing code. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  // While the QR is up, watch for the app's first sync on that token.
  useEffect(() => {
    if (!pairing || paired) return undefined;
    let live = true;
    const check = async () => {
      try {
        const list = await getDeviceTokens();
        if (live && list.some((d) => d.id === pairing.id && d.last_used_at)) {
          setPaired(true);
          onChange?.();
          onPaired?.();
        }
      } catch {
        /* keep polling; a dropped request isn't an error worth showing */
      }
    };
    const t = setInterval(check, POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [pairing, paired]); // eslint-disable-line react-hooks/exhaustive-deps

  if (paired) return <p className="watch-paired">Connected. Your iPhone sent its first sync; it keeps syncing in the background.</p>;

  if (pairing)
    return (
      <div className="watch-pair">
        <PairQr value={pairUrl(pairing.token)} />
        <div className="watch-pair-text">
          <p>Open the PD Companion app and tap Scan QR code, or point the iPhone Camera at the code, then allow Health access.</p>
          {IS_IPHONE && (
            <a className="btn-primary" href={pairUrl(pairing.token)}>
              Open in the app
            </a>
          )}
          <p className="watch-note">Waiting for the first sync… Anyone with this code can send data to your account, so keep it private.</p>
          {LOCAL && !import.meta.env.VITE_PAIR_API_URL && (
            <p className="watch-note">This code points the phone at this computer. Set VITE_PAIR_API_URL to a tunnel address to test locally.</p>
          )}
        </div>
      </div>
    );

  return (
    <>
      <p>Install the PD Companion app, then show a pairing code here and scan it. Nothing to copy or type.</p>
      <button type="button" className="btn-primary watch-connect" onClick={start} disabled={busy}>
        {busy ? "Creating…" : "Show pairing code"}
      </button>
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}
