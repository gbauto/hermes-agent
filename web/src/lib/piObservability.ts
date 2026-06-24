import { fetchJSON } from "@/lib/api";

export type PiObsEventType =
  | "session_start"
  | "session_shutdown"
  | "agent_start"
  | "agent_end"
  | "turn_start"
  | "turn_end"
  | "user_message"
  | "assistant_message"
  | "tool_call"
  | "tool_result"
  | "model_change"
  | "thinking"
  | "error"
  | "custom"
  | "compaction"
  | "branch_nav";

export interface PiObsSession {
  agent_name?: string;
  cwd?: string;
  event_count: number;
  first_ts: string;
  last_ts: string;
  model?: string;
  pool: string;
  provider?: string;
  session_file?: string;
  session_id: string;
  tags: string[];
}

export interface PiObsEvent {
  agent_name?: string;
  cwd: string;
  event_id: string;
  model?: string;
  payload: Record<string, unknown>;
  pool: string;
  provider?: string;
  seq: number;
  session_id: string;
  tags: string[];
  ts: string;
  type: PiObsEventType;
}

export interface SmokeClientPiObservabilityFixture {
  events: PiObsEvent[];
  generated_at: string;
  sessions: PiObsSession[];
  source: string;
  tenant: "smoke-client";
  validation?: Array<{
    command: string;
    gate: string;
    status: "pass" | "fail" | "warn" | string;
  }>;
}

export interface PiObsHealth {
  events_total: number;
  ok: boolean;
  sessions_total: number;
  uptime_s: number;
  version: string;
}

export interface PiObsLiveConfig {
  token: string;
  url: string;
}

export interface SmokeClientPiObservabilityData {
  events: PiObsEvent[];
  fixture: SmokeClientPiObservabilityFixture | null;
  health: PiObsHealth | null;
  liveError: string | null;
  liveSource: boolean;
  sessions: PiObsSession[];
  source?: "fixture" | "hermes";
  upstream?: {
    configured: boolean;
    url_origin?: string;
  };
}

export function formatPiObsDate(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function eventSummary(event: PiObsEvent) {
  const payload = event.payload ?? {};
  if (event.type === "tool_call") {
    return `${String(payload.tool_name ?? "tool")} ${String(payload.tool_call_id ?? "")}`.trim();
  }
  if (event.type === "tool_result") {
    const resultText = String(payload.content_text ?? "");
    return resultText || `${String(payload.tool_name ?? "tool")} result`;
  }
  if (event.type === "assistant_message" || event.type === "user_message" || event.type === "thinking") {
    return String(payload.text ?? "");
  }
  if (event.type === "error") {
    return String(payload.message ?? "error");
  }
  if (event.type === "custom") {
    return String(payload.custom_type ?? "custom event");
  }
  if (event.type === "model_change") {
    return `${String(payload.provider ?? event.provider ?? "")} ${String(payload.model ?? event.model ?? "")}`.trim();
  }
  return event.type.replace(/_/g, " ");
}

export function statusToneForEvent(event: PiObsEvent) {
  if (event.type === "error") return "bad";
  if (event.type === "tool_result" && event.payload?.is_error === true) return "bad";
  if (event.type === "session_start" || event.type === "agent_start") return "good";
  if (event.type === "tool_call" || event.type === "thinking" || event.type === "custom") return "warn";
  return "";
}

interface HermesPiObsSnapshot {
  events?: PiObsEvent[];
  health?: PiObsHealth | null;
  ok?: boolean;
  sessions?: PiObsSession[];
  source?: "hermes";
  upstream?: {
    configured: boolean;
    url_origin?: string;
  };
}

export async function fetchSmokeClientPiObservability(): Promise<SmokeClientPiObservabilityData> {
  const fixture = await fetch("/gbauto-supabase/smoke-client-pi-observability.json")
    .then((response) => response.json() as Promise<SmokeClientPiObservabilityFixture>)
    .catch(() => null);

  try {
    const hermes = await fetchJSON<HermesPiObsSnapshot>(
      "/api/gbauto/pi-observability/snapshot?pool=smoke-client&limit=25",
    );
    const sessions = hermes.sessions ?? [];
    const events = hermes.events ?? [];
    return {
      events: events.length ? events : (fixture?.events ?? []),
      fixture,
      health: hermes.health ?? null,
      liveError: null,
      liveSource: true,
      source: "hermes",
      sessions: sessions.length ? sessions : (fixture?.sessions ?? []),
      upstream: hermes.upstream,
    };
  } catch (error) {
    return {
      events: fixture?.events ?? [],
      fixture,
      health: null,
      liveError: error instanceof Error ? error.message : "Unknown Hermes observability fetch error",
      liveSource: false,
      source: "fixture",
      sessions: fixture?.sessions ?? [],
    };
  }
}
