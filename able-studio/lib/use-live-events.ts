"use client";

import { useEffect, useRef } from "react";

const CONTROL_BASE_URL =
  process.env.NEXT_PUBLIC_ABLE_GATEWAY_URL ||
  process.env.ABLE_CONTROL_API_BASE ||
  "";

const MAX_SSE_RETRIES = 5;

export type GatewayEventType =
  | "connected"
  | "ping"
  | "routing_decision"
  | "buddy_xp"
  | "evolution_cycle"
  | "approval_request";

export interface GatewayEvent {
  type: GatewayEventType | string;
  ts?: string;
  data?: Record<string, unknown>;
  // For routing_decision
  tier?: number;
  provider?: string;
  domain?: string;
  score?: number;
  channel?: string;
  // For buddy_xp
  name?: string;
  level?: number;
  xp?: number;
  mood?: string;
}

/**
 * Connect to the gateway SSE /events stream.
 * Automatically reconnects with exponential backoff on failure.
 * Call `onEvent` for every event received (ping events are filtered).
 */
export function useLiveEvents(onEvent: (event: GatewayEvent) => void) {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let retries = 0;
    let cancelled = false;

    function connect() {
      if (cancelled) return;
      // Don't attempt SSE if gateway URL isn't configured
      if (!CONTROL_BASE_URL) return;
      if (retries >= MAX_SSE_RETRIES) return;
      try {
        es = new EventSource(`${CONTROL_BASE_URL}/events`);

        es.onmessage = (event) => {
          try {
            const parsed: GatewayEvent = JSON.parse(event.data);
            if (parsed.type === "ping") return; // suppress keepalives
            // Flatten nested data for convenience
            const flat: GatewayEvent = parsed.data
              ? { ...parsed, ...(parsed.data as Record<string, unknown>) }
              : parsed;
            onEventRef.current(flat);
          } catch {
            // Malformed event — ignore
          }
        };

        es.onopen = () => {
          retries = 0;
        };

        es.onerror = () => {
          es?.close();
          es = null;
          if (cancelled) return;
          const delay = Math.min(1000 * 2 ** retries, 30_000);
          retries++;
          reconnectTimer = setTimeout(connect, delay);
        };
      } catch {
        // EventSource not available (SSR) — skip silently
      }
    }

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
    };
  }, []);
}
