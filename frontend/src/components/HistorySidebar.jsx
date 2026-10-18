import { useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, useMatch, useNavigate } from "react-router-dom";
import { useChatHistory } from "../context/ChatHistoryContext.jsx";
import LogoMark from "./LogoMark.jsx";
import SideNav from "./SideNav.jsx";

// Full-height left sidebar on every page. Collapsed it is an icon rail;
// expanded it shows the brand, main navigation, chat actions and past chats.
const DAY = 24 * 60 * 60 * 1000;

function bucketOf(ts) {
  const startOfToday = new Date().setHours(0, 0, 0, 0);
  if (ts >= startOfToday) return "Today";
  if (ts >= startOfToday - DAY) return "Yesterday";
  if (ts >= startOfToday - 7 * DAY) return "Previous 7 days";
  return "Older";
}

const order = ["Today", "Yesterday", "Previous 7 days", "Older"];
const HISTORY_OPEN_KEY = "caresync-history-open";

// Discreet date beside each chat: time for today/yesterday, weekday this
// week, otherwise the date.
function shortWhen(ts) {
  const d = new Date(ts);
  const bucket = bucketOf(ts);
  if (bucket === "Today" || bucket === "Yesterday") return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  if (bucket === "Previous 7 days") return d.toLocaleDateString("en-US", { weekday: "short" });
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}

const fullWhen = (ts) =>
  new Date(ts).toLocaleString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });

// Whether the chat history dropdown is open; remembered, closed by default.
function useHistoryOpen() {
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(HISTORY_OPEN_KEY) === "1";
    } catch {
      return false;
    }
  });
  const set = (next) => {
    setOpen(next);
    try {
      localStorage.setItem(HISTORY_OPEN_KEY, next ? "1" : "0");
    } catch {
      /* storage unavailable */
    }
  };
  return [open, set];
}

const Icon = ({ children }) => (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    {children}
  </svg>
);

