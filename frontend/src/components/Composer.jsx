import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import useSpeechRecognition from "../hooks/useSpeechRecognition.js";
import usePrefersReducedMotion from "../hooks/usePrefersReducedMotion.js";
import TypewriterHint from "./TypewriterHint.jsx";

// Pill-shaped chat bar: + menu (files / photo), text box, mode picker,
// microphone (speech to text) and send. Calls onSend(text, files, mode).

const ACCEPT = ".pdf,.txt,.csv,.doc,.docx,.png,.jpg,.jpeg,.heic";
const MAX_FILES = 5;
const MAX_SIZE = 10 * 1024 * 1024; // 10 MB per file
const MAX_HEIGHT = 164; // about 6 lines

const MODES = [
  { id: "auto", label: "Auto", hint: "Picks the right depth for you" },
  { id: "quick", label: "Quick", hint: "Short, simple answers" },
  { id: "detailed", label: "Detailed", hint: "Longer explanations" },
];

// Questions the empty chat box types out in turn (see TypewriterHint). Kept
// short so they fit the box on a phone.
export const ASSISTANT_HINTS = [
  "What do you need help with today?",
  "Anything aching today?",
  "How did you sleep last night?",
  "Questions about your medications?",
  "Want help reading your vitals?",
  "Feeling stressed or run down?",
  "Need to book a doctor's visit?",
];

const join = (a, b) => (!b ? a : !a.trim() ? b : `${a.trimEnd()} ${b}`);

export const formatSize = (bytes) =>
  bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

export function FileIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}

