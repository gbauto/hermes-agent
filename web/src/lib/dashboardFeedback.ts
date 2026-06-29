import { fetchJSON } from "@/lib/api";

export interface FeedbackRow {
  feedback_id?: string;
  created_at?: string;
  status?: string;
  route?: string | null;
  page_url?: string | null;
  client_slug?: string | null;
  board_slug?: string | null;
  task_id?: string | null;
  feedback_type?: string | null;
  message?: string | null;
  metadata?: Record<string, unknown>;
}

interface SubmitFeedbackResponse {
  ok: boolean;
  row?: FeedbackRow | null;
  error?: string;
}

export interface SubmitFeedbackPayload {
  message: string;
  route: string;
  pageUrl: string;
  clientSlug: string;
  boardSlug?: string;
  metadata?: Record<string, unknown>;
}

export async function submitDashboardFeedback(payload: SubmitFeedbackPayload): Promise<FeedbackRow | null> {
  const res = await fetchJSON<SubmitFeedbackResponse>("/api/gbauto/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: payload.message,
      route: payload.route,
      page_url: payload.pageUrl,
      client_slug: payload.clientSlug,
      board_slug: payload.boardSlug ?? "gbautomation",
      feedback_type: "website_feedback",
      metadata: payload.metadata ?? {},
    }),
  });
  if (!res.ok) throw new Error(res.error || "Feedback submit failed");
  return res.row ?? null;
}
