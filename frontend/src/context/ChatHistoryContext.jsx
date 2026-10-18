import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { USE_BACKEND, deleteConversation, getConversationMessages, getConversations, sendChat } from "../api/client.js";
import { useSession } from "./SessionContext.jsx";

// Conversations: { id, remoteId?, title, createdAt, updatedAt, loaded,
//                  messages: [{ id, role, text, sources?, attachments? }] }
// sendMessage(id, text, files?) - id null starts a new conversation.
//
// Backend off: kept in this browser (localStorage), shared by whoever uses it.
// Backend on: each signed-in user's conversations live on the server (Neon),
// so a different login sees a different history. The list loads after sign-in;
// a conversation's messages load the first time it's opened (loadMessages).
// A new chat gets a local id at once (so the page can open it) and its server
// id (remoteId) from the first reply. Guests have no account, so their chats
// stay in memory and the assistant asks them to sign in.
// Attachments are saved as { name, size, type } only; the backend's chat takes
// text, so attached files are named in the message rather than uploaded.
const STORAGE_KEY = "caresync-chats";
const ChatHistoryContext = createContext(null);

function loadChats() {
  if (USE_BACKEND) return [];
  try {
    const data = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(data) ? data.map((c) => ({ ...c, loaded: true })) : [];
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
const time = (iso) => new Date(iso).getTime();

// Backend source { n, title, url } -> the short label shown under a reply.
const sourceLabel = (s) => (typeof s === "string" ? s : s?.title ?? "");

function fromServerConversation(c) {
  return {
    id: c.id,
    remoteId: c.id,
    title: c.title,
    createdAt: time(c.created_at),
    updatedAt: time(c.updated_at),
    loaded: c.message_count === 0,
    messages: [],
  };
}

function fromServerMessage(m) {
  return {
    id: m.id,
    role: m.role,
    text: m.content,
    sources: (m.sources ?? []).map(sourceLabel).filter(Boolean),
  };
}

export function ChatHistoryProvider({ children }) {
  const { session, signedIn } = useSession();
  const [chats, setChats] = useState(loadChats);
  const [ready, setReady] = useState(!USE_BACKEND); // false until the user's list has loaded
  const [pending, setPending] = useState({}); // chat id -> waiting for a reply
  const [errors, setErrors] = useState({}); // chat id -> { text, files, signIn? } of the question that failed
  const busy = useRef(new Set());
  const chatsRef = useRef(chats);
  chatsRef.current = chats;
  const account = signedIn ? session?.userId ?? session?.email : null;

  // Backend off: keep history in this browser.
  useEffect(() => {
    if (USE_BACKEND) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(chats.map(({ loaded: _l, ...c }) => c)));
    } catch {
      /* storage unavailable: history just won't persist */
    }
  }, [chats]);

  // Backend on: a new login (or sign-out) swaps in that account's conversations.
  useEffect(() => {
    if (!USE_BACKEND) return;
    setChats([]);
    setPending({});
    setErrors({});
    if (!account) {
      setReady(true);
      return;
    }
    setReady(false);
    let cancelled = false;
    getConversations()
      .then((list) => !cancelled && setChats(list.map(fromServerConversation)))
      .catch(() => {
        /* offline: start empty; new chats still work once the backend answers */
      })
      .finally(() => !cancelled && setReady(true));
    return () => {
      cancelled = true;
    };
  }, [account]);

  const patchChat = useCallback((id, fn) => {
    setChats((cs) => cs.map((c) => (c.id === id ? fn(c) : c)));
  }, []);

  const addMessage = useCallback(
    (id, message) => patchChat(id, (c) => ({ ...c, updatedAt: Date.now(), messages: [...c.messages, message] })),
    [patchChat]
  );

  // Fetch a server conversation's messages the first time it's opened.
  const loadMessages = useCallback(
    async (id) => {
      const chat = chatsRef.current.find((c) => c.id === id);
      if (!USE_BACKEND || !chat || chat.loaded || !chat.remoteId) return;
      patchChat(id, (c) => ({ ...c, loaded: true }));
      try {
        const msgs = await getConversationMessages(chat.remoteId);
        patchChat(id, (c) => ({ ...c, messages: [...msgs.map(fromServerMessage), ...c.messages] }));
      } catch {
        patchChat(id, (c) => ({ ...c, loaded: false }));
      }
    },
    [patchChat]
  );

  const ask = useCallback(
    async (id, text, files) => {
      busy.current.add(id);
      setPending((p) => ({ ...p, [id]: true }));
      setErrors((e) => without(e, id));
      try {
        if (USE_BACKEND && !account) {
          const err = new Error("guest");
          err.signIn = true;
          throw err;
        }
        const named = files.length ? `${text}\n\n(Attached: ${files.map((f) => f.name).join(", ")})`.trim() : text;
        const remoteId = chatsRef.current.find((c) => c.id === id)?.remoteId;
        const res = await sendChat(named, { conversationId: remoteId, newConversation: USE_BACKEND && !remoteId });
        if (res.conversation_id && !remoteId) patchChat(id, (c) => ({ ...c, remoteId: res.conversation_id }));
        addMessage(id, {
          id: makeId(),
          role: "assistant",
          text: res.answer,
          sources: (res.sources ?? []).map(sourceLabel).filter(Boolean),
        });
      } catch (err) {
        setErrors((e) => ({ ...e, [id]: { text, files, signIn: Boolean(err?.signIn) } }));
      } finally {
        busy.current.delete(id);
        setPending((p) => without(p, id));
      }
    },
    [account, addMessage, patchChat]
  );

  // Sends a message; pass id = null to start a new conversation. Returns the conversation id.
  const sendMessage = useCallback(
    (id, raw, files = []) => {
      const text = raw.trim();
      if ((!text && files.length === 0) || (id && busy.current.has(id))) return id;

      const message = { id: makeId(), role: "user", text };
      if (files.length) message.attachments = files.map((f) => ({ name: f.name, size: f.size, type: f.type }));
      let chatId = id;
      if (chatId) {
        addMessage(chatId, message);
      } else {
        chatId = makeId();
        const now = Date.now();
        const created = {
          id: chatId,
          title: titleFrom(text || files[0].name),
          createdAt: now,
          updatedAt: now,
          loaded: true,
          messages: [message],
        };
        setChats((cs) => [created, ...cs]);
      }
      ask(chatId, text, files);
      return chatId;
    },
    [addMessage, ask]
  );

  const retry = useCallback(
    (id) => {
      const failed = errors[id];
      if (failed && !busy.current.has(id)) ask(id, failed.text, failed.files);
    },
    [ask, errors]
  );

  // Removed here at once; on the server too when it was saved there.
  const deleteChat = useCallback((id) => {
    const remoteId = chatsRef.current.find((c) => c.id === id)?.remoteId;
    setChats((cs) => cs.filter((c) => c.id !== id));
    setErrors((e) => without(e, id));
    if (USE_BACKEND && remoteId) {
      deleteConversation(remoteId).catch(() => {
        /* it reappears on the next load if the server kept it */
      });
    }
  }, []);

  const value = useMemo(
    () => ({ chats, ready, pending, errors, sendMessage, retry, deleteChat, loadMessages }),
    [chats, ready, pending, errors, sendMessage, retry, deleteChat, loadMessages]
  );

  return <ChatHistoryContext.Provider value={value}>{children}</ChatHistoryContext.Provider>;
}

export function useChatHistory() {
  const ctx = useContext(ChatHistoryContext);
  if (!ctx) throw new Error("useChatHistory must be used inside ChatHistoryProvider");
  return ctx;
}
