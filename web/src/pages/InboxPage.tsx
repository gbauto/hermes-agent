import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Link } from "react-router-dom";
import {
  Archive,
  ArrowRight,
  CalendarClock,
  Check,
  ClipboardList,
  Inbox,
  Layers3,
  MessageSquareText,
  RefreshCw,
  Send,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { fetchJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

type InboxItemType = "decision" | "quick_form" | "dynamic_form" | "swipe";
type InboxTab = "all" | "decisions" | "forms" | "swipe";
type InboxAction =
  | "answer"
  | "archive"
  | "go"
  | "note"
  | "snooze_until_tomorrow";

interface InboxChoice {
  direction?: "left" | "right" | "up";
  label?: string;
  value?: string;
}

interface FormField {
  label?: string;
  name?: string;
  options?: Array<string | InboxChoice>;
  placeholder?: string;
  required?: boolean;
  type?: "checkbox" | "number" | "radio" | "select" | "textarea" | "text";
}

interface InboxItem {
  assignee?: string | null;
  choices: Array<string | InboxChoice>;
  detail?: string | null;
  form_schema: {
    fields?: FormField[];
    submit_label?: string;
  };
  id: string;
  item_type: InboxItemType;
  note?: string | null;
  priority: number;
  prompt: string;
  source_board?: string | null;
  source_snapshot?: Record<string, unknown>;
  source_task_id?: string | null;
  status: string;
  title: string;
}

interface InboxPayload {
  counts: Record<InboxItemType, number>;
  items: InboxItem[];
  pending_total: number;
}

const TABS: Array<{ id: InboxTab; label: string; icon: typeof Inbox }> = [
  { id: "all", label: "All", icon: Inbox },
  { id: "decisions", label: "Decisions", icon: MessageSquareText },
  { id: "forms", label: "Forms", icon: ClipboardList },
  { id: "swipe", label: "Swipe Deck", icon: Layers3 },
];

function choiceValue(choice: string | InboxChoice): string {
  return typeof choice === "string" ? choice : choice.value || choice.label || "";
}

function choiceLabel(choice: string | InboxChoice): string {
  if (typeof choice === "string") return choice;
  return choice.label || choice.value || "Select";
}

function sourceTaskHref(item: InboxItem): string {
  if (!item.source_board || !item.source_task_id) return "/kanban";
  return `/kanban?board=${encodeURIComponent(item.source_board)}#task=${encodeURIComponent(item.source_task_id)}`;
}

function rememberSourceBoard(item: InboxItem) {
  if (!item.source_board) return;
  try {
    window.localStorage.setItem("hermes.kanban.selectedBoard", item.source_board);
  } catch {
    // Storage can be disabled in privacy mode; the query string remains useful.
  }
}

function FieldInput({
  field,
  value,
  onChange,
}: {
  field: FormField;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const type = field.type || "text";
  if (type === "textarea") {
    return (
      <textarea
        onChange={(event) => onChange(event.target.value)}
        placeholder={field.placeholder}
        required={field.required}
        rows={4}
        value={String(value ?? "")}
      />
    );
  }
  if (type === "select") {
    return (
      <select
        onChange={(event) => onChange(event.target.value)}
        required={field.required}
        value={String(value ?? "")}
      >
        <option value="">Choose one</option>
        {(field.options || []).map((option) => (
          <option key={choiceValue(option)} value={choiceValue(option)}>
            {choiceLabel(option)}
          </option>
        ))}
      </select>
    );
  }
  if (type === "radio") {
    return (
      <div className="inbox-radio-group">
        {(field.options || []).map((option) => {
          const optionValue = choiceValue(option);
          return (
            <label key={optionValue}>
              <input
                checked={value === optionValue}
                name={field.name}
                onChange={() => onChange(optionValue)}
                type="radio"
                value={optionValue}
              />
              <span>{choiceLabel(option)}</span>
            </label>
          );
        })}
      </div>
    );
  }
  if (type === "checkbox") {
    return (
      <label className="inbox-checkbox">
        <input
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
          type="checkbox"
        />
        <span>{field.placeholder || "Yes"}</span>
      </label>
    );
  }
  return (
    <input
      onChange={(event) =>
        onChange(type === "number" ? event.target.valueAsNumber : event.target.value)
      }
      placeholder={field.placeholder}
      required={field.required}
      type={type === "number" ? "number" : "text"}
      value={typeof value === "number" ? value : String(value ?? "")}
    />
  );
}

function StandardInboxCard({
  busy,
  item,
  onRespond,
}: {
  busy: boolean;
  item: InboxItem;
  onRespond: (
    item: InboxItem,
    action: InboxAction,
    response?: Record<string, unknown>,
    note?: string,
  ) => Promise<void>;
}) {
  const [answer, setAnswer] = useState("");
  const [formValues, setFormValues] = useState<Record<string, unknown>>({});
  const [note, setNote] = useState(item.note || "");
  const fields = item.form_schema?.fields || [];
  const isForm = item.item_type === "quick_form" || item.item_type === "dynamic_form";

  return (
    <article className="inbox-card">
      <div className="inbox-card-topline">
        <Badge tone="outline" className="inbox-type-badge">
          {item.item_type.replace("_", " ")}
        </Badge>
        <span>P{item.priority}</span>
      </div>
      <h3>{item.title}</h3>
      <p className="inbox-prompt">{item.prompt}</p>
      {item.detail ? <p className="inbox-detail">{item.detail}</p> : null}

      {isForm ? (
        <form
          className="inbox-form"
          onSubmit={(event) => {
            event.preventDefault();
            void onRespond(item, "answer", { fields: formValues });
          }}
        >
          {fields.length ? (
            fields.map((field, index) => {
              const name = field.name || `field_${index + 1}`;
              return (
                <label key={name}>
                  <span>
                    {field.label || name}
                    {field.required ? " *" : ""}
                  </span>
                  <FieldInput
                    field={{ ...field, name }}
                    onChange={(value) =>
                      setFormValues((current) => ({ ...current, [name]: value }))
                    }
                    value={formValues[name]}
                  />
                </label>
              );
            })
          ) : (
            <label>
              <span>Your answer</span>
              <textarea
                onChange={(event) => setAnswer(event.target.value)}
                rows={4}
                value={answer}
              />
            </label>
          )}
          <button disabled={busy} type="submit">
            <Send className="h-3.5 w-3.5" />
            {item.form_schema?.submit_label || "Submit response"}
          </button>
        </form>
      ) : item.choices?.length ? (
        <div className="inbox-choice-grid">
          {item.choices.map((choice) => (
            <button
              disabled={busy}
              key={choiceValue(choice)}
              onClick={() =>
                void onRespond(item, "answer", { choice: choiceValue(choice) })
              }
              type="button"
            >
              {choiceLabel(choice)}
            </button>
          ))}
        </div>
      ) : (
        <div className="inbox-answer-row">
          <textarea
            onChange={(event) => setAnswer(event.target.value)}
            placeholder="Write a quick answer"
            rows={3}
            value={answer}
          />
          <button
            disabled={busy || !answer.trim()}
            onClick={() => void onRespond(item, "answer", { answer: answer.trim() })}
            type="button"
          >
            <Check className="h-3.5 w-3.5" />
            Answer
          </button>
        </div>
      )}

      <div className="inbox-card-footer">
        <div className="inbox-note-row">
          <input
            onChange={(event) => setNote(event.target.value)}
            placeholder="Add a note"
            value={note}
          />
          <button
            aria-label="Save note"
            disabled={busy || !note.trim()}
            onClick={() => void onRespond(item, "note", {}, note.trim())}
            type="button"
          >
            <MessageSquareText className="h-3.5 w-3.5" />
          </button>
        </div>
        <button
          className="inbox-archive-button"
          disabled={busy}
          onClick={() => void onRespond(item, "archive")}
          type="button"
        >
          <Archive className="h-3.5 w-3.5" />
          Archive
        </button>
      </div>

      {item.source_board ? (
        <Link
          className="inbox-source-link"
          onClick={() => rememberSourceBoard(item)}
          to={sourceTaskHref(item)}
        >
          {item.source_board}
          {item.source_task_id ? ` / ${item.source_task_id}` : ""}
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      ) : null}
    </article>
  );
}

function SwipeCard({
  busy,
  item,
  onRespond,
}: {
  busy: boolean;
  item: InboxItem;
  onRespond: (
    item: InboxItem,
    action: InboxAction,
    response?: Record<string, unknown>,
    note?: string,
  ) => Promise<void>;
}) {
  const start = useRef<{ x: number; y: number } | null>(null);
  const [drag, setDrag] = useState({ x: 0, y: 0 });
  const [note, setNote] = useState(item.note || "");

  const finishGesture = (event: ReactPointerEvent<HTMLElement>) => {
    if (!start.current || busy) return;
    const dx = event.clientX - start.current.x;
    const dy = event.clientY - start.current.y;
    start.current = null;
    setDrag({ x: 0, y: 0 });
    if (Math.abs(dx) > 84 && Math.abs(dx) > Math.abs(dy)) {
      void onRespond(item, dx > 0 ? "go" : "archive", {
        gesture: dx > 0 ? "right" : "left",
      });
    } else if (dy < -84 && Math.abs(dy) > Math.abs(dx)) {
      void onRespond(item, "snooze_until_tomorrow", { gesture: "up" });
    }
  };

  return (
    <article
      className={cn("inbox-card inbox-swipe-card", busy && "is-busy")}
      onPointerCancel={finishGesture}
      onPointerDown={(event) => {
        if (busy) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        start.current = { x: event.clientX, y: event.clientY };
      }}
      onPointerMove={(event) => {
        if (!start.current || busy) return;
        setDrag({
          x: event.clientX - start.current.x,
          y: Math.min(0, event.clientY - start.current.y),
        });
      }}
      onPointerUp={finishGesture}
      style={{
        transform: `translate(${drag.x * 0.45}px, ${drag.y * 0.35}px) rotate(${drag.x * 0.025}deg)`,
      }}
    >
      <div className="inbox-swipe-hints" aria-hidden>
        <span>← Archive</span>
        <span>↑ Tomorrow</span>
        <span>Go →</span>
      </div>
      <div className="inbox-card-topline">
        <Badge tone="outline" className="inbox-type-badge">Swipe decision</Badge>
        <span>P{item.priority}</span>
      </div>
      <h3>{item.title}</h3>
      <p className="inbox-prompt">{item.prompt}</p>
      {item.detail ? <p className="inbox-detail">{item.detail}</p> : null}
      <div className="inbox-swipe-actions">
        <button disabled={busy} onClick={() => void onRespond(item, "archive")} type="button">
          <Archive className="h-4 w-4" /> Archive
        </button>
        <button
          disabled={busy}
          onClick={() => void onRespond(item, "snooze_until_tomorrow")}
          type="button"
        >
          <CalendarClock className="h-4 w-4" /> Tomorrow
        </button>
        <button disabled={busy} onClick={() => void onRespond(item, "go")} type="button">
          Go <ArrowRight className="h-4 w-4" />
        </button>
      </div>
      <div className="inbox-note-row">
        <input
          onChange={(event) => setNote(event.target.value)}
          placeholder="Add context before deciding"
          value={note}
        />
        <button
          aria-label="Save note"
          disabled={busy || !note.trim()}
          onClick={() => void onRespond(item, "note", {}, note.trim())}
          type="button"
        >
          <MessageSquareText className="h-3.5 w-3.5" />
        </button>
      </div>
      {item.source_board ? (
        <Link
          className="inbox-source-link"
          onClick={() => rememberSourceBoard(item)}
          to={sourceTaskHref(item)}
        >
          {item.source_board} / {item.source_task_id}
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      ) : null}
    </article>
  );
}

export default function InboxPage() {
  const [payload, setPayload] = useState<InboxPayload | null>(null);
  const [activeTab, setActiveTab] = useState<InboxTab>("all");
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (importSwipe = false) => {
    setError(null);
    if (importSwipe) {
      await fetchJSON("/api/plugins/kanban/inbox/import/carlos-swipe", {
        method: "POST",
      }).catch(() => null);
    }
    const data = await fetchJSON<InboxPayload>(
      "/api/plugins/kanban/inbox?status=pending&recipient=greg",
    );
    setPayload(data);
  }, []);

  useEffect(() => {
    globalThis.document.title = "Inbox | GBAutomation";
    void load(true)
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Inbox unavailable"),
      )
      .finally(() => setLoading(false));
  }, [load]);

  const refresh = async () => {
    setRefreshing(true);
    try {
      await load(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Inbox unavailable");
    } finally {
      setRefreshing(false);
    }
  };

  const respond = async (
    item: InboxItem,
    action: InboxAction,
    response: Record<string, unknown> = {},
    note?: string,
  ) => {
    setBusyIds((current) => new Set(current).add(item.id));
    setError(null);
    try {
      await fetchJSON(`/api/plugins/kanban/inbox/${encodeURIComponent(item.id)}/respond`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, response, note, actor: "dashboard" }),
      });
      if (action !== "note") {
        setPayload((current) =>
          current
            ? {
                ...current,
                items: current.items.filter((candidate) => candidate.id !== item.id),
                pending_total: Math.max(0, current.pending_total - 1),
                counts: {
                  ...current.counts,
                  [item.item_type]: Math.max(0, current.counts[item.item_type] - 1),
                },
              }
            : current,
        );
      } else {
        setPayload((current) =>
          current
            ? {
                ...current,
                items: current.items.map((candidate) =>
                  candidate.id === item.id ? { ...candidate, note: note || null } : candidate,
                ),
              }
            : current,
        );
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save response");
    } finally {
      setBusyIds((current) => {
        const next = new Set(current);
        next.delete(item.id);
        return next;
      });
    }
  };

  const filtered = useMemo(() => {
    const items = payload?.items || [];
    if (activeTab === "decisions") return items.filter((item) => item.item_type === "decision");
    if (activeTab === "forms") {
      return items.filter(
        (item) => item.item_type === "quick_form" || item.item_type === "dynamic_form",
      );
    }
    if (activeTab === "swipe") return items.filter((item) => item.item_type === "swipe");
    return items;
  }, [activeTab, payload]);

  const tabCount = (tab: InboxTab) => {
    if (!payload) return 0;
    if (tab === "all") return payload.pending_total;
    if (tab === "decisions") return payload.counts.decision || 0;
    if (tab === "forms") {
      return (payload.counts.quick_form || 0) + (payload.counts.dynamic_form || 0);
    }
    return payload.counts.swipe || 0;
  };

  return (
    <div className="gbhub-page inbox-page normal-case">
      <section className="gbhub-hero inbox-hero">
        <Link className="gbhub-brand-row" to="/overview">
          <span className="gbhub-mark">gb</span>
          <span>GBAutomation</span>
        </Link>
        <Badge tone="outline" className="gbhub-badge">
          <Sparkles className="h-3 w-3" />
          Daily control plane
        </Badge>
        <div className="inbox-title-row">
          <div>
            <h2>Inbox</h2>
            <p>
              Questions, lightweight forms, and swipe decisions that need a human signal
              before the automation keeps moving.
            </p>
          </div>
          <button disabled={refreshing} onClick={() => void refresh()} type="button">
            <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
            Sync latest
          </button>
        </div>
      </section>

      <nav aria-label="Inbox types" className="inbox-tabs">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              aria-current={activeTab === tab.id ? "page" : undefined}
              className={activeTab === tab.id ? "is-active" : ""}
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              type="button"
            >
              <Icon className="h-4 w-4" />
              <span>{tab.label}</span>
              <strong>{tabCount(tab.id)}</strong>
            </button>
          );
        })}
      </nav>

      <section className="inbox-toolbar">
        <span>
          <SlidersHorizontal className="h-3.5 w-3.5" />
          {filtered.length} pending
        </span>
        <p>Swipe left to archive · up for tomorrow · right to go</p>
      </section>

      {error ? <div className="inbox-error">{error}</div> : null}

      {loading ? (
        <section className="gbhub-empty-state">Loading today&apos;s inbox…</section>
      ) : filtered.length ? (
        <div className={cn("inbox-grid", activeTab === "swipe" && "inbox-swipe-grid")}>
          {filtered.map((item) =>
            item.item_type === "swipe" ? (
              <SwipeCard
                busy={busyIds.has(item.id)}
                item={item}
                key={item.id}
                onRespond={respond}
              />
            ) : (
              <StandardInboxCard
                busy={busyIds.has(item.id)}
                item={item}
                key={item.id}
                onRespond={respond}
              />
            ),
          )}
        </div>
      ) : (
        <section className="gbhub-empty-state inbox-empty">
          <Check className="h-6 w-6" />
          <strong>Inbox clear</strong>
          <span>No pending items in this view.</span>
        </section>
      )}
    </div>
  );
}
