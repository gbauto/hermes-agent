import { useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen,
  Check,
  FileText,
  Keyboard,
  MessageSquare,
  Package,
  Search,
  Send,
  TableProperties,
  X,
} from "lucide-react";

export type CommandLayerMode = "commands" | "feedback" | "shortcuts";

export interface CommandLayerAction {
  description: string;
  keys: string;
  label: string;
  run: () => void;
}

export function CommandLayerOverlay({
  actions,
  mode,
  onClose,
  route,
}: {
  actions: CommandLayerAction[];
  mode: CommandLayerMode;
  onClose: () => void;
  route: string;
}) {
  const [query, setQuery] = useState("");
  const [feedback, setFeedback] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (mode === "feedback") textareaRef.current?.focus();
      else inputRef.current?.focus();
    }, 80);
    return () => window.clearTimeout(timer);
  }, [mode]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const filteredActions = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return actions;
    return actions.filter((action) =>
      `${action.label} ${action.description} ${action.keys}`.toLowerCase().includes(normalized),
    );
  }, [actions, query]);

  const submitFeedback = () => {
    setSubmitted(true);
    window.setTimeout(() => setSubmitted(false), 2000);
  };

  return (
    <div
      aria-modal="true"
      className="command-layer-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      role="dialog"
    >
      <section className="command-layer-panel" aria-labelledby="command-layer-title">
        <div className="command-layer-header">
          <div className="min-w-0">
            <p className="command-layer-eyebrow">
              {mode === "feedback" ? "Feedback" : mode === "shortcuts" ? "Shortcuts" : "Command layer"}
            </p>
            <h2 id="command-layer-title">
              {mode === "feedback" ? "Submit feedback" : mode === "shortcuts" ? "Keyboard shortcuts" : "Open command"}
            </h2>
          </div>
          <button aria-label="Close command layer" className="command-layer-close" onClick={onClose} type="button">
            <X className="h-4 w-4" />
          </button>
        </div>

        {mode === "feedback" ? (
          <div className="command-feedback-form">
            <div className="command-layer-route">
              <FileText className="h-3.5 w-3.5" />
              <span>{route}</span>
            </div>
            <label>
              <span>Notes</span>
              <textarea
                ref={textareaRef}
                onChange={(event) => setFeedback(event.target.value)}
                placeholder="Describe what should change. This is staged locally until the Supabase feedback endpoint is wired."
                value={feedback}
              />
            </label>
            <button className="command-layer-submit" onClick={submitFeedback} type="button">
              {submitted ? <Check className="h-3.5 w-3.5" /> : <Send className="h-3.5 w-3.5" />}
              {submitted ? "Feedback staged" : "Submit feedback"}
            </button>
          </div>
        ) : (
          <>
            <label className="command-layer-search">
              <Search className="h-4 w-4" />
              <input
                ref={inputRef}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && filteredActions[0]) {
                    event.preventDefault();
                    filteredActions[0].run();
                  }
                }}
                placeholder="Search commands"
                value={query}
              />
            </label>

            <div className="command-layer-list">
              {filteredActions.map((action) => (
                <button
                  className="command-layer-item"
                  key={action.keys}
                  onClick={action.run}
                  type="button"
                >
                  <CommandIcon label={action.label} />
                  <span className="command-layer-item-copy">
                    <strong>{action.label}</strong>
                    <small>{action.description}</small>
                  </span>
                  <kbd>{action.keys}</kbd>
                </button>
              ))}
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function CommandIcon({ label }: { label: string }) {
  if (label.includes("Chat")) return <MessageSquare className="h-4 w-4" />;
  if (label.includes("Board")) return <TableProperties className="h-4 w-4" />;
  if (label.includes("Library")) return <Package className="h-4 w-4" />;
  if (label.includes("Brain")) return <BookOpen className="h-4 w-4" />;
  return <Keyboard className="h-4 w-4" />;
}
