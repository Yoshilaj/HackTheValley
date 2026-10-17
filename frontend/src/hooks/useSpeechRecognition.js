import { useCallback, useEffect, useRef, useState } from "react";

// Speech-to-text with the browser's Web Speech API (Chrome, Edge, Safari).
// Runs entirely in the frontend; no backend or API key needed.
const MESSAGES = {
  "not-allowed": "Microphone access was blocked. Allow it in your browser's site settings.",
  "service-not-allowed": "Microphone access was blocked. Allow it in your browser's site settings.",
  "no-speech": "Didn't catch that. Try again.",
  "audio-capture": "No microphone found.",
  network: "Speech service unavailable. Check your connection.",
};

const getRecognition = () =>
  typeof window === "undefined" ? null : window.SpeechRecognition || window.webkitSpeechRecognition || null;

// onFinal(text) is called with each finished phrase.
export default function useSpeechRecognition(onFinal) {
  const Recognition = getRecognition();
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState("");
  const recRef = useRef(null);
  const onFinalRef = useRef(onFinal);

  useEffect(() => {
    onFinalRef.current = onFinal;
  });

  const start = useCallback(() => {
    if (!Recognition || recRef.current) return;
    const rec = new Recognition();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = navigator.language || "en-US";

    rec.onresult = (e) => {
      let live = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const result = e.results[i];
        if (result.isFinal) onFinalRef.current?.(result[0].transcript.trim());
        else live += result[0].transcript;
      }
      setInterim(live);
    };
    rec.onerror = (e) => {
      if (e.error !== "aborted") setError(MESSAGES[e.error] || "Voice input stopped unexpectedly.");
    };
    rec.onend = () => {
      recRef.current = null;
      setListening(false);
      setInterim("");
    };

    setError("");
    try {
      rec.start();
      recRef.current = rec;
      setListening(true);
    } catch {
      setError("Couldn't start the microphone.");
    }
  }, [Recognition]);

  // stop() finishes the current phrase; abort() throws it away.
  const stop = useCallback(() => recRef.current?.stop(), []);
  const abort = useCallback(() => recRef.current?.abort(), []);

  useEffect(() => () => recRef.current?.abort(), []);

  return { supported: Boolean(Recognition), listening, interim, error, start, stop, abort };
}
