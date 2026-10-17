import { useEffect, useRef, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { useChatHistory } from "../context/ChatHistoryContext.jsx";

const suggestions = [
  "When is my next appointment?",
  "Which medications are due today?",
  "Explain my blood pressure reading",
];

export default function ChatPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { chats, pending, errors, sendMessage, retry } = useChatHistory();
  const [draft, setDraft] = useState("");
  const logRef = useRef(null);

  const chat = id ? chats.find((c) => c.id === id) : null;
  const loading = Boolean(id && pending[id]);
  const failed = Boolean(id && errors[id]);
  const messageCount = chat ? chat.messages.length : 0;

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [id, messageCount, loading, failed]);

  if (id && !chat) return <Navigate to="/chat" replace />;

  const send = (text) => {
    if (!text.trim() || loading) return;
    const chatId = sendMessage(id ?? null, text);
    setDraft("");
    if (!id && chatId) navigate(`/chat/${chatId}`);
  };

  const onSubmit = (e) => {
    e.preventDefault();
    send(draft);
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) onSubmit(e);
  };

  return (
    <main className="page chat-page">
      <header className="page-head">
        <div>
          <h1 tabIndex={-1}>{chat ? chat.title : "Care assistant"}</h1>
          <p className="subtitle">Ask about your appointments, medications or readings.</p>
        </div>
      </header>

      <section className="card chat-card" aria-label="Conversation">
        <div className="chat-log" ref={logRef} role="log" aria-live="polite">
          {!chat && (
            <div className="chat-empty">
              <p className="chat-hello">Hi Micky, how can I help?</p>
              <div className="chat-suggest">
                {suggestions.map((s) => (
                  <button key={s} className="chip" onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {chat?.messages.map((m) => (
            <div key={m.id} className={`msg ${m.role}`}>
              <p>{m.text}</p>
              {m.sources?.length > 0 && (
                <ul className="msg-sources" aria-label="Sources">
                  {m.sources.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              )}
            </div>
          ))}

          {loading && (
            <div className="msg assistant typing" role="status" aria-label="Assistant is typing">
              <span />
              <span />
              <span />
            </div>
          )}

          {failed && (
            <div className="msg assistant error">
              <p>I can't reach the care assistant right now. Please try again in a moment.</p>
              <button className="msg-retry" onClick={() => retry(id)}>
                Try again
              </button>
            </div>
          )}
        </div>

        <form className="chat-form" onSubmit={onSubmit}>
          <label htmlFor="chat-input" className="sr-only">
            Message the care assistant
          </label>
          <textarea
            id="chat-input"
            rows={2}
            value={draft}
            placeholder="Type your question…"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="btn-primary" type="submit" disabled={!draft.trim() || loading}>
            Send
          </button>
        </form>
        <p className="chat-note">Not medical advice. In an emergency, call your local emergency number.</p>
      </section>
    </main>
  );
}
