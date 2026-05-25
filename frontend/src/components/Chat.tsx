"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getSessionMessages,
  listSessions,
  MessageOut,
  sendChatStream,
  SessionSummary,
} from "@/lib/api";

const SESSION_KEY = "ollive.session_id";

type ChatMessage = { role: "user" | "assistant"; content: string };

function toChatMessages(rows: MessageOut[]): ChatMessage[] {
  return rows.map((r) => ({ role: r.role, content: r.content }));
}

export default function Chat() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Load session id from localStorage on mount.
  useEffect(() => {
    const saved = typeof window !== "undefined" ? window.localStorage.getItem(SESSION_KEY) : null;
    if (saved) setSessionId(saved);
  }, []);

  // Persist session id whenever it changes.
  useEffect(() => {
    if (sessionId) window.localStorage.setItem(SESSION_KEY, sessionId);
  }, [sessionId]);

  // Refresh session list and current messages.
  const refreshSessions = useCallback(async () => {
    try {
      const s = await listSessions();
      setSessions(s);
    } catch (e) {
      // ignore for now
    }
  }, []);

  const loadSession = useCallback(async (id: string) => {
    try {
      const msgs = await getSessionMessages(id);
      setMessages(toChatMessages(msgs));
    } catch {
      setMessages([]);
    }
  }, []);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  useEffect(() => {
    if (sessionId) loadSession(sessionId);
  }, [sessionId, loadSession]);

  // Auto-scroll on new message.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, pending]);

  const onSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const text = input.trim();
      if (!text || pending) return;

      const userMsg: ChatMessage = { role: "user", content: text };
      // Append the user turn AND an empty assistant placeholder we'll stream into.
      setMessages((m) => [...m, userMsg, { role: "assistant", content: "" }]);
      setInput("");
      setPending(true);

      const ctrl = new AbortController();
      abortRef.current = ctrl;

      // Mutate the last (assistant) message as deltas arrive.
      const appendDelta = (chunk: string) => {
        setMessages((m) => {
          const next = m.slice();
          const last = next[next.length - 1];
          if (last && last.role === "assistant") {
            next[next.length - 1] = { ...last, content: last.content + chunk };
          }
          return next;
        });
      };

      const replaceAssistant = (content: string) => {
        setMessages((m) => {
          const next = m.slice();
          const last = next[next.length - 1];
          if (last && last.role === "assistant") {
            next[next.length - 1] = { ...last, content };
          }
          return next;
        });
      };

      try {
        for await (const evt of sendChatStream(text, sessionId, ctrl.signal)) {
          if (evt.type === "session") {
            if (!sessionId) setSessionId(evt.session_id);
          } else if (evt.type === "delta") {
            appendDelta(evt.text);
          } else if (evt.type === "done") {
            if (evt.status === "error") {
              replaceAssistant(`Error: ${evt.error_message ?? "unknown"}`);
            }
          }
        }
        refreshSessions();
      } catch (err: any) {
        if (err?.name === "AbortError") {
          // Preserve any partial text and append a hint.
          setMessages((m) => {
            const next = m.slice();
            const last = next[next.length - 1];
            if (last && last.role === "assistant") {
              const suffix = last.content ? " (canceled)" : "(canceled)";
              next[next.length - 1] = { ...last, content: last.content + suffix };
            }
            return next;
          });
        } else {
          replaceAssistant(`Error: ${err?.message ?? "unknown"}`);
        }
      } finally {
        setPending(false);
        abortRef.current = null;
      }
    },
    [input, pending, sessionId, refreshSessions],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const startNew = useCallback(() => {
    setSessionId(null);
    setMessages([]);
    window.localStorage.removeItem(SESSION_KEY);
  }, []);

  const sortedSessions = useMemo(
    () =>
      [...sessions].sort(
        (a, b) =>
          new Date(b.last_active_at).getTime() - new Date(a.last_active_at).getTime(),
      ),
    [sessions],
  );

  return (
    <div className="chat">
      <aside className="sidebar">
        <div className="sidebar-header">
          <button className="new" onClick={startNew}>
            + New chat
          </button>
        </div>
        <div className="session-list">
          {sortedSessions.length === 0 && (
            <div style={{ padding: "8px 12px", color: "var(--muted)", fontSize: 12 }}>
              No conversations yet.
            </div>
          )}
          {sortedSessions.map((s) => (
            <div
              key={s.id}
              className={`session-item ${sessionId === s.id ? "active" : ""}`}
              title={s.id}
              onClick={() => setSessionId(s.id)}
            >
              {s.title ?? s.id.slice(0, 10)}
            </div>
          ))}
        </div>
      </aside>

      <section className="chat-main">
        <div className="messages" ref={scrollRef}>
          {messages.length === 0 && (
            <div className="empty-state">Start the conversation.</div>
          )}
          {messages.map((m, i) => {
            const isLast = i === messages.length - 1;
            const showCursor = pending && isLast && m.role === "assistant";
            return (
              <div key={i} className={`message ${m.role}`}>
                <div className="bubble">
                  {m.content}
                  {showCursor && <span className="cursor" aria-hidden>▍</span>}
                </div>
              </div>
            );
          })}
        </div>

        <form className="composer" onSubmit={onSubmit}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Send a message…"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSubmit(e as unknown as React.FormEvent);
              }
            }}
            disabled={pending}
            rows={1}
          />
          {pending ? (
            <button type="button" className="cancel" onClick={cancel}>
              Cancel
            </button>
          ) : (
            <button type="submit" disabled={!input.trim()}>
              Send
            </button>
          )}
        </form>
      </section>
    </div>
  );
}
