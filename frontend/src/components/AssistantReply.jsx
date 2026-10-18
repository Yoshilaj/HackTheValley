import { useEffect, useRef, useState } from "react";

// An assistant reply that types itself out as it streams in. Before the first
// words arrive it shows what the assistant is doing (searching the research
// library, then thinking, with the model's thinking as it goes); after, that
// thinking stays one tap away under "Show thinking". Links to the sources it
// cites appear once it has finished typing.

const MIN_RATE = 90; // characters per second
const CATCH_UP_S = 2.5; // a backlog never takes longer than this to type out
const THOUGHT_TAIL = 280; // characters of thinking shown while waiting

// Replies that have finished typing, so they show whole when the chat is reopened.
const typedOut = new Set();

const prefersReducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

// Reveals `text` a few characters per frame, speeding up when it falls behind.
function useTypewriter(id, text, animate) {
  const [count, setCount] = useState(() => (animate && !typedOut.has(id) && !prefersReducedMotion() ? 0 : Infinity));
  const countRef = useRef(count);

  useEffect(() => {
    if (countRef.current >= text.length) return undefined;
    let frame;
    let last = performance.now();
    let carry = 0;
    const tick = (now) => {
      const backlog = text.length - countRef.current;
      carry += ((now - last) / 1000) * Math.max(MIN_RATE, backlog / CATCH_UP_S);
      last = now;
      const step = Math.floor(carry);
      if (step > 0) {
        carry -= step;
        countRef.current = Math.min(text.length, countRef.current + step);
        setCount(countRef.current);
      }
      if (countRef.current < text.length) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [text]);

  return [text.slice(0, count), count < text.length];
}

// Gemini's thinking summaries come as "**Heading**\n\nParagraph".
const plain = (thinking) => thinking.replace(/\*\*/g, "").trim();

function tail(text) {
  if (text.length <= THOUGHT_TAIL) return text;
  const cut = text.slice(-THOUGHT_TAIL);
  return `…${cut.slice(cut.indexOf(" ") + 1)}`;
}

// The [n] numbers the answer cites, including "[1, 3]" and "[1-2]" (as the backend counts them).
function citedNumbers(text) {
  const nums = new Set();
  for (const [, group] of text.matchAll(/\[(\d+(?:\s*[,\-–]\s*\d+)*)\]/g)) {
    for (const part of group.split(/\s*,\s*/)) {
      const [a, b = a] = part.split(/\s*[-–]\s*/).map(Number);
      for (let n = Math.min(a, b); n <= Math.max(a, b); n++) nums.add(n);
    }
  }
  return nums;
}

// Only the sources the answer cites, one link per publication (several passages
// can come from the same one), labelled with every [n] that points to it.
export function SourceLinks({ text, sources }) {
  const cited = citedNumbers(text ?? "");
  const byLink = new Map();
  for (const s of sources ?? []) {
    if (!s?.url || !cited.has(s.n)) continue;
    const entry = byLink.get(s.url) ?? { ...s, ns: [] };
    entry.ns.push(s.n);
    byLink.set(s.url, entry);
  }
  if (byLink.size === 0) return null;
  return (
    <ul className="msg-sources" aria-label="Sources">
      {[...byLink.values()].map((s) => (
        <li key={s.url}>
          <a href={s.url} target="_blank" rel="noopener noreferrer">
            <span className="msg-source-n">[{s.ns.join(", ")}]</span> {s.title}
            {s.year ? ` (${s.year})` : ""}
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M14 5h5v5M19 5l-8 8M17 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h4" />
            </svg>
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        </li>
      ))}
    </ul>
  );
}

export default function AssistantReply({ message }) {
  const { id, text = "", typed, streaming, stage, thinking, sources } = message;
  const [shown, typing] = useTypewriter(id, text, Boolean(typed));
  const waiting = streaming && !text;

  useEffect(() => {
    if (typed && !streaming && !typing) typedOut.add(id);
  }, [id, typed, streaming, typing]);

  // aria-busy: screen readers read the reply once it's complete, not letter by letter.
  return (
    <div aria-busy={typing || streaming}>
      {waiting && (
        <p className="reply-stage" role="status">
          <span className="reply-pulse" aria-hidden="true" />
          {stage === "searching" ? "Searching the research library…" : "Thinking…"}
        </p>
      )}
      {waiting && thinking && <p className="reply-thinking">{tail(plain(thinking))}</p>}
      {!waiting && thinking && (
        <details className="reply-thought">
          <summary>Show thinking</summary>
          <p>{plain(thinking)}</p>
        </details>
      )}
      {shown && (
        <p>
          {shown}
          {(typing || streaming) && <span className="typewriter-caret reply-caret" aria-hidden="true" />}
        </p>
      )}
      {!typing && !streaming && <SourceLinks text={text} sources={sources} />}
    </div>
  );
}
