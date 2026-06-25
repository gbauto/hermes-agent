/**
 * Chat client for the dashboard /chat tab.
 *
 * Reads/sends go through the dashboard's OWN backend (/api/gbauto/chat),
 * which talks to Supabase via the gbauto-supabase CLI under server-side
 * credentials and is gated by the dashboard session token. This is the
 * secure path: real-client chat is never exposed to the browser via the
 * public anon key, and only authenticated dashboard users can read/write it.
 *
 * Updates via light polling (no Realtime SDK dependency).
 */
import { fetchJSON } from "@/lib/api";

export interface ChatRow {
  id: string;
  role: string;
  content: string;
  created_at: string;
  tenant: string;
  session_id?: string | null;
}

interface ReadResponse {
  ok: boolean;
  rows?: ChatRow[];
  error?: string;
}

interface SendResponse {
  ok: boolean;
  row?: ChatRow | null;
  error?: string;
}

/** Load chat history for a tenant, oldest-first. Pass `sinceIso` for incremental polls. */
export async function loadMessages(
  tenant: string,
  sinceIso?: string,
  limit = 100,
): Promise<ChatRow[]> {
  const qs = new URLSearchParams({ tenant, limit: String(limit) });
  if (sinceIso) qs.set("since", sinceIso);
  const res = await fetchJSON<ReadResponse>(`/api/gbauto/chat?${qs.toString()}`);
  if (!res.ok) throw new Error(res.error || "Load failed");
  return res.rows ?? [];
}

/** Send a user message (server inserts via real creds; bypasses anon RLS). */
export async function sendMessage(tenant: string, sessionId: string, text: string): Promise<void> {
  const res = await fetchJSON<SendResponse>("/api/gbauto/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tenant, session_id: sessionId, text }),
  });
  if (!res.ok) throw new Error(res.error || "Send failed");
}
