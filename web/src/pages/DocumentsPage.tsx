import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  Archive,
  CalendarDays,
  Check,
  ExternalLink,
  FileText,
  ImageIcon,
  LayoutGrid,
  MessageSquare,
  RotateCcw,
  Search,
  Sparkles,
  Star,
  ThumbsDown,
  Trash2,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { FloatingReviewPanel } from "@/components/FloatingReviewPanel";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { gbautoDocuments, gbautoDocumentsGeneratedAt } from "@/generated/gbautoDocuments";
import { tenantOption, useTenant } from "@/lib/tenant";
import { PluginSlot } from "@/plugins";

interface DocumentArtifact {
  contentScore: number;
  description: string;
  docType: string;
  extension: string;
  favorite: boolean;
  formattingScore: number;
  generatedAt?: string;
  group: string;
  id: string;
  modifiedAt: string;
  publicPath: string;
  previewPath?: string;
  sizeBytes: number;
  sourcePath: string;
  taxonomy: string;
  title: string;
}

interface DocumentFeedback {
  archived: boolean;
  comment: string;
  contentScore: number;
  deleted: boolean;
  downvoted: boolean;
  favorite: boolean;
  formattingScore: number;
  regenerate: boolean;
  submitted: boolean;
}

const documents: DocumentArtifact[] = gbautoDocuments.map((document) => ({ ...document }));

const FEEDBACK_STORAGE_KEY = "hermes.dashboard.documentFeedback";

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown date";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function formatDateKey(dateKey: string) {
  if (dateKey === "unknown") return "Unknown";
  const date = new Date(`${dateKey}T12:00:00`);
  if (Number.isNaN(date.getTime())) return dateKey;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
}

function documentTime(document: DocumentArtifact) {
  const ms = new Date(document.modifiedAt || document.generatedAt || "").getTime();
  return Number.isNaN(ms) ? 0 : ms;
}

function documentDateKey(document: DocumentArtifact) {
  const ms = documentTime(document);
  if (!ms) return "unknown";
  return new Date(ms).toISOString().slice(0, 10);
}

const TENANT_ALIASES: Record<string, string[]> = {
  ecom: ["ecom", "the-mall", "the mall", "mall-client", "vans"],
  jid5274: ["jid5274", "jason-diaz", "jason diaz", "carlos", "pmc"],
  "smoke-client": ["smoke-client", "smoke client", "smoke_client"],
};

const SHARED_ALIASES = ["gbautomation", "gb-automation", "gb automation", "gbauto"];

function tenantSearchBlob(document: DocumentArtifact) {
  return [
    document.id,
    document.title,
    document.description,
    document.group,
    document.sourcePath,
    document.publicPath,
    document.taxonomy,
    document.docType,
  ]
    .filter(Boolean)
    .join(" ")
    .replace(/\\/g, "/")
    .toLowerCase();
}

function matchesAnyAlias(blob: string, aliases: string[]) {
  return aliases.some((alias) => blob.includes(alias));
}

function documentMatchesTenant(document: DocumentArtifact, tenant: string) {
  const blob = tenantSearchBlob(document);
  if (tenant === "gbautomation") {
    const matchesClientTenant = Object.values(TENANT_ALIASES).some((aliases) => matchesAnyAlias(blob, aliases));
    return matchesAnyAlias(blob, SHARED_ALIASES) || !matchesClientTenant;
  }
  return matchesAnyAlias(blob, TENANT_ALIASES[tenant] ?? [tenant]);
}

function dedupeDocuments(source: DocumentArtifact[]) {
  const byArtifact = new Map<string, DocumentArtifact>();
  for (const document of source) {
    const key = document.sourcePath || document.publicPath || document.id;
    const existing = byArtifact.get(key);
    if (!existing || documentTime(document) >= documentTime(existing)) {
      byArtifact.set(key, document);
    }
  }
  return Array.from(byArtifact.values());
}

