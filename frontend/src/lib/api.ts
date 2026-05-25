const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type ChatResponse = { session_id: string; response: string };

export type MessageOut = {
  role: "user" | "assistant";
  content: string;
  created_at: string;
};

export type SessionSummary = {
  id: string;
  title: string | null;
  created_at: string;
  last_active_at: string;
};

export type SessionStats = {
  session_id: string;
  message_count: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  avg_latency_ms: number;
  total_cost: number;
  error_count: number;
  last_seen_at: string;
};

export type StatsResponse = {
  overall: {
    sessions: number;
    total_messages: number;
    total_tokens: number;
    avg_latency_ms: number;
    total_cost: number;
    error_count: number;
  };
  per_session: SessionStats[];
};

export async function sendChat(
  message: string,
  sessionId: string | null,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  });
  if (!res.ok) {
    throw new Error(`chat failed: ${res.status}`);
  }
  return res.json();
}

export async function listSessions(): Promise<SessionSummary[]> {
  const res = await fetch(`${BASE}/sessions`);
  if (!res.ok) throw new Error(`sessions failed: ${res.status}`);
  return res.json();
}

export async function getSessionMessages(id: string): Promise<MessageOut[]> {
  const res = await fetch(`${BASE}/sessions/${id}/messages`);
  if (!res.ok) throw new Error(`messages failed: ${res.status}`);
  return res.json();
}

export async function getStats(): Promise<StatsResponse> {
  const res = await fetch(`${BASE}/stats`, { cache: "no-store" });
  if (!res.ok) throw new Error(`stats failed: ${res.status}`);
  return res.json();
}
