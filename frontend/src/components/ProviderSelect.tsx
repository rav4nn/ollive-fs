"use client";

import { useEffect, useState } from "react";
import { listProviders, ProviderInfo } from "@/lib/api";

type Props = {
  value: string | null;
  onChange: (provider: string | null) => void;
};

const STORAGE_KEY = "ollive.provider";

export default function ProviderSelect({ value, onChange }: Props) {
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [defaultProvider, setDefaultProvider] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listProviders()
      .then((r) => {
        if (cancelled) return;
        setProviders(r.providers);
        setDefaultProvider(r.default);
        // First-load: pick from localStorage, else server default.
        if (value === null) {
          const saved = window.localStorage.getItem(STORAGE_KEY);
          const valid = saved && r.providers.find((p) => p.name === saved && p.available);
          onChange(valid ? saved : r.default);
        }
      })
      .catch(() => {
        // ignore — chat still works against the server default
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persist whenever the parent changes the value.
  useEffect(() => {
    if (value) window.localStorage.setItem(STORAGE_KEY, value);
  }, [value]);

  if (!providers) return null;

  const available = providers.filter((p) => p.available);
  if (available.length <= 1) return null; // nothing to pick from

  return (
    <select
      className="provider-select"
      value={value ?? defaultProvider ?? ""}
      onChange={(e) => onChange(e.target.value)}
      title="Choose which LLM provider answers your next message"
    >
      {available.map((p) => (
        <option key={p.name} value={p.name}>
          {p.name} ({p.model})
        </option>
      ))}
    </select>
  );
}
