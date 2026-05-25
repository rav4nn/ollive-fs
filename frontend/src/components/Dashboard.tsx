"use client";

import { useEffect, useState } from "react";
import { getStats, StatsResponse } from "@/lib/api";

const REFRESH_MS = 30_000;

function fmtUsd(n: number): string {
  return `$${n.toFixed(4)}`;
}

function fmtNum(n: number): string {
  return n.toLocaleString();
}

function fmtLatency(n: number): string {
  return `${Math.round(n)} ms`;
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function Dashboard() {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await getStats();
        if (!cancelled) {
          setStats(s);
          setErr(null);
        }
      } catch (e: any) {
        if (!cancelled) setErr(e?.message ?? "load failed");
      }
    };
    tick();
    const id = setInterval(tick, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (err && !stats) {
    return (
      <div className="container">
        <div style={{ color: "var(--danger)" }}>Failed to load stats: {err}</div>
      </div>
    );
  }

  if (!stats) {
    return (
      <div className="container">
        <div style={{ color: "var(--muted)" }}>Loading…</div>
      </div>
    );
  }

  const o = stats.overall;

  return (
    <div className="container">
      <div className="dash-grid">
        <div className="card">
          <div className="label">Total messages</div>
          <div className="value">{fmtNum(o.total_messages)}</div>
        </div>
        <div className="card">
          <div className="label">Sessions</div>
          <div className="value">{fmtNum(o.sessions)}</div>
        </div>
        <div className="card">
          <div className="label">Avg latency</div>
          <div className="value">{fmtLatency(o.avg_latency_ms)}</div>
        </div>
        <div className="card">
          <div className="label">Total tokens</div>
          <div className="value">{fmtNum(o.total_tokens)}</div>
        </div>
        <div className="card">
          <div className="label">Total cost</div>
          <div className="value">{fmtUsd(o.total_cost)}</div>
        </div>
        <div className="card">
          <div className="label">Errors</div>
          <div className="value" style={{ color: o.error_count ? "var(--danger)" : undefined }}>
            {fmtNum(o.error_count)}
          </div>
        </div>
      </div>

      <h2 style={{ fontSize: 14, color: "var(--muted)", marginBottom: 8 }}>
        Per-session breakdown
      </h2>
      <table className="sessions">
        <thead>
          <tr>
            <th>Session</th>
            <th>Msgs</th>
            <th>Tokens (in/out/total)</th>
            <th>Avg latency</th>
            <th>Cost</th>
            <th>Errors</th>
            <th>Last seen</th>
          </tr>
        </thead>
        <tbody>
          {stats.per_session.length === 0 && (
            <tr>
              <td colSpan={7} style={{ color: "var(--muted)" }}>
                No data yet.
              </td>
            </tr>
          )}
          {stats.per_session.map((s) => (
            <tr key={s.session_id}>
              <td className="mono">{s.session_id.slice(0, 12)}</td>
              <td>{fmtNum(s.message_count)}</td>
              <td>
                {fmtNum(s.total_prompt_tokens)} / {fmtNum(s.total_completion_tokens)} /{" "}
                {fmtNum(s.total_tokens)}
              </td>
              <td>{fmtLatency(s.avg_latency_ms)}</td>
              <td>{fmtUsd(s.total_cost)}</td>
              <td style={{ color: s.error_count ? "var(--danger)" : undefined }}>
                {fmtNum(s.error_count)}
              </td>
              <td>{fmtTime(s.last_seen_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="refresh-hint">Auto-refreshes every 30s.</div>
    </div>
  );
}