// hints: optional list of questions to type out while the box is empty. With
// reduced motion the first one is shown as a plain placeholder instead.
const Composer = forwardRef(function Composer(
  { onSend, disabled = false, allowFiles = true, showMode = true, placeholder = "Ask CareSync anything…", hints, id = "chat" },
  ref
) {
  const [draft, setDraft] = useState("");
  const [focused, setFocused] = useState(false);
  const reduceMotion = usePrefersReducedMotion();
  const [files, setFiles] = useState([]);
  const [fileError, setFileError] = useState("");
  const [menu, setMenu] = useState(null); // null | "add" | "mode"
  const [mode, setMode] = useState("auto");
  const [tall, setTall] = useState(false);
  const rootRef = useRef(null);
  const textRef = useRef(null);
  const fileRef = useRef(null);
  const photoRef = useRef(null);
  const addBtnRef = useRef(null);
  const modeBtnRef = useRef(null);

  const speech = useSpeechRecognition((text) => setDraft((d) => join(d, text)));

  const addFiles = (list) => {
    const incoming = Array.from(list);
    const ok = incoming.filter((f) => f.size <= MAX_SIZE);
    const tooBig = incoming.find((f) => f.size > MAX_SIZE);
    setFiles([...files, ...ok].slice(0, MAX_FILES));
    if (tooBig) setFileError(`${tooBig.name} is over 10 MB.`);
    else if (files.length + ok.length > MAX_FILES) setFileError(`You can attach up to ${MAX_FILES} files.`);
    else setFileError("");
  };

  useImperativeHandle(ref, () => ({ addFiles }));

  // Grow the text box with its content, up to MAX_HEIGHT.
  useLayoutEffect(() => {
    const el = textRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
    setTall(el.scrollHeight > 48);
  }, [draft]);

  // Close menus on outside click or Esc.
  useEffect(() => {
    if (!menu) return;
    const onPointer = (e) => {
      if (!rootRef.current?.contains(e.target)) setMenu(null);
    };
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      (menu === "add" ? addBtnRef : modeBtnRef).current?.focus();
      setMenu(null);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  const pending = speech.listening ? join(draft, speech.interim) : draft;
  const canSend = Boolean(pending.trim() || files.length) && !disabled;

  // Typed-out questions replace the placeholder while the box is empty.
  const hasHints = hints?.length > 0;
  const typing = hasHints && !reduceMotion && !speech.listening && !draft;
  const shownPlaceholder = speech.listening ? "Listening…" : typing ? "" : hasHints ? hints[0] : placeholder;

  const submit = () => {
    if (!canSend) return;
    if (speech.listening) speech.abort();
    onSend(pending, files, mode);
    setDraft("");
    setFiles([]);
    setFileError("");
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const pick = (input) => {
    setMenu(null);
    input.current?.click();
  };

  const onPicked = (e) => {
    addFiles(e.target.files);
    e.target.value = "";
  };

  const current = MODES.find((m) => m.id === mode);
  const micLabel = !speech.supported
    ? "Voice input isn't supported in this browser"
    : speech.listening
      ? "Stop voice input"
      : "Start voice input";

  return (
    <div className="composer" ref={rootRef}>
      {(files.length > 0 || fileError) && (
        <div className="composer-files">
          {files.length > 0 && (
            <ul aria-label="Files to send">
              {files.map((f, i) => (
                <li key={`${f.name}-${i}`} className="file-chip">
                  <FileIcon />
                  <span className="file-name">{f.name}</span>
                  <span className="file-size">{formatSize(f.size)}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${f.name}`}
                    onClick={() => {
                      setFiles(files.filter((_, j) => j !== i));
                      setFileError("");
                    }}
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M7 7l10 10M17 7L7 17" />
                    </svg>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {fileError && (
            <p className="file-error" role="alert">
              {fileError}
            </p>
          )}
        </div>
      )}

      {speech.listening && (
        <p className="composer-live" aria-live="polite">
          <span className="live-dot" aria-hidden="true" />
          Listening… {speech.interim}
        </p>
      )}

      <form
        className={`composer-pill ${tall ? "tall" : ""}`}
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        {allowFiles && (
          <div className="composer-menu-wrap">
            <button
              ref={addBtnRef}
              type="button"
              className="cmp-btn"
              aria-label="Add files and more"
              aria-haspopup="menu"
              aria-expanded={menu === "add"}
              disabled={files.length >= MAX_FILES}
              onClick={() => setMenu(menu === "add" ? null : "add")}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 5v14M5 12h14" />
              </svg>
            </button>
            {menu === "add" && (
              <div className="composer-menu" role="menu" aria-label="Add">
                <button type="button" role="menuitem" autoFocus onClick={() => pick(fileRef)}>
                  <FileIcon />
                  <span>
                    Upload files
                    <small>PDF, Word, text or CSV</small>
                  </span>
                </button>
                <button type="button" role="menuitem" onClick={() => pick(photoRef)}>
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <rect x="3" y="5" width="18" height="14" rx="2" />
                    <circle cx="9" cy="10" r="1.6" />
                    <path d="M21 16l-5-5-8 8" />
                  </svg>
                  <span>
                    Upload photo
                    <small>Pill bottles, lab results, rashes</small>
                  </span>
                </button>
              </div>
            )}
            <input ref={fileRef} type="file" multiple accept={ACCEPT} className="sr-only" tabIndex={-1} onChange={onPicked} />
            <input ref={photoRef} type="file" multiple accept="image/*" capture="environment" className="sr-only" tabIndex={-1} onChange={onPicked} />
          </div>
        )}

        <label htmlFor={`${id}-input`} className="sr-only">
          Message the care assistant
        </label>
        <div className="composer-input">
          <textarea
            ref={textRef}
            id={`${id}-input`}
            rows={1}
            value={draft}
            placeholder={shownPlaceholder}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
          />
          {/* Its own caret is hidden while the box is focused, so there's only the real one. */}
          {typing && <TypewriterHint phrases={hints} showCaret={!focused} />}
        </div>

        {showMode && (
        <div className="composer-menu-wrap">
          <button
            ref={modeBtnRef}
            type="button"
            className="mode-btn"
            aria-label={`Response mode: ${current.label}`}
            aria-haspopup="menu"
            aria-expanded={menu === "mode"}
            onClick={() => setMenu(menu === "mode" ? null : "mode")}
          >
            <span className="mode-brand">CareSync</span>
            <span className="mode-name">{current.label}</span>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
          {menu === "mode" && (
            <div className="composer-menu right" role="menu" aria-label="Response mode">
              {MODES.map((m, i) => (
                <button
                  key={m.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={m.id === mode}
                  autoFocus={i === 0}
                  onClick={() => {
                    setMode(m.id);
                    setMenu(null);
                    modeBtnRef.current?.focus();
                  }}
                >
                  <span>
                    {m.label}
                    <small>{m.hint}</small>
                  </span>
                  {m.id === mode && (
                    <svg className="check" viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M5 12l5 5L19 7" />
                    </svg>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
        )}

        <button
          type="button"
          className={`cmp-btn mic-btn ${speech.listening ? "listening" : ""}`}
          aria-label={micLabel}
          title={micLabel}
          aria-pressed={speech.listening}
          disabled={!speech.supported}
          onClick={speech.listening ? speech.stop : speech.start}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <rect x="9" y="3" width="6" height="11" rx="3" />
            <path d="M5 11a7 7 0 0014 0M12 18v3" />
          </svg>
        </button>

        <button type="submit" className="cmp-btn cmp-send" aria-label="Send" title="Send" disabled={!canSend}>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 19V5M5 12l7-7 7 7" />
          </svg>
        </button>
      </form>

      {speech.error && (
        <p className="composer-error" role="alert">
          {speech.error}
        </p>
      )}
    </div>
  );
});

export default Composer;
