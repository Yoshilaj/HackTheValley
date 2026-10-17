import { useEffect, useState } from "react";

// Animated placeholder for the chat box: types a question, holds it, deletes
// it and moves on to the next, looping. Decorative only (aria-hidden); the
// text box keeps its own label. Lives in its own component so each typed
// character re-renders just this span, not the whole composer.
const START_MS = 300; // before the first character
const TYPE_MS = 28; // per character, plus up to TYPE_JITTER_MS so it feels typed
const TYPE_JITTER_MS = 20;
const DELETE_MS = 14; // per character when erasing
const GAP_MS = 250; // empty box before the next question

// The full question stays up long enough to read: a moment to find it, plus
// time per word at an easy reading pace (about 230 words a minute).
const HOLD_BASE_MS = 1000;
const HOLD_PER_WORD_MS = 260;
const holdFor = (phrase) => HOLD_BASE_MS + phrase.split(/\s+/).length * HOLD_PER_WORD_MS;

export default function TypewriterHint({ phrases, showCaret = true }) {
  // phase: "start" -> "typing" -> (hold) -> "deleting" -> next phrase "typing"
  const [state, setState] = useState({ i: 0, len: 0, phase: "start" });
  const phrase = phrases[state.i % phrases.length];

  useEffect(() => {
    let delay;
    let next;
    if (state.phase === "start") {
      delay = START_MS;
      next = { ...state, phase: "typing" };
    } else if (state.phase === "typing") {
      const full = state.len >= phrase.length;
      delay = full ? holdFor(phrase) : TYPE_MS + Math.random() * TYPE_JITTER_MS;
      next = full ? { ...state, phase: "deleting" } : { ...state, len: state.len + 1 };
    } else {
      const empty = state.len === 0;
      delay = empty ? GAP_MS : DELETE_MS;
      next = empty ? { i: (state.i + 1) % phrases.length, len: 0, phase: "typing" } : { ...state, len: state.len - 1 };
    }
    const t = setTimeout(() => setState(next), delay);
    return () => clearTimeout(t);
  }, [state, phrase, phrases.length]);

  // The caret blinks while resting and stays solid while characters move.
  const resting = state.phase === "start" || (state.phase === "typing" && state.len >= phrase.length);

  return (
    <span className="typewriter" aria-hidden="true">
      {phrase.slice(0, state.len)}
      {showCaret && <span className={`typewriter-caret ${resting ? "blink" : ""}`} />}
    </span>
  );
}
