import { useMemo, useState } from "react";
import { Link, NavLink, useMatch, useNavigate } from "react-router-dom";
import { useChatHistory } from "../context/ChatHistoryContext.jsx";

const DAY = 24 * 60 * 60 * 1000;

function bucketOf(ts) {
  const startOfToday = new Date().setHours(0, 0, 0, 0);
  if (ts >= startOfToday) return "Today";
  if (ts >= startOfToday - DAY) return "Yesterday";
  if (ts >= startOfToday - 7 * DAY) return "Previous 7 days";
  return "Older";
}

const order = ["Today", "Yesterday", "Previous 7 days", "Older"];

export default function HistorySidebar({ collapsed, onToggle }) {
  const { chats, deleteChat } = useChatHistory();
  const [query, setQuery] = useState("");
  const activeMatch = useMatch("/chat/:id");
  const navigate = useNavigate();

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const sorted = [...chats]
      .filter((c) => !q || c.title.toLowerCase().includes(q))
      .sort((a, b) => b.updatedAt - a.updatedAt);
    return order
      .map((label) => ({ label, items: sorted.filter((c) => bucketOf(c.updatedAt) === label) }))
      .filter((g) => g.items.length);
  }, [chats, query]);

  const remove = (chat) => {
    if (!window.confirm(`Delete "${chat.title}"?`)) return;
    deleteChat(chat.id);
    if (activeMatch?.params.id === chat.id) navigate("/chat");
  };

  return (
    <aside className={`history ${collapsed ? "collapsed" : ""}`} aria-label="Chat history">
      <div className="history-head">
        <button
          className="icon-btn"
          onClick={onToggle}
          aria-expanded={!collapsed}
          aria-controls="history-body"
          aria-label={collapsed ? "Open chat history" : "Collapse chat history"}
        >
          <svg viewBox="0 0 24 24">
            <rect x="4" y="5" width="16" height="14" rx="3" />
            <path d="M9 5v14" />
          </svg>
        </button>
        <h2 className="history-title">Chat history</h2>
        {collapsed && (
          <Link to="/chat" className="icon-btn rail-new" aria-label="New chat">
            <svg viewBox="0 0 24 24">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </Link>
        )}
      </div>

      <div className="history-body" id="history-body">
        <Link to="/chat" className="btn-primary new-chat">
          <span aria-hidden="true">+</span> New chat
        </Link>

        <label htmlFor="history-search" className="sr-only">
          Search conversations
        </label>
        <input
          id="history-search"
          className="history-search"
          type="search"
          placeholder="Search chats"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />

        <nav className="history-list" aria-label="Conversations">
          {chats.length === 0 && <p className="history-empty">No conversations yet. Start one with "New chat".</p>}
          {chats.length > 0 && groups.length === 0 && <p className="history-empty">No chats match "{query}".</p>}

          {groups.map((g) => (
            <section key={g.label} aria-label={g.label}>
              <h3 className="history-group">{g.label}</h3>
              <ul>
                {g.items.map((c) => (
                  <li key={c.id}>
                    <NavLink to={`/chat/${c.id}`} className={({ isActive }) => (isActive ? "active" : "")}>
                      {c.title}
                    </NavLink>
                    <button className="history-delete" onClick={() => remove(c)} aria-label={`Delete chat: ${c.title}`}>
                      <svg viewBox="0 0 24 24">
                        <path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" />
                      </svg>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </nav>
      </div>
    </aside>
  );
}
