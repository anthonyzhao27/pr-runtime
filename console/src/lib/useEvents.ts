import { useEffect, useRef, useState } from "react";
import type { ServerEvent } from "./types";

export type LiveStatus = "connecting" | "live" | "disconnected";

/**
 * Subscribe to /api/events (SSE). The handler is kept in a ref so callers
 * can pass a fresh closure every render without reconnecting.
 */
export function useEvents(onEvent: (ev: ServerEvent) => void, enabled = true): LiveStatus {
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!enabled) return;
    const es = new EventSource("/api/events");
    es.onopen = () => setStatus("live");
    es.onerror = () => setStatus("disconnected"); // EventSource auto-reconnects (server sets retry: 3000)
    es.onmessage = (m: MessageEvent<string>) => {
      if (!m.data) return;
      let parsed: ServerEvent;
      try {
        parsed = JSON.parse(m.data) as ServerEvent;
      } catch {
        return;
      }
      if (parsed && typeof parsed === "object" && typeof parsed.type === "string") {
        setStatus("live");
        handler.current(parsed);
      }
    };
    return () => es.close();
  }, [enabled]);

  return status;
}

/** Re-render every `ms` so relative ages stay fresh. */
export function useTick(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}
