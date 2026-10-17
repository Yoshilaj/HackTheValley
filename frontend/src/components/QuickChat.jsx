import { useNavigate } from "react-router-dom";
import { useChatHistory } from "../context/ChatHistoryContext.jsx";
import Composer, { ASSISTANT_HINTS } from "./Composer.jsx";

// Overview prompt that starts a new assistant chat. Messages go through the
// shared chat history -> api/client.js sendChat -> POST /api/chat, so the API
// key stays on the backend and never ships to the browser.
const prompts = ["I didn't sleep well", "Side effects question", "Feeling stressed"];

export default function QuickChat() {
  const navigate = useNavigate();
  const { sendMessage } = useChatHistory();

  const send = (text) => {
    if (!text.trim()) return;
    const chatId = sendMessage(null, text, []);
    if (chatId) navigate(`/chat/${chatId}`);
  };

  return (
    <section className="card quick-chat" aria-labelledby="quick-chat-title">
      <h2 id="quick-chat-title" className="quick-chat-title">
        Anything on your mind today?
      </h2>
      <p className="small quick-chat-sub">Ask the care assistant about how you're feeling.</p>

      <Composer
        id="quick"
        allowFiles={false}
        placeholder="Type a message…"
        hints={ASSISTANT_HINTS}
        onSend={(text) => send(text)}
      />

      <div className="quick-chat-prompts">
        {prompts.map((p) => (
          <button key={p} type="button" className="chip" onClick={() => send(p)}>
            {p}
          </button>
        ))}
      </div>
    </section>
  );
}
