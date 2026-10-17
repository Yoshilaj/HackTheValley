import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { sendChat } from "../api/client.js";

// Conversations: { id, title, createdAt, updatedAt, messages: [{ id, role, text, sources? }] }
// Stored in localStorage so history survives a refresh. Swap for an API later.
const STORAGE_KEY = "caresync-chats";
const ChatHistoryContext = createContext(null);

function loadChats() {
  try {
    const data = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

const makeId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
const titleFrom = (text) => (text.length > 40 ? `${text.slice(0, 40).trimEnd()}…` : text);
const without = (obj, key) => {
  const { [key]: _removed, ...rest } = obj;
  return rest;
};

export function ChatHistoryProvider({ children }) {
  const [chats, setChats] = useState(loadChats);
  const [pending, setPending] = useState({}); // chat id -> waiting for a reply
  const [errors, setErrors] = useState({}); // chat id -> text of the question that failed
  const busy = useRef(new Set());

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(chats));
    } catch {
      /* storage unavailable: history just won't persist */
    }
  }, [chats]);

  const addMessage = useCallback((id, message) => {
    setChats((cs) =>
      cs.map((c) => (c.id === id ? { ...c, updatedAt: Date.now(), messages: [...c.messages, message] } : c))
    );
  }, []);

  const ask = useCallback(
    async (id, text) => {
      busy.current.add(id);
      setPending((p) => ({ ...p, [id]: true }));
      setErrors((e) => without(e, id));
      try {
        const res = await sendChat(text);
        addMessage(id, { id: makeId(), role: "assistant", text: res.answer, sources: res.sources ?? [] });
      } catch {
        setErrors((e) => ({ ...e, [id]: text }));
      } finally {
        busy.current.delete(id);
        setPending((p) => without(p, id));
      }
    },
    [addMessage]
  );

  // Sends a message; pass id = null to start a new conversation. Returns the conversation id.
  const sendMessage = useCallback(
    (id, raw) => {
      const text = raw.trim();
      if (!text || (id && busy.current.has(id))) return id;

      const message = { id: makeId(), role: "user", text };
      let chatId = id;
      if (chatId) {
        addMessage(chatId, message);
      } else {
        chatId = makeId();
        const now = Date.now();
        const created = { id: chatId, title: titleFrom(text), createdAt: now, updatedAt: now, messages: [message] };
        setChats((cs) => [created, ...cs]);
      }
      ask(chatId, text);
      return chatId;
    },
    [addMessage, ask]
  );

  const retry = useCallback(
    (id) => {
      const text = errors[id];
      if (text && !busy.current.has(id)) ask(id, text);
    },
    [ask, errors]
  );

  const deleteChat = useCallback((id) => {
    setChats((cs) => cs.filter((c) => c.id !== id));
    setErrors((e) => without(e, id));
  }, []);

  const value = useMemo(
    () => ({ chats, pending, errors, sendMessage, retry, deleteChat }),
    [chats, pending, errors, sendMessage, retry, deleteChat]
  );

  return <ChatHistoryContext.Provider value={value}>{children}</ChatHistoryContext.Provider>;
}

export function useChatHistory() {
  const ctx = useContext(ChatHistoryContext);
  if (!ctx) throw new Error("useChatHistory must be used inside ChatHistoryProvider");
  return ctx;
}
