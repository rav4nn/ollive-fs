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
  provider: string | null,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId, provider }),
    signal,
  });
  if (!res.ok) {
    throw new Error(`chat failed: ${res.status}`);
  }
  return res.json();
}

export type ProviderInfo = {
  name: string;
  available: boolean;
  model: string;
  is_default: boolean;
};

export type ProvidersResponse = {
  default: string;
  providers: ProviderInfo[];
};

export async function listProviders(): Promise<ProvidersResponse> {
  const res = await fetch(`${BASE}/providers`);
  if (!res.ok) throw new Error(`providers failed: ${res.status}`);
  return res.json();
}

export type StreamEvent =
  | { type: "session"; session_id: string }
  | { type: "delta"; text: string }
  | {
      type: "done";
      status: "ok" | "error";
      error_message: string | null;
      usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
      latency_ms: number;
      time_to_first_token_ms: number | null;
      provider: string;
      model: string;
    };

/** Open an SSE stream for a chat turn. The async iterator yields one event per
 * SSE frame and ends when the server closes the connection. */
export async function* sendChatStream(
  message: string,
  sessionId: string | null,
  provider: string | null,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ message, session_id: sessionId, provider }),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`stream failed: ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let currentEvent = "message";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line.
      let sepIdx: number;
      while ((sepIdx = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, sepIdx);
        buf = buf.slice(sepIdx + 2);

        let evt = currentEvent;
        let data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) {
            evt = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            data += line.slice(5).trimStart();
          }
        }
        currentEvent = "message";
        if (!data) continue;
        try {
          const parsed = JSON.parse(data);
          yield { type: evt as StreamEvent["type"], ...parsed } as StreamEvent;
        } catch {
          // skip unparseable frame
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
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

export type BucketPoint = {
  bucket_ts: string;
  message_count: number;
  total_tokens: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  error_count: number;
  total_cost: number;
};

export type TimeseriesResponse = { points: BucketPoint[] };

export async function getTimeseries(minutes = 60): Promise<TimeseriesResponse> {
  const res = await fetch(`${BASE}/stats/timeseries?minutes=${minutes}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`timeseries failed: ${res.status}`);
  return res.json();
}
