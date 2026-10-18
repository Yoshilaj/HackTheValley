import { useEffect, useRef, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { useChatHistory } from "../context/ChatHistoryContext.jsx";
import AssistantReply, { SourceLinks } from "../components/AssistantReply.jsx";
import Composer, { ASSISTANT_HINTS, FileIcon, formatSize } from "../components/Composer.jsx";
import { useCareAlert } from "../context/CareAlertContext.jsx";
import { useSession } from "../context/SessionContext.jsx";
import { useProfile } from "../context/ProfileContext.jsx";
import useVitals from "../hooks/useVitals.js";
import { conditionLevel } from "../utils/risk.js";
import "../styles/chat-flat.css";

// Guests have no record to draw on, so they get general starters.
const GENERAL_SUGGESTIONS = [
  "What should a healthy blood pressure be?",
  "How can I sleep better?",
  "When should I see a doctor about a headache?",
];

// Signed-in starters come from the user's own record: their highest condition
// risk and their latest blood pressure. Falls back to the general ones for
// anything missing.
function personalSuggestions(conditions, vitals) {
  const out = [];
  const raised = (conditions ?? [])
    .map((c) => ({ c, level: conditionLevel(c) }))
    .filter(({ level }) => level !== "Low")
    .sort((a, b) => b.c.risk - a.c.risk)[0];
  if (raised) out.push(`Why is my ${raised.c.name.replace(" / ", " and ").toLowerCase()} risk ${raised.level.toLowerCase()}?`);
  const bp = vitals.find((v) => v.id === "bp");
  if (bp) out.push(`Is ${bp.value} ${bp.unit} too high for me?`);
  return [...out, ...GENERAL_SUGGESTIONS].slice(0, 3);
}

export default function ChatPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { chats, ready, pending, errors, sendMessage, retry, loadMessages } = useChatHistory();
  const { alert, alertText, openPrompt, risk } = useCareAlert();
  const { signedIn, requestSignIn } = useSession();
  const { profile: patient } = useProfile();
  const { vitals } = useVitals();
  const suggestions = signedIn ? personalSuggestions(risk?.conditions, vitals) : GENERAL_SUGGESTIONS;
  const [dragging, setDragging] = useState(false);
  const logRef = useRef(null);
  const composerRef = useRef(null);

  const chat = id ? chats.find((c) => c.id === id) : null;
  const loading = Boolean(id && pending[id]);
  const failed = Boolean(id && errors[id]);
  const needsSignIn = Boolean(id && errors[id]?.signIn);
  const messageCount = chat ? chat.messages.length : 0;
  // The reply's own bubble shows progress, so the typing dots step aside once it appears.
  const replying = Boolean(chat?.messages.at(-1)?.streaming);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [id, messageCount, loading, failed]);

  // Follow a reply as it types out, unless the user has scrolled up to read.
  const hasLog = Boolean(chat);
  useEffect(() => {
    const el = logRef.current;
    if (!el) return undefined;
    let pinned = true;
    const onScroll = () => {
      pinned = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    };
    const follow = new MutationObserver(() => {
      if (pinned) el.scrollTop = el.scrollHeight;
    });
    el.addEventListener("scroll", onScroll);
    follow.observe(el, { childList: true, subtree: true, characterData: true });
    return () => {
      el.removeEventListener("scroll", onScroll);
      follow.disconnect();
    };
  }, [id, hasLog]);

  // A conversation saved on the server loads its messages the first time it's opened.
  useEffect(() => {
    if (id) loadMessages(id);
  }, [id, chat?.remoteId, loadMessages]);

  // Wait for the account's conversations to arrive before calling a link unknown.
  if (id && !chat) {
    if (!ready) {
      return (
        <main className="page chat-page">
          <h1 className="sr-only" tabIndex={-1}>
            Care assistant
          </h1>
          <p className="chat-loading" role="status">
            Loading your conversation…
          </p>
        </main>
      );
    }
    return <Navigate to="/chat" replace />;
  }

  const send = (text, files = []) => {
    if ((!text.trim() && files.length === 0) || loading) return;
    const chatId = sendMessage(id ?? null, text, files);
    if (!id && chatId) navigate(`/chat/${chatId}`);
  };

  const onDragOver = (e) => {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    setDragging(true);
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) composerRef.current?.addFiles(e.dataTransfer.files);
  };

  return (
    <main className="page chat-page">
      {/* A new chat opens on just the greeting and a large centred message bar;
          the page title appears once the conversation has started. */}
      {chat ? (
        <header className="page-head">
          <div>
            <h1 tabIndex={-1}>Care assistant</h1>
            <p className="subtitle">Ask about your appointments, readings or daily tasks.</p>
          </div>
        </header>
      ) : (
        <h1 className="sr-only" tabIndex={-1}>
          Care assistant
        </h1>
      )}

      {/* Only while the alert needs a decision; once booked or sent it's in Appointments. */}
      {alert && ["pending", "planning", "consent", "choose"].includes(alert.status) && (
        <section className="care-banner" aria-label="Follow-up needed">
          <span className="care-banner-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M3 12h4l2-5 4 10 2-5h6" />
            </svg>
          </span>
          <div>
            <h2>{alertText.title}</h2>
            <p>
              {alertText.body}
              {alert.kind !== "agent" && ` ${alert.suspected.why}`}
            </p>
          </div>
          <button type="button" className="btn-primary" onClick={openPrompt} aria-haspopup="dialog">
            {alert.kind === "agent" ? "Review" : "Review & confirm"}
          </button>
        </section>
      )}

      <section
        className={`card chat-card ${chat ? "" : "start"} ${dragging ? "dragging" : ""}`}
        aria-label="Conversation"
        onDragOver={onDragOver}
        onDragLeave={(e) => !e.currentTarget.contains(e.relatedTarget) && setDragging(false)}
        onDrop={onDrop}
      >
        {dragging && (
          <div className="chat-drop" aria-hidden="true">
            Drop files to attach
          </div>
        )}

        <div className="chat-log" ref={logRef} role="log" aria-live="polite">
          {!chat && (
            <p className="chat-hello">{signedIn ? `Hi ${patient.name}, how can I help?` : "Hi, how can I help?"}</p>
          )}

          {chat?.messages.map((m) => (
            <div key={m.id} className={`msg ${m.role}`}>
              {m.attachments?.length > 0 && (
                <ul className="msg-files" aria-label="Attachments">
                  {m.attachments.map((f, i) => (
                    <li key={`${f.name}-${i}`}>
                      <FileIcon />
                      <span className="file-name">{f.name}</span>
                      <span className="file-size">{formatSize(f.size)}</span>
                    </li>
                  ))}
                </ul>
              )}
              {m.typed ? (
                <AssistantReply message={m} />
              ) : (
                <>
                  {m.text && <p>{m.text}</p>}
                  <SourceLinks text={m.text} sources={m.sources} />
                </>
              )}
            </div>
          ))}

          {loading && !replying && (
            <div className="msg assistant typing" role="status" aria-label="Assistant is typing">
              <span />
              <span />
              <span />
            </div>
          )}

          {chat && !chat.loaded && (
            <p className="chat-loading" role="status">
              Loading messages…
            </p>
          )}

          {failed &&
            (needsSignIn ? (
              <div className="msg assistant error">
                <p>Sign in to chat with the care assistant. Your conversations are saved to your account.</p>
                <button className="msg-retry" onClick={requestSignIn}>
                  Sign in
                </button>
              </div>
            ) : (
              <div className="msg assistant error">
                <p>I can't reach the care assistant right now. Please try again in a moment.</p>
                <button className="msg-retry" onClick={() => retry(id)}>
                  Try again
                </button>
              </div>
            ))}
        </div>

        {/* Typed-out questions only on a new chat; mid-conversation the box stays still. */}
        {/* Same position in both layouts, so the bar keeps focus as the first message sends */}
        <Composer ref={composerRef} onSend={send} disabled={loading} hints={chat ? undefined : ASSISTANT_HINTS} />
        {!chat && (
          <div className="chat-suggest">
            {suggestions.map((s) => (
              <button key={s} className="chip" onClick={() => send(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
        <p className="chat-note">
          Not medical advice. In an emergency, call your local emergency number.
        </p>
      </section>
    </main>
  );
}
