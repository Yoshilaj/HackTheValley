import { useCallback, useEffect, useRef, useState } from "react";

// Microphone recording for the voice tasks, on MediaRecorder.
//   state:   idle | requesting | recording | stopped | denied | unsupported | error
//   level:   0..1, the input loudness right now (for a meter)
//   elapsedS: seconds since recording started
//   start({ maxMs }) asks for the microphone and starts; stops itself at maxMs.
//   stop() -> Promise<{ blob, mimeType, ext, durationS } | null>
//   take:    the last finished recording ({ blob, mimeType, ext, durationS }), also
//            set when the time limit stopped it, so callers can watch state === "stopped".
//   reset() back to idle (releases the microphone, clears the take).
// The browser's own processing (echo cancellation, noise suppression, automatic
// gain) is turned off: the backend's quality gate measures noise and clipping,
// and those filters distort both. The container is whatever the browser can
// record (webm/opus in Chrome and Firefox, mp4 in Safari); the file extension
// tells the backend how to decode it.
const MIME_CANDIDATES = [
  ["audio/webm;codecs=opus", "webm"],
  ["audio/webm", "webm"],
  ["audio/mp4", "mp4"],
  ["audio/ogg;codecs=opus", "ogg"],
];

const supported = () =>
  typeof window !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia) && typeof window.MediaRecorder !== "undefined";

function pickMime() {
  for (const [mime, ext] of MIME_CANDIDATES) {
    if (window.MediaRecorder.isTypeSupported?.(mime)) return { mime, ext };
  }
  return { mime: undefined, ext: "webm" };
}

export const DENIED_HINT = "Allow the microphone for this site (the icon in the address bar), then try again.";

export default function useRecorder() {
  const [state, setState] = useState(() => (supported() ? "idle" : "unsupported"));
  const [level, setLevel] = useState(0);
  const [elapsedS, setElapsedS] = useState(0);
  const [error, setError] = useState("");
  const [take, setTake] = useState(null);
  const refs = useRef({ stream: null, recorder: null, chunks: [], ctx: null, raf: 0, timer: 0, maxTimer: 0, startedAt: 0, resolve: null, ext: "webm" });

  const release = useCallback(() => {
    const r = refs.current;
    cancelAnimationFrame(r.raf);
    clearInterval(r.timer);
    clearTimeout(r.maxTimer);
    r.stream?.getTracks().forEach((t) => t.stop());
    r.stream = null;
    r.ctx?.close().catch(() => {});
    r.ctx = null;
    setLevel(0);
  }, []);

  const stop = useCallback(
    () =>
      new Promise((resolve) => {
        const r = refs.current;
        if (!r.recorder || r.recorder.state === "inactive") {
          resolve(null);
          return;
        }
        r.resolve = resolve;
        r.recorder.stop();
      }),
    []
  );

  const start = useCallback(
    async ({ maxMs } = {}) => {
      if (!supported()) {
        setState("unsupported");
        return;
      }
      setError("");
      setState("requesting");
      const r = refs.current;
      try {
        r.stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
        });
      } catch (e) {
        const denied = e?.name === "NotAllowedError" || e?.name === "SecurityError";
        setState(denied ? "denied" : "error");
        setError(denied ? DENIED_HINT : e?.name === "NotFoundError" ? "No microphone was found on this device." : "Couldn't start the microphone.");
        return;
      }
      const { mime, ext } = pickMime();
      r.ext = ext;
      r.chunks = [];
      try {
        r.recorder = new MediaRecorder(r.stream, mime ? { mimeType: mime } : undefined);
      } catch {
        r.recorder = new MediaRecorder(r.stream);
      }
      r.recorder.ondataavailable = (e) => e.data?.size && r.chunks.push(e.data);
      r.recorder.onstop = () => {
        const durationS = (performance.now() - r.startedAt) / 1000;
        const type = r.recorder.mimeType || mime || "audio/webm";
        const blob = new Blob(r.chunks, { type });
        const result = { blob, mimeType: type, ext: r.ext, durationS };
        release();
        setTake(result);
        setState("stopped");
        r.resolve?.(result);
        r.resolve = null;
      };

      // Level meter from the raw samples.
      try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        r.ctx = new Ctx();
        const source = r.ctx.createMediaStreamSource(r.stream);
        const analyser = r.ctx.createAnalyser();
        analyser.fftSize = 1024;
        source.connect(analyser);
        const buf = new Float32Array(analyser.fftSize);
        const tick = () => {
          analyser.getFloatTimeDomainData(buf);
          let sum = 0;
          for (let i = 0; i < buf.length; i += 1) sum += buf[i] * buf[i];
          const rms = Math.sqrt(sum / buf.length);
          setLevel(Math.min(1, rms * 4)); // speech peaks around 0.25 RMS
          r.raf = requestAnimationFrame(tick);
        };
        r.raf = requestAnimationFrame(tick);
      } catch {
        /* no meter: recording still works */
      }

      r.recorder.start(250);
      r.startedAt = performance.now();
      setTake(null);
      setElapsedS(0);
      r.timer = setInterval(() => setElapsedS((performance.now() - r.startedAt) / 1000), 100);
      if (maxMs) r.maxTimer = setTimeout(() => stop(), maxMs);
      setState("recording");
    },
    [release, stop]
  );

  const reset = useCallback(() => {
    const r = refs.current;
    if (r.recorder && r.recorder.state !== "inactive") {
      r.resolve = null;
      r.recorder.onstop = null;
      r.recorder.stop();
    }
    r.recorder = null;
    release();
    setTake(null);
    setElapsedS(0);
    setError("");
    setState(supported() ? "idle" : "unsupported");
  }, [release]);

  useEffect(() => reset, [reset]); // release the microphone on unmount

  return { state, level, elapsedS, error, take, start, stop, reset };
}