export default function HistorySidebar({ collapsed, onToggle }) {
  const { chats, deleteChat } = useChatHistory();
  const [query, setQuery] = useState("");
  const [focusSearch, setFocusSearch] = useState(false);
  const [historyOpen, setHistoryOpen] = useHistoryOpen();
  const searchRef = useRef(null);
  const asideRef = useRef(null);
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

  // Search from the rail: expand and open the history, then focus the box once it renders.
  useEffect(() => {
    if (focusSearch && !collapsed && historyOpen) {
      searchRef.current?.focus();
      setFocusSearch(false);
    }
  }, [focusSearch, collapsed, historyOpen]);

  const showHistory = () => {
    if (collapsed) onToggle();
    setHistoryOpen(true);
  };

  const openSearch = () => {
    showHistory();
    setFocusSearch(true);
  };

  // Right edge: click to toggle, or drag it in/out. While dragging the column
  // follows the pointer and the sidebar switches mode at the halfway point;
  // on release it snaps to the rail or full width.
  const onEdgeDown = (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const edge = e.currentTarget;
    const shell = asideRef.current.parentElement;
    // Rail and open widths come from the CSS (--history-rail / --history-open).
    const css = getComputedStyle(shell);
    const min = parseFloat(css.getPropertyValue("--history-rail")) || 80;
    const max = parseFloat(css.getPropertyValue("--history-open")) || 248;
    const startX = e.clientX;
    const startW = asideRef.current.getBoundingClientRect().right - shell.getBoundingClientRect().left;
    let dragged = false;
    let isCollapsed = collapsed;
    edge.setPointerCapture(e.pointerId);

    const onMove = (ev) => {
      const dx = ev.clientX - startX;
      if (!dragged && Math.abs(dx) < 4) return;
      // Data attribute, not a class, so React re-renders of the shell don't wipe it.
      if (!dragged) shell.dataset.resizing = "";
      dragged = true;
      const w = Math.min(max, Math.max(min, startW + dx));
      shell.style.setProperty("--history-w", `${w}px`);
      const shouldCollapse = w < (min + max) / 2;
      if (shouldCollapse !== isCollapsed) {
        isCollapsed = shouldCollapse;
        onToggle();
      }
    };
    const onUp = () => {
      edge.removeEventListener("pointermove", onMove);
      edge.removeEventListener("pointerup", onUp);
      edge.removeEventListener("pointercancel", onUp);
      delete shell.dataset.resizing;
      shell.style.removeProperty("--history-w");
      if (!dragged) onToggle();
    };
    edge.addEventListener("pointermove", onMove);
    edge.addEventListener("pointerup", onUp);
    edge.addEventListener("pointercancel", onUp);
  };

  const remove = (chat) => {
    if (!window.confirm(`Delete "${chat.title}"?`)) return;
    deleteChat(chat.id);
    if (activeMatch?.params.id === chat.id) navigate("/chat");
  };

  return (
    <aside ref={asideRef} className={`history ${collapsed ? "collapsed" : ""}`} aria-label="Sidebar">
        {/* Pointer-only shortcut; keyboard users have the toggle button. */}
        <div
          className="history-edge"
          aria-hidden="true"
          title={collapsed ? "Click or drag to expand" : "Click or drag to collapse"}
          onPointerDown={onEdgeDown}
        />
        <div className="history-scroll">
        <div className="history-top">
          <Link to="/" className="history-brand" aria-label="CareSync home">
            <LogoMark />
            <span className="history-label">
              care<b>sync</b>
            </span>
          </Link>
          <button
            className="history-icon toggle"
            onClick={onToggle}
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            <Icon>
              <rect x="4" y="5" width="16" height="14" rx="3" />
              <path d="M9 5v14" />
            </Icon>
          </button>
        </div>

        <SideNav collapsed={collapsed} />
        <div className="history-divider" role="presentation" />

        {/* A new chat starts from "CareSync" in the main navigation above. */}
        <nav className="history-actions" aria-label="Chat actions">
          {collapsed ? (
            <>
              <button type="button" className="history-row" title="Search chats" aria-label="Search chats" onClick={openSearch}>
                <Icon>
                  <circle cx="11" cy="11" r="7" />
                  <path d="M20 20l-3.5-3.5" />
                </Icon>
              </button>
              <button type="button" className="history-row" title="Chat history" aria-label="Show chat history" onClick={showHistory}>
                <Icon>
                  <circle cx="12" cy="12" r="8" />
                  <path d="M12 8v4l3 2" />
                </Icon>
              </button>
            </>
          ) : (
            <button
              type="button"
              className="history-row history-toggle"
              aria-expanded={historyOpen}
              aria-controls="history-panel"
              onClick={() => setHistoryOpen(!historyOpen)}
            >
              <Icon>
                <circle cx="12" cy="12" r="8" />
                <path d="M12 8v4l3 2" />
              </Icon>
              <span className="history-label">Chat history</span>
              {chats.length > 0 && <span className="history-count">{chats.length}</span>}
              <svg className="history-chevron" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
          )}
        </nav>

        {!collapsed && historyOpen && (
          <div className="history-panel" id="history-panel">
            <div className="history-search-wrap">
              <Icon>
                <circle cx="11" cy="11" r="7" />
                <path d="M20 20l-3.5-3.5" />
              </Icon>
              <label htmlFor="history-search" className="sr-only">
                Search chats
              </label>
              <input
                ref={searchRef}
                id="history-search"
                className="history-search"
                type="search"
                placeholder="Search chats"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>

            <nav className="history-list" aria-label="Past chats">
              {chats.length === 0 && <p className="history-empty">No conversations yet. Start one from CareSync above.</p>}
              {chats.length > 0 && groups.length === 0 && <p className="history-empty">No chats match "{query}".</p>}

              {groups.map((g) => (
                <section key={g.label} aria-label={g.label}>
                  <h3 className="history-group">{g.label}</h3>
                  <ul>
                    {g.items.map((c) => (
                      <li key={c.id}>
                        <NavLink to={`/chat/${c.id}`} className={({ isActive }) => (isActive ? "active" : "")}>
                          <span className="history-title">{c.title}</span>
                          <time className="history-time" dateTime={new Date(c.updatedAt).toISOString()} title={fullWhen(c.updatedAt)}>
                            {shortWhen(c.updatedAt)}
                          </time>
                        </NavLink>
                        <button className="history-delete" onClick={() => remove(c)} aria-label={`Delete chat: ${c.title}`}>
                          <Icon>
                            <path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" />
                          </Icon>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </nav>
          </div>
        )}
        </div>
      </aside>
  );
}
