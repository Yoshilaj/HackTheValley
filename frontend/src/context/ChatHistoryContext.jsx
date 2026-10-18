import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  USE_BACKEND,
  deleteConversation,
  getConversationMessages,
  getConversations,
  sendChat,
  streamChat,
  uploadDocument,
} from "../api/client.js";
import { useSession } from "./SessionContext.jsx";

// Conversations: { id, remoteId?, title, createdAt, updatedAt, loaded,
//                  messages: [{ id, role, text, sources?: [{ n, title, year, url }], attachments? }] }
// A reply being written also has typed (show it typing out), streaming,
// stage ("searching" | "thinking") and thinking (the model's thinking so far).
// sendMessage(id, text, files?) - id null starts a new conversation.
//
// Backend off: kept in this browser (localStorage), shared by whoever uses it.
// Backend on: each signed-in user's conversations live on the server (Neon),
// so a different login sees a different history. The list loads after sign-in;
// a conversation's messages load the first time it's opened (loadMessages).
// A new chat gets a local id at once (so the page can open it) and its server
// id (remoteId) from the first reply. Guests have no account, so their chats
// stay in memory and the assistant asks them to sign in.
// Attachments: with the backend on, each file is uploaded to the user's record
// (/api/uploads/document, stored in the database) before the message is sent,
// and the message tells the assistant what was attached, with the summary the
// backend read from it. Without the backend they're only named. Messages keep
// { name, size, type } per file for display.
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

// File types the backend's upload accepts (PDF, images, plain text, audio).
const UPLOADABLE = /^(application\/pdf|image\/(jpeg|png|gif|webp)|text\/plain|audio\/)/;
const kindFor = (file) => (file.type.startsWith("audio/") ? "audio" : "doctor_note");

// Upload each attachment; returns the lines to add to the message for the assistant.
async function uploadAttachments(files) {
  const lines = [];
  for (const file of files) {
    if (!UPLOADABLE.test(file.type)) {
      lines.push(`(Couldn't attach ${file.name}: only PDF, image, text or audio files can be saved.)`);
      continue;
    }
    try {
      const doc = await uploadDocument(file, kindFor(file));
      const summary = doc.extracted?.data?.summary;
      lines.push(
        summary
          ? `(Attached ${file.name}, saved to my record. What it says: ${String(summary).slice(0, 600)})`
          : `(Attached ${file.name}, saved to my record.)`
      );
    } catch {
      lines.push(`(Couldn't attach ${file.name}. It wasn't saved.)`);
    }
  }
  return lines;
}

// Backend source -> { n, title, year, url }: n matches the [n] markers in the
// answer, url is the original publication. Older title-only sources are dropped.
const sourceRef = (s) => (s && typeof s === "object" ? { n: s.n, title: s.title, year: s.year, url: s.url } : null);

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
    sources: (m.sources ?? []).map(sourceRef).filter(Boolean),
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
      // A reply still streaming isn't kept (after a reload it would never finish),
      // and finished ones are shown whole next time rather than typed out again.
      const saved = chats.map(({ loaded: _l, ...c }) => ({
        ...c,
        messages: c.messages.filter((m) => !m.streaming).map(({ typed: _t, thinking: _th, stage: _s, ...m }) => m),
      }));
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
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

  // fn(message) returns the fields to change.
  const patchMessage = useCallback(
    (id, messageId, fn) =>
      patchChat(id, (c) => ({ ...c, messages: c.messages.map((m) => (m.id === messageId ? { ...m, ...fn(m) } : m)) })),
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

  // attachNote: lines from an earlier upload, so a retry doesn't upload the files twice.
  const ask = useCallback(
    async (id, text, files, attachNote = null) => {
      let note = attachNote;
      busy.current.add(id);
      setPending((p) => ({ ...p, [id]: true }));
      setErrors((e) => without(e, id));
      try {
        if (USE_BACKEND && !account) {
          const err = new Error("guest");
          err.signIn = true;
          throw err;
        }
        if (note == null && files.length) {
          note = USE_BACKEND
            ? (await uploadAttachments(files)).join("\n")
            : `(Attached: ${files.map((f) => f.name).join(", ")})`;
        }
        // The backend takes at most 4000 characters per message.
        const named = (note ? `${text}\n\n${note}`.trim() : text).slice(0, 4000);
        const remoteId = chatsRef.current.find((c) => c.id === id)?.remoteId;
        const options = { conversationId: remoteId, newConversation: USE_BACKEND && !remoteId };
        // The reply appears with the first event and fills in as it streams.
        const live = { id: makeId(), role: "assistant", text: "", typed: true, streaming: true, thinking: "" };
        let started = false;
        const onEvent = (ev) => {
          if (!started) {
            started = true;
            addMessage(id, live);
          }
          patchMessage(id, live.id, (m) => {
            if (ev.type === "status") return { stage: ev.stage };
            if (ev.type === "thought") return { thinking: m.thinking + ev.text };
            if (ev.type === "delta") return { text: m.text + ev.text };
            if (ev.type === "answer") return { text: ev.answer, sources: ev.sources.map(sourceRef).filter(Boolean) };
            return {};
          });
        };
        let res;
        try {
          res = await streamChat(named, options, onEvent);
        } catch (err) {
          // A backend without the stream endpoint: ask the plain way, typed out at the end.
          if (started || ![404, 405].includes(err?.status)) throw err;
          res = await sendChat(named, options);
          addMessage(id, live);
        }
        if (res.conversation_id && !remoteId) patchChat(id, (c) => ({ ...c, remoteId: res.conversation_id }));
        patchMessage(id, live.id, () => ({
          text: res.answer,
          sources: (res.sources ?? []).map(sourceRef).filter(Boolean),
          streaming: false,
        }));
      } catch (err) {
        // Drop a half-streamed reply; the error bubble offers a retry.
        patchChat(id, (c) => ({ ...c, messages: c.messages.filter((m) => !m.streaming) }));
        setErrors((e) => ({ ...e, [id]: { text, files, signIn: Boolean(err?.signIn), note } }));
      } finally {
        busy.current.delete(id);
        setPending((p) => without(p, id));
      }
    },
    [account, addMessage, patchChat, patchMessage]
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
      if (failed && !busy.current.has(id)) ask(id, failed.text, failed.files, failed.note);
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
