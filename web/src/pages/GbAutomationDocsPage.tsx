import { useCallback, useEffect, useMemo, useState } from "react";
import { BookOpen, FileText, RefreshCw, Search } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { fetchJSON } from "@/lib/api";

/**
 * GB Automation Docs — live vault documentation browser.
 *
 * Reads markdown straight off the gbautomation checkout on this host through
 * the dashboard's own /api/fs endpoints (session-token authed via fetchJSON),
 * so pages are always current with `main` — no generated snapshot, no rebuild
 * to refresh content.
 *
 * Rendering is react-markdown + remark-gfm (tables, strikethrough, task
 * lists) with doc-grade typography — deliberately NOT the shared chat
 * <Markdown/> component, which is a lightweight non-GFM renderer.
 */

const REPO_ROOT = "/Users/greg/repos/gbautomation";

const DOC_GROUPS: { id: string; label: string; dir: string }[] = [
  { id: "systems", label: "Systems", dir: `${REPO_ROOT}/second-brain/systems` },
  { id: "ops", label: "Ops", dir: `${REPO_ROOT}/second-brain/knowledge/ops` },
  { id: "decisions", label: "Decisions", dir: `${REPO_ROOT}/second-brain/intelligence/decisions` },
];

const DEFAULT_DOC = "kanban-telegram-alerts-and-live-board.md";

interface FsEntry {
  name: string;
  path: string;
  isDirectory: boolean;
  /** Epoch seconds from /api/fs/list; drives the newest-first ordering. */
  modifiedAt?: number | null;
}