function formatSize(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function previewKind(document: DocumentArtifact) {
  if (document.extension === "png") return "image";
  if (document.extension === "pdf") return "pdf";
  return "html";
}

function ArtifactPreview({
  className,
  document,
  mode,
}: {
  className: string;
  document: DocumentArtifact;
  mode: "card" | "open";
}) {
  const kind = previewKind(document);
  const previewSource = mode === "card" ? (document.previewPath ?? document.publicPath) : document.publicPath;
  const loading = mode === "card" ? "lazy" : "eager";

  if (kind === "image") {
    return (
      <img
        alt={`${document.title} preview`}
        className={className}
        loading={loading}
        src={previewSource}
      />
    );
  }

  if (mode === "card" && document.previewPath?.endsWith(".png")) {
    return (
      <img
        alt={`${document.title} preview`}
        className={className}
        loading={loading}
        src={document.previewPath}
      />
    );
  }

  return (
    <iframe
      className={className}
      loading={loading}
      src={previewSource}
      title={`${document.title} ${mode === "card" ? "thumbnail" : "report"}`}
    />
  );
}

function defaultFeedback(document: DocumentArtifact): DocumentFeedback {
  return {
    archived: false,
    comment: "",
    contentScore: document.contentScore,
    deleted: false,
    downvoted: false,
    favorite: document.favorite,
    formattingScore: document.formattingScore,
    regenerate: false,
    submitted: false,
  };
}

function DocumentActionButtons({
  document,
  feedback,
  onUpdate,
}: {
  document: DocumentArtifact;
  feedback: DocumentFeedback;
  onUpdate: (document: DocumentArtifact, partial: Partial<DocumentFeedback>) => void;
}) {
  return (
    <div className="documents-action-buttons">
      <button
        aria-label={
          feedback.favorite ? `Remove ${document.title} from favorites` : `Add ${document.title} to favorites`
        }
        className={feedback.favorite ? "documents-icon-action is-active" : "documents-icon-action"}
        onClick={() => onUpdate(document, { favorite: !feedback.favorite })}
        title="Favorite"
        type="button"
      >
        <Star className="h-3.5 w-3.5" />
      </button>
      <button
        aria-label={feedback.archived ? `Unarchive ${document.title}` : `Archive ${document.title}`}
        className={feedback.archived ? "documents-icon-action is-active" : "documents-icon-action"}
        onClick={() => onUpdate(document, { archived: !feedback.archived, deleted: false })}
        title="Archive"
        type="button"
      >
        <Archive className="h-3.5 w-3.5" />
      </button>
      <button
        aria-label={
          feedback.downvoted ? `Remove downvote from ${document.title}` : `Downvote ${document.title}`
        }
        className={feedback.downvoted ? "documents-icon-action is-danger" : "documents-icon-action"}
        onClick={() => onUpdate(document, { downvoted: !feedback.downvoted })}
        title="Downvote"
        type="button"
      >
        <ThumbsDown className="h-3.5 w-3.5" />
      </button>
      <button
        aria-label={feedback.deleted ? `Restore ${document.title}` : `Delete ${document.title}`}
        className={feedback.deleted ? "documents-icon-action is-danger" : "documents-icon-action"}
        onClick={() => onUpdate(document, { deleted: !feedback.deleted, archived: false })}
        title="Delete"
        type="button"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
      <button
        aria-label={
          feedback.regenerate
            ? `Remove ${document.title} from regeneration queue`
            : `Stage ${document.title} for regeneration`
        }
        className={feedback.regenerate ? "documents-icon-action is-active" : "documents-icon-action"}
        onClick={() => onUpdate(document, { regenerate: !feedback.regenerate })}
        title="Stage regeneration"
        type="button"
      >
        <RotateCcw className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function ReportReviewModal({
  document,
  feedback,
  onClose,
  onSubmit,
  onUpdate,
}: {
  document: DocumentArtifact;
  feedback: DocumentFeedback;
  onClose: () => void;
  onSubmit: (document: DocumentArtifact) => void;
  onUpdate: (document: DocumentArtifact, partial: Partial<DocumentFeedback>) => void;
}) {
  const modalRef = useModalBehavior({
    open: true,
    onClose,
    onSubmit: () => onSubmit(document),
    focusSelector: "textarea",
  });
  const titleId = `documents-report-modal-title-${document.id}`;

  return createPortal(
    <div
      aria-labelledby={titleId}
      aria-modal="true"
      className="documents-report-modal"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      role="dialog"
    >
      <div className="documents-report-modal-stage" ref={modalRef}>
        <div className="documents-report-modal-frame-wrap">
          <ArtifactPreview className="documents-report-modal-frame" document={document} mode="open" />
        </div>

        <FloatingReviewPanel
          actions={
            <DocumentActionButtons document={document} feedback={feedback} onUpdate={onUpdate} />
          }
          eyebrow={document.docType}
          onClose={onClose}
          title={document.title}
          titleId={titleId}
        >

          <label className="documents-score-control">
            <span>Content score</span>
            <output>{feedback.contentScore}/10</output>
            <input
              max="10"
              min="1"
              onChange={(event) => onUpdate(document, { contentScore: Number(event.target.value) })}
              type="range"
              value={feedback.contentScore}
            />
          </label>

          <label className="documents-score-control">
            <span>Formatting score</span>
            <output>{feedback.formattingScore}/10</output>
            <input
              max="10"
              min="1"
              onChange={(event) => onUpdate(document, { formattingScore: Number(event.target.value) })}
              type="range"
              value={feedback.formattingScore}
            />
          </label>

          <label className="documents-comment-control">
            <span><MessageSquare className="h-3.5 w-3.5" /> Comments</span>
            <textarea
              onChange={(event) => onUpdate(document, { comment: event.target.value })}
              placeholder="Add review notes. This is staged locally until the Supabase CRUD endpoint is wired."
              value={feedback.comment}
            />
          </label>

          <button className="documents-feedback-submit" onClick={() => onSubmit(document)} type="button">
            {feedback.submitted ? <Check className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />}
            {feedback.submitted ? "Feedback staged" : "Submit feedback"}
          </button>

          <a className="documents-open-link" href={document.publicPath} rel="noreferrer" target="_blank">
            <ExternalLink className="h-3.5 w-3.5" />
            Open raw page
          </a>
        </FloatingReviewPanel>
      </div>
    </div>,
    globalThis.document.body,
  );
}

export default function DocumentsPage() {
  const activeTenant = useTenant();
  const activeTenantOption = tenantOption(activeTenant);
  const [activeDocType, setActiveDocType] = useState("All");
  const [activeTaxonomy, setActiveTaxonomy] = useState("All");
  const [activeGroup, setActiveGroup] = useState("All");
  const [activeDate, setActiveDate] = useState("All");
  const [query, setQuery] = useState("");
  const [showNewOnly, setShowNewOnly] = useState(false);
  // Seed from the bundled static index, then refresh at runtime from
  // /gbauto-documents/index.json so artifacts added by the nightly diff job
  // appear on a browser refresh without rebuilding the SPA.
  const [docs, setDocs] = useState<DocumentArtifact[]>(documents);
  const [recentlyAdded, setRecentlyAdded] = useState<Set<string>>(new Set());
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(documents[0]?.id ?? null);
  const [modalDocumentId, setModalDocumentId] = useState<string | null>(null);
  const [feedbackByDocument, setFeedbackByDocument] = useState<Record<string, DocumentFeedback>>(() => {
    const defaults = documents.reduce<Record<string, DocumentFeedback>>((accumulator, document) => {
      accumulator[document.id] = defaultFeedback(document);
      return accumulator;
    }, {});
    try {
      const stored = window.localStorage.getItem(FEEDBACK_STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored) as Record<string, Partial<DocumentFeedback>>;
        for (const [id, value] of Object.entries(parsed)) {
          if (defaults[id]) {
            defaults[id] = { ...defaults[id], ...value };
          }
        }
      }
    } catch {
      // localStorage may be unavailable or hold malformed JSON; fall back to defaults.
    }
    return defaults;
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(FEEDBACK_STORAGE_KEY, JSON.stringify(feedbackByDocument));
    } catch {
      // localStorage may be unavailable; staged feedback stays in memory only.
    }
  }, [feedbackByDocument]);

  // Pull the freshly-generated index at runtime. The nightly diff job rewrites
  // index.json (and stamps `recentlyAdded`), so this surfaces new artifacts
  // without an SPA rebuild. Falls back silently to the bundled static index.
  useEffect(() => {
    let cancelled = false;
    fetch("/gbauto-documents/index.json", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (cancelled || !data || !Array.isArray(data.artifacts)) return;
        setDocs((data.artifacts as DocumentArtifact[]).map((document) => ({ ...document })));
        if (Array.isArray(data.recentlyAdded)) {
          setRecentlyAdded(new Set(data.recentlyAdded as string[]));
        }
      })
      .catch(() => {
        // Offline / file:// / first build before index.json exists — keep static.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setActiveDocType("All");
    setActiveTaxonomy("All");
    setActiveGroup("All");
    setActiveDate("All");
  }, [activeTenant]);

  const dedupedDocs = useMemo(() => dedupeDocuments(docs), [docs]);
  const duplicateCount = Math.max(0, docs.length - dedupedDocs.length);
  const tenantScopedDocs = useMemo(
    () => dedupedDocs.filter((document) => documentMatchesTenant(document, activeTenant)),
    [activeTenant, dedupedDocs],
  );
  const docTypes = useMemo(() => ["All", ...Array.from(new Set(tenantScopedDocs.map((document) => document.docType)))], [tenantScopedDocs]);
  const taxonomies = useMemo(() => ["All", ...Array.from(new Set(tenantScopedDocs.map((document) => document.taxonomy)))], [tenantScopedDocs]);
  const groups = useMemo(
    () => ["All", ...Array.from(new Set(tenantScopedDocs.map((document) => document.group))).slice(0, 16)],
    [tenantScopedDocs],
  );
  const dateOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const document of tenantScopedDocs) {
      const dateKey = documentDateKey(document);
      counts.set(dateKey, (counts.get(dateKey) ?? 0) + 1);
    }
    return Array.from(counts.entries())
      .map(([dateKey, count]) => ({ count, dateKey }))
      .sort((a, b) => {
        if (a.dateKey === "unknown") return 1;
        if (b.dateKey === "unknown") return -1;
        return b.dateKey.localeCompare(a.dateKey);
      });
  }, [tenantScopedDocs]);

  const hasActiveFilters =
    query.trim() !== "" ||
    showNewOnly ||
    activeDocType !== "All" ||
    activeTaxonomy !== "All" ||
    activeGroup !== "All" ||
    activeDate !== "All";

  const resetFilters = () => {
    setQuery("");
    setShowNewOnly(false);
    setActiveDocType("All");
    setActiveTaxonomy("All");
    setActiveGroup("All");
    setActiveDate("All");
  };

  const filteredDocuments = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return tenantScopedDocs
      .filter((document) => {
        // Drop artifacts the reviewer downvoted, deleted, or archived.
        const fb = feedbackByDocument[document.id];
        if (fb && (fb.archived || fb.deleted || fb.downvoted)) return false;
        if (activeDocType !== "All" && document.docType !== activeDocType) return false;
        if (activeTaxonomy !== "All" && document.taxonomy !== activeTaxonomy) return false;
        if (activeGroup !== "All" && document.group !== activeGroup) return false;
        if (activeDate !== "All" && documentDateKey(document) !== activeDate) return false;
        if (showNewOnly && !recentlyAdded.has(document.id)) return false;
        if (needle) {
          const haystack = [
            document.title,
            document.description,
            document.id,
            document.sourcePath,
            document.publicPath,
            document.group,
            document.taxonomy,
            document.docType,
            document.extension,
            document.modifiedAt,
            document.generatedAt,
          ]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();
          if (!haystack.includes(needle)) return false;
        }
        return true;
      })
      // Newest first.
      .sort((a, b) => documentTime(b) - documentTime(a));
  }, [activeDate, activeDocType, activeGroup, activeTaxonomy, query, showNewOnly, recentlyAdded, feedbackByDocument, tenantScopedDocs]);

  const selectedDocument =
    filteredDocuments.find((document) => document.id === selectedDocumentId) ?? filteredDocuments[0] ?? null;
  const selectedFeedback = selectedDocument ? feedbackByDocument[selectedDocument.id] ?? defaultFeedback(selectedDocument) : null;
  const modalDocument = docs.find((document) => document.id === modalDocumentId) ?? null;
  const modalFeedback = modalDocument ? feedbackByDocument[modalDocument.id] ?? defaultFeedback(modalDocument) : null;

  const updateFeedback = (document: DocumentArtifact, partial: Partial<DocumentFeedback>) => {
    setFeedbackByDocument((current) => {
      const existing = current[document.id] ?? defaultFeedback(document);
      return {
        ...current,
        [document.id]: {
          ...existing,
          ...partial,
          submitted:
            partial.comment === undefined && partial.contentScore === undefined && partial.formattingScore === undefined
              ? existing.submitted
              : false,
        },
      };
    });
  };

  const submitFeedback = (document: DocumentArtifact) => {
    setFeedbackByDocument((current) => ({
      ...current,
      [document.id]: {
        ...(current[document.id] ?? defaultFeedback(document)),
        submitted: true,
      },
    }));
  };

  return (
    <div className="documents-gallery flex w-full min-w-0 flex-col gap-6 normal-case">
      <PluginSlot name="documents:top" />

      <section className="documents-hero">
        <div className="min-w-0">
          <p className="documents-eyebrow">GBAutomation artifacts</p>
          <h2>Artifact card gallery</h2>
          <p>
            Real HTML, website page views, PDFs, and PNG artifacts scanned from the GBAutomation workspace
            and served as static website assets for review. Index generated {formatDate(gbautoDocumentsGeneratedAt)}.
          </p>
        </div>
        <Badge tone="outline" className="documents-hero-badge">
          <LayoutGrid className="h-3 w-3" />
          {activeTenantOption.label}: {filteredDocuments.length} shown{duplicateCount ? `, ${duplicateCount} deduped` : ""}
        </Badge>
      </section>

      <section className="documents-filter-bar" aria-label="Document filters">
        <label className="documents-search">
          <Search className="h-3.5 w-3.5" />
          <input
            aria-label="Search artifacts"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search artifacts by title, path, group…"
            type="search"
            value={query}
          />
        </label>
        <select
          aria-label="Filter by doc type"
          className="documents-filter-select"
          onChange={(event) => setActiveDocType(event.target.value)}
          value={activeDocType}
        >
          {docTypes.map((type) => (
            <option key={type} value={type}>
              {type === "All" ? "All types" : type}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by taxonomy"
          className="documents-filter-select"
          onChange={(event) => setActiveTaxonomy(event.target.value)}
          value={activeTaxonomy}
        >
          {taxonomies.map((taxonomy) => (
            <option key={taxonomy} value={taxonomy}>
              {taxonomy === "All" ? "All taxonomies" : taxonomy}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by set"
          className="documents-filter-select"
          onChange={(event) => setActiveGroup(event.target.value)}
          value={activeGroup}
        >
          {groups.map((group) => (
            <option key={group} value={group}>
              {group === "All" ? "All sets" : group}
            </option>
          ))}
        </select>
        <button
          aria-pressed={showNewOnly}
          className={showNewOnly ? "documents-new-toggle is-active" : "documents-new-toggle"}
          disabled={recentlyAdded.size === 0}
          onClick={() => setShowNewOnly((value) => !value)}
          title={
            recentlyAdded.size === 0
              ? "No new artifacts since the last nightly index"
              : "Show only artifacts added by the latest nightly diff"
          }
          type="button"
        >
          <Sparkles className="h-3.5 w-3.5" />
          New{recentlyAdded.size ? ` ${recentlyAdded.size}` : ""}
        </button>
        {hasActiveFilters ? (
          <button className="documents-filter-clear" onClick={resetFilters} type="button">
            Clear
          </button>
        ) : null}
      </section>

      <section className="documents-date-filter" aria-label="Artifact date filter">
        <div className="documents-date-filter-heading">
          <CalendarDays className="h-3.5 w-3.5" />
          <span>Date filter</span>
          <small>{activeDate === "All" ? "All dates" : formatDateKey(activeDate)}</small>
        </div>
        <input
          aria-label="Select artifact date"
          className="documents-date-input"
          onChange={(event) => setActiveDate(event.target.value || "All")}
          type="date"
          value={activeDate === "All" || activeDate === "unknown" ? "" : activeDate}
        />
        <div className="documents-date-calendar" role="list" aria-label="Available artifact dates">
          <button
            aria-pressed={activeDate === "All"}
            className={activeDate === "All" ? "documents-date-chip is-active" : "documents-date-chip"}
            onClick={() => setActiveDate("All")}
            type="button"
          >
            <span>All</span>
            <small>{tenantScopedDocs.length}</small>
          </button>
          {dateOptions.map(({ count, dateKey }) => (
            <button
              aria-pressed={activeDate === dateKey}
              className={activeDate === dateKey ? "documents-date-chip is-active" : "documents-date-chip"}
              key={dateKey}
              onClick={() => setActiveDate(dateKey)}
              type="button"
            >
              <span>{formatDateKey(dateKey)}</span>
              <small>{count}</small>
            </button>
          ))}
        </div>
      </section>

      <div className="documents-artifact-grid">
        {filteredDocuments.map((document) => {
          const feedback = feedbackByDocument[document.id] ?? defaultFeedback(document);
          return (
            <article
              className={
                selectedDocument?.id === document.id
                  ? "documents-artifact-card is-selected"
                  : "documents-artifact-card"
              }
              key={document.id}
            >
              <div className="documents-artifact-preview-shell">
                <ArtifactPreview className="documents-preview-frame" document={document} mode="card" />
                <button
                  aria-label={`Open report preview for ${document.title}`}
                  className="documents-artifact-preview-button"
                  onClick={() => {
                    setSelectedDocumentId(document.id);
                    setModalDocumentId(document.id);
                  }}
                  type="button"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  Open
                </button>
              </div>

              <div className="documents-card-body">
                <div className="flex items-center justify-between gap-3">
                  <span className="documents-card-eyebrow inline-flex items-center gap-1.5">
                    {document.docType}
                    {recentlyAdded.has(document.id) ? (
                      <span className="documents-card-new-badge">New</span>
                    ) : null}
                  </span>
                  <DocumentActionButtons document={document} feedback={feedback} onUpdate={updateFeedback} />
                </div>
                <h3>{document.title}</h3>
                <p>{document.description}</p>
                <div className="documents-card-meta">
                  <span>{document.taxonomy}</span>
                  <span>{document.extension.toUpperCase()}</span>
                  <span>{formatSize(document.sizeBytes)}</span>
                  <span>{formatDate(document.modifiedAt)}</span>
                </div>
              </div>
            </article>
          );
        })}
      </div>

      {filteredDocuments.length === 0 ? (
        <section className="documents-empty-state">
          <Search className="h-4 w-4" />
          <span>No artifacts match that filter set.</span>
        </section>
      ) : null}

      {selectedDocument && selectedFeedback ? (
        <section className="documents-open-report">
          <div className="documents-open-report-header">
            <div className="min-w-0">
              <p className="documents-card-eyebrow">{selectedDocument.docType}</p>
              <h3>{selectedDocument.title}</h3>
              <p>{selectedDocument.sourcePath}</p>
            </div>
            <div className="documents-open-actions">
              <a className="documents-open-link" href={selectedDocument.publicPath} rel="noreferrer" target="_blank">
                <ExternalLink className="h-3.5 w-3.5" />
                Open page
              </a>
              <DocumentActionButtons document={selectedDocument} feedback={selectedFeedback} onUpdate={updateFeedback} />
            </div>
          </div>

          <div className="documents-open-report-layout">
            <div className="documents-open-report-frame-wrap">
              <ArtifactPreview className="documents-open-report-frame" document={selectedDocument} mode="open" />
              <div className="documents-report-review-overlay">
                <button
                  aria-label={
                    selectedFeedback.favorite
                      ? `Remove ${selectedDocument.title} from favorites`
                      : `Add ${selectedDocument.title} to favorites`
                  }
                  className={selectedFeedback.favorite ? "documents-overlay-favorite is-active" : "documents-overlay-favorite"}
                  onClick={() => updateFeedback(selectedDocument, { favorite: !selectedFeedback.favorite })}
                  type="button"
                >
                  <Star className="h-3.5 w-3.5" />
                </button>
                <label>
                  <span>Content</span>
                  <output>{selectedFeedback.contentScore}/10</output>
                  <input
                    max="10"
                    min="1"
                    onChange={(event) => updateFeedback(selectedDocument, { contentScore: Number(event.target.value) })}
                    type="range"
                    value={selectedFeedback.contentScore}
                  />
                </label>
                <label>
                  <span>Format</span>
                  <output>{selectedFeedback.formattingScore}/10</output>
                  <input
                    max="10"
                    min="1"
                    onChange={(event) => updateFeedback(selectedDocument, { formattingScore: Number(event.target.value) })}
                    type="range"
                    value={selectedFeedback.formattingScore}
                  />
                </label>
              </div>
            </div>

            <aside className="documents-feedback-panel" aria-label={`${selectedDocument.title} feedback`}>
              <div>
                <p className="documents-card-eyebrow">Report feedback</p>
                <h4>Review scores</h4>
              </div>

              <label className="documents-score-control">
                <span>Content score</span>
                <output>{selectedFeedback.contentScore}/10</output>
                <input
                  max="10"
                  min="1"
                  onChange={(event) => updateFeedback(selectedDocument, { contentScore: Number(event.target.value) })}
                  type="range"
                  value={selectedFeedback.contentScore}
                />
              </label>

              <label className="documents-score-control">
                <span>Formatting score</span>
                <output>{selectedFeedback.formattingScore}/10</output>
                <input
                  max="10"
                  min="1"
                  onChange={(event) => updateFeedback(selectedDocument, { formattingScore: Number(event.target.value) })}
                  type="range"
                  value={selectedFeedback.formattingScore}
                />
              </label>

              <label className="documents-comment-control">
                <span><MessageSquare className="h-3.5 w-3.5" /> Comments</span>
                <textarea
                  onChange={(event) => updateFeedback(selectedDocument, { comment: event.target.value })}
                  placeholder="Add review notes for this report."
                  value={selectedFeedback.comment}
                />
              </label>

              <button
                className="documents-feedback-submit"
                onClick={() => submitFeedback(selectedDocument)}
                type="button"
              >
                {selectedFeedback.submitted ? <Check className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />}
                {selectedFeedback.submitted ? "Feedback saved" : "Submit feedback"}
              </button>
            </aside>
          </div>
        </section>
      ) : null}

      <section className="documents-render-strip">
        <div>
          <p className="documents-eyebrow">Static artifact index</p>
          <h3>Served HTML, PDFs, and visuals</h3>
        </div>
        <div className="documents-render-points">
          <span><FileText className="h-3.5 w-3.5" /> HTML/PDF</span>
          <span><ImageIcon className="h-3.5 w-3.5" /> PNG previews</span>
          <span><LayoutGrid className="h-3.5 w-3.5" /> review grid</span>
        </div>
      </section>

      {modalDocument && modalFeedback ? (
        <ReportReviewModal
          document={modalDocument}
          feedback={modalFeedback}
          onClose={() => setModalDocumentId(null)}
          onSubmit={submitFeedback}
          onUpdate={updateFeedback}
        />
      ) : null}

      <PluginSlot name="documents:bottom" />
    </div>
  );
}
