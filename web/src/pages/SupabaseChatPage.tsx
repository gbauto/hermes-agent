import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, MessageSquare, Send } from "lucide-react";

import { type ChatRow, loadMessages, sendMessage } from "@/lib/dashboardChat";
import { tenantOption, useTenant } from "@/lib/tenant";

const POLL_MS = 3000;
const SESSION_ID = "dashboard-web";

function roleClass(role: string): "user" | "assistant" | "system" {
  if (role === "assistant" || role === "agent" || role === "hermes") return "assistant";
  if (role === "system") return "system";
  return "user";
}

/**
 * Supabase-backed web chat for the dashboard /chat tab (replaces the
 * POSIX-PTY terminal embed, which can't run on native Windows). Scoped to
 * the active client; smoke-client is the fully-wired path (anon read +
 * Hermes bridge replies). See lib/dashboardChat.
 */
export default function SupabaseChatPage() {
  const tenant = useTenant();
  const option = tenantOption(tenant);
  const [searchParams, setSearchParams] = useSearchParams();
  const [messages, setMessages] = useState<ChatRow[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const lastTs = useRef<string | null>(null);
  const handledPromptRef = useRef<string | null>(null);
  const promptParam = searchParams.get("prompt");
  const autoSendPrompt = searchParams.get("send") === "1";

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      const el = scrollRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }, []);

  const merge = useCallback(
    (incoming: ChatRow[]) => {
      if (!incoming.length) return;
      setMessages((current) => {
        const seen = new Set(current.map((m) => m.id));
        const next = [...current];
        for (const row of incoming) if (!seen.has(row.id)) next.push(row);
        const last = next[next.length - 1];
        if (last) lastTs.current = last.created_at;
        return next;
      });
      scrollToBottom();
    },
    [scrollToBottom],
  );

  // Load history + reset when the active client changes.
  useEffect(() => {
    let cancelled = false;
    setMessages([]);
    setError(null);
    lastTs.current = null;
    setLoading(true);
    loadMessages(tenant)
      .then((rows) => {
        if (cancelled) return;
        setMessages(rows);
        lastTs.current = rows.length ? rows[rows.length - 1].created_at : null;
        scrollToBottom();
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load chat");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tenant, scrollToBottom]);

  // Poll for new messages (incremental).
  useEffect(() => {
    const id = window.setInterval(() => {
      loadMessages(tenant, lastTs.current ?? undefined)
        .then(merge)
        .catch(() => {
          /* transient poll failures are non-fatal */
        });
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [tenant, merge]);

  const sendText = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setSending(true);
    setError(null);
    try {
      await sendMessage(tenant, SESSION_ID, trimmed);
      merge(await loadMessages(tenant, lastTs.current ?? undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to send");
    } finally {
      setSending(false);
    }
  }, [sending, tenant, merge]);

  const onSend = useCallback(async () => {
    await sendText(draft);
    setDraft("");
  }, [draft, sendText]);

  useEffect(() => {
    if (!promptParam) return;
    if (!autoSendPrompt) {
      setDraft(promptParam);
      return;
    }
    if (handledPromptRef.current === promptParam) return;
    handledPromptRef.current = promptParam;
    void sendText(promptParam).finally(() => {
      const next = new URLSearchParams(searchParams);
      next.delete("prompt");
      next.delete("send");
      setSearchParams(next, { replace: true });
    });
  }, [
    autoSendPrompt,
    promptParam,
    searchParams,
    sendText,
    setSearchParams,
  ]);

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void onSend();
    }
  };

  return (
    <div
      className="gbhub-page normal-case flex w-full min-w-0 flex-col gap-4"
      style={{ height: "calc(100vh - 120px)" }}
    >
      <div>
        <p className="gbhub-eyebrow">GBAutomation Chat</p>
        <h2 className="flex items-center gap-2" style={{ fontFamily: "var(--gb-font-serif)", fontSize: "1.5rem" }}>
          <MessageSquare className="h-5 w-5" /> Hermes Chat
          <span className="inline-flex items-center gap-1.5 text-xs" style={{ color: option.color }}>
            <span style={{ width: 8, height: 8, borderRadius: 999, background: option.color, display: "inline-block" }} />
            {option.label}
          </span>
        </h2>
      </div>

      {tenant !== "smoke-client" && (
        <div
          className="flex items-start gap-2 rounded-lg border p-3 text-sm"
          style={{ borderColor: "var(--gb-stone)", background: "rgba(217,119,87,0.08)" }}
        >
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>
            Live chat is wired for <b>smoke-client</b> (anon read + Hermes bridge replies). Other clients
            may show no history and sends may be rejected until their read policy and Edge Function
            allowlist are added.
          </span>
        </div>
      )}

      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto rounded-lg border p-4"
        style={{ borderColor: "var(--gb-stone)", background: "var(--gb-cream, #f3f1e7)" }}
      >
        {loading && !messages.length ? (
          <p className="text-sm opacity-60">Loading…</p>
        ) : !messages.length ? (
          <p className="text-sm opacity-60">No messages yet for {option.label}. Send one to start.</p>
        ) : (
          <div className="flex flex-col gap-3">
            {messages.map((m) => {
              const c = roleClass(m.role);
              const mine = c === "user";
              return (
                <div
                  key={m.id}
                  className={`flex ${mine ? "justify-end" : c === "system" ? "justify-center" : "justify-start"}`}
                >
                  <div
                    className="max-w-[78%] rounded-2xl px-3 py-2 text-sm"
                    style={{
                      background: mine ? "rgba(217,119,87,0.14)" : c === "system" ? "rgba(0,0,0,0.05)" : "#fff",
                      border: "1px solid var(--gb-stone)",
                      color: "var(--gb-ink, #191919)",
                    }}
                  >
                    <span className="block text-[10px] font-bold uppercase tracking-wider opacity-50">
                      {c === "assistant" ? "Hermes" : c === "system" ? "System" : "You"}
                    </span>
                    <span className="whitespace-pre-wrap break-words">{m.content}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {error ? (
        <p className="text-sm" style={{ color: "#c0392b" }}>
          {error}
        </p>
      ) : null}

      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void onSend();
        }}
      >
        <textarea
          className="flex-1 resize-none rounded-lg border p-2.5 text-sm"
          style={{ borderColor: "var(--gb-stone)", background: "#fff", color: "var(--gb-ink)", minHeight: 44, maxHeight: 160 }}
          rows={1}
          placeholder={`Message ${option.label}…`}
          value={draft}
          disabled={sending}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          type="submit"
          disabled={sending || !draft.trim()}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full px-4 py-2.5 text-sm font-semibold"
          style={{
            background: "var(--gb-ink, #191919)",
            color: "var(--gb-cream, #f3f1e7)",
            opacity: sending || !draft.trim() ? 0.5 : 1,
          }}
        >
          <Send className="h-3.5 w-3.5" /> {sending ? "Sending…" : "Send"}
        </button>
      </form>
    </div>
  );
}