function formatDate(ts?: number | null): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function prettyTitle(name: string): string {
  const stem = name.replace(/\.md$/i, "").replace(/^[_]/, "");
  return stem.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Split YAML frontmatter off a vault note; surface `title:` when present.
 * Also drops a leading `# Heading` line — the page header already shows the
 * title, so keeping it would render it twice. */
function splitFrontmatter(text: string): { title: string | null; body: string } {
  let title: string | null = null;
  let body = text;
  const match = body.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (match) {
    const titleLine = match[1].match(/^title:\s*["']?(.+?)["']?\s*$/m);
    if (titleLine) title = titleLine[1];
    body = body.slice(match[0].length);
  }
  const h1 = body.match(/^\s*#\s+(.+)\r?\n/);
  if (h1) {
    if (!title) title = h1[1].trim();
    body = body.slice(h1[0].length).replace(/^\s*\n/, "");
  }
  return { title, body };
}

/** Vault notes use [[wikilinks]]; render them as highlighted references. */
function renderWikilinks(body: string): string {
  return body.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_m, target, label) => {
    return `\`↗ ${label ?? target}\``;
  });
}

function DocMarkdown({ body }: { body: string }) {
  return (
    <div className="gbauto-doc text-[13.5px] leading-6 text-foreground">
      <ReactMarkdown
        components={{
          a: ({ href, children }) => (
            <a
              className="font-medium text-primary underline decoration-primary/40 underline-offset-2 hover:decoration-primary"
              href={href}
              rel="noreferrer"
              target={href?.startsWith("http") ? "_blank" : undefined}
            >
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote className="my-4 border-l-2 border-primary/50 bg-muted/40 px-4 py-2 text-muted-foreground [&>p]:my-1">
              {children}
            </blockquote>
          ),
          code: ({ className, children }) =>
            className ? (
              <code className={className}>{children}</code>
            ) : (
              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[12px] text-foreground">
                {children}
              </code>
            ),
          h1: ({ children }) => (
            <h1 className="mb-4 mt-10 border-b border-border pb-2 text-xl font-semibold first:mt-0">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-3 mt-9 border-b border-border pb-1.5 text-lg font-semibold first:mt-0">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-2 mt-7 text-base font-semibold first:mt-0">{children}</h3>
          ),
          h4: ({ children }) => (
            <h4 className="mb-2 mt-5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              {children}
            </h4>
          ),
          hr: () => <hr className="my-8 border-border" />,
          li: ({ children }) => <li className="leading-6 [&>p]:my-0.5">{children}</li>,
          ol: ({ children }) => (
            <ol className="my-3 list-decimal space-y-1.5 pl-6 marker:text-muted-foreground">
              {children}
            </ol>
          ),
          p: ({ children }) => <p className="my-3">{children}</p>,
          pre: ({ children }) => (
            <pre className="my-4 overflow-x-auto rounded-lg border border-border bg-muted/50 p-3.5 font-mono text-xs leading-5">
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div className="my-5 overflow-x-auto rounded-lg border border-border">
              <table className="w-full border-collapse text-[12.5px]">{children}</table>
            </div>
          ),
          td: ({ children }) => (
            <td className="border-t border-border px-3 py-2 align-top leading-5 [&>code]:whitespace-nowrap">
              {children}
            </td>
          ),
          th: ({ children }) => (
            <th className="bg-muted/60 px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {children}
            </th>
          ),
          tr: ({ children }) => <tr className="even:bg-muted/20">{children}</tr>,
          ul: ({ children }) => (
            <ul className="my-3 list-disc space-y-1.5 pl-6 marker:text-muted-foreground">
              {children}
            </ul>
          ),
        }}
        remarkPlugins={[remarkGfm]}
      >
        {body}
      </ReactMarkdown>
    </div>
  );
}

export default function GbAutomationDocsPage() {
  const [groupId, setGroupId] = useState<string>(DOC_GROUPS[0].id);
  const [entries, setEntries] = useState<FsEntry[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);
  const [query, setQuery] = useState<string>("");

  const group = useMemo(
    () => DOC_GROUPS.find((g) => g.id === groupId) ?? DOC_GROUPS[0],
    [groupId],
  );

  const loadList = useCallback(async () => {
    setListError(null);
    try {
      const payload = await fetchJSON<{ entries?: FsEntry[]; error?: string }>(
        `/api/fs/list?path=${encodeURIComponent(group.dir)}`,
      );
      if (payload.error) throw new Error(payload.error);
      const docs = (payload.entries ?? [])
        .filter((e) => !e.isDirectory && /\.md$/i.test(e.name))
        .sort(
          (a, b) =>
            (b.modifiedAt ?? 0) - (a.modifiedAt ?? 0) || b.name.localeCompare(a.name),
        );
      setEntries(docs);
      setSelected((prev) => {
        if (prev && docs.some((d) => d.path === prev)) return prev;
        const preferred = docs.find((d) => d.name === DEFAULT_DOC);
        return (preferred ?? docs[0])?.path ?? null;
      });
    } catch (err) {
      setEntries([]);
      setSelected(null);
      setListError(err instanceof Error ? err.message : "listing failed");
    }
  }, [group.dir]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (!selected) {
      setContent("");
      return;
    }
    let cancelled = false;
    setLoading(true);
    fetchJSON<{ text?: string }>(`/api/fs/read-text?path=${encodeURIComponent(selected)}`)
      .then((payload) => {
        if (!cancelled) setContent(typeof payload.text === "string" ? payload.text : "");
      })
      .catch(() => {
        if (!cancelled) setContent("_Failed to load document._");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter((e) => e.name.toLowerCase().includes(q));
  }, [entries, query]);

  const { title, body } = useMemo(() => splitFrontmatter(content), [content]);
  const rendered = useMemo(() => renderWikilinks(body), [body]);
  const selectedEntry = entries.find((e) => e.path === selected) ?? null;

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-72 shrink-0 flex-col border-r border-border">
        <div className="flex items-center gap-2 border-b border-border p-3">
          <BookOpen className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-semibold">GBAuto Docs</span>
          <button
            className="ml-auto rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            onClick={() => void loadList()}
            title="Refresh list"
            type="button"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="flex gap-1 border-b border-border p-2">
          {DOC_GROUPS.map((g) => (
            <button
              className={
                g.id === groupId
                  ? "rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground"
                  : "rounded-md px-2.5 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
              }
              key={g.id}
              onClick={() => setGroupId(g.id)}
              type="button"
            >
              {g.label}
            </button>
          ))}
        </div>
        <div className="relative border-b border-border p-2">
          <Search className="pointer-events-none absolute left-4 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            className="w-full rounded-md border border-border bg-background py-1.5 pl-8 pr-2 text-xs outline-none focus:ring-1 focus:ring-ring"
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter docs…"
            value={query}
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {listError ? (
            <p className="px-2 py-4 text-xs text-destructive">
              Could not list {group.dir}: {listError}
            </p>
          ) : filtered.length === 0 ? (
            <p className="px-2 py-4 text-xs text-muted-foreground">No markdown docs found.</p>
          ) : (
            filtered.map((entry) => (
              <button
                className={
                  entry.path === selected
                    ? "flex w-full items-start gap-2 rounded-md bg-accent px-2 py-1.5 text-left text-xs font-medium text-foreground"
                    : "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-accent/60 hover:text-foreground"
                }
                key={entry.path}
                onClick={() => setSelected(entry.path)}
                type="button"
              >
                <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span className="flex-1 break-words">{prettyTitle(entry.name)}</span>
                <span className="mt-0.5 shrink-0 text-[10px] tabular-nums text-muted-foreground/70">
                  {formatDate(entry.modifiedAt)}
                </span>
              </button>
            ))
          )}
        </div>
      </aside>
      <main className="flex min-h-0 flex-1 flex-col">
        <div className="shrink-0 border-b border-border bg-muted/40 px-6 py-2 text-[11.5px] leading-5 text-muted-foreground">
          <span className="font-semibold text-foreground">Live source:</span>{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[10.5px]">{group.dir}</code>{" "}
          — this dashboard host&apos;s pull-only checkout of{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[10.5px]">
            gbauto/gbautomation@main
          </code>
          . Docs are read at view time via the dashboard&apos;s /api/fs endpoints (no snapshot,
          no rebuild), sorted newest-first. Client rollout: point{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[10.5px]">REPO_ROOT</code> /{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[10.5px]">DOC_GROUPS</code>{" "}
          at the tenant&apos;s repo checkout.
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
        {selectedEntry ? (
          <div className="mx-auto max-w-4xl px-8 py-7">
            <h1 className="mb-1 text-2xl font-semibold tracking-tight">
              {title ?? prettyTitle(selectedEntry.name)}
            </h1>
            <p className="mb-6 break-all font-mono text-[11px] text-muted-foreground">
              {selectedEntry.path.replace(`${REPO_ROOT}/`, "")}
            </p>
            {loading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              <DocMarkdown body={rendered} />
            )}
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Select a document.
          </div>
        )}
        </div>
      </main>
    </div>
  );
}
