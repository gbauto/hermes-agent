import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Archive,
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
  Trash2,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { FloatingReviewPanel } from "@/components/FloatingReviewPanel";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { gbautoDocuments, gbautoDocumentsGeneratedAt } from "@/generated/gbautoDocuments";
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
  favorite: boolean;
  formattingScore: number;
  regenerate: boolean;
  submitted: boolean;
}

const documents: DocumentArtifact[] = gbautoDocuments.map((document) => ({ ...document }));

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown date";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
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
  const modalRef = useModalBehavior({ open: true, onClose });
  const commentRef = useRef<HTMLTextAreaElement>(null);
  const titleId = `documents-report-modal-title-${document.id}`;

  useEffect(() => {
    const timer = window.setTimeout(() => commentRef.current?.focus(), 120);
    return () => window.clearTimeout(timer);
  }, [document.id]);

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
              ref={commentRef}
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
  const [activeDocType, setActiveDocType] = useState("All");
  const [activeTaxonomy, setActiveTaxonomy] = useState("All");
  const [activeGroup, setActiveGroup] = useState("All");
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(documents[0]?.id ?? null);
  const [modalDocumentId, setModalDocumentId] = useState<string | null>(null);
  const [feedbackByDocument, setFeedbackByDocument] = useState<Record<string, DocumentFeedback>>(() => {
    return documents.reduce<Record<string, DocumentFeedback>>((accumulator, document) => {
      accumulator[document.id] = defaultFeedback(document);
      return accumulator;
    }, {});
  });

  const docTypes = useMemo(() => ["All", ...Array.from(new Set(documents.map((document) => document.docType)))], [documents]);
  const taxonomies = useMemo(() => ["All", ...Array.from(new Set(documents.map((document) => document.taxonomy)))], [documents]);
  const groups = useMemo(
    () => ["All", ...Array.from(new Set(documents.map((document) => document.group))).slice(0, 16)],
    [documents],
  );

  const filteredDocuments = useMemo(() => {
    return documents.filter((document) => {
      const matchesDocType = activeDocType === "All" || document.docType === activeDocType;
      const matchesTaxonomy = activeTaxonomy === "All" || document.taxonomy === activeTaxonomy;
      const matchesGroup = activeGroup === "All" || document.group === activeGroup;
      return matchesDocType && matchesTaxonomy && matchesGroup;
    });
  }, [activeDocType, activeGroup, activeTaxonomy, documents]);

  const selectedDocument =
    documents.find((document) => document.id === selectedDocumentId) ?? filteredDocuments[0] ?? documents[0] ?? null;
  const selectedFeedback = selectedDocument ? feedbackByDocument[selectedDocument.id] ?? defaultFeedback(selectedDocument) : null;
  const modalDocument = documents.find((document) => document.id === modalDocumentId) ?? null;
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
          {filteredDocuments.length} shown
        </Badge>
      </section>

      <section className="documents-filter-panel" aria-label="Document filters">
        <div className="documents-filter-row">
          <span className="documents-filter-label">Doc type</span>
          <div className="documents-filter-pills">
            {docTypes.map((type) => (
              <button
                className={type === activeDocType ? "documents-filter-pill is-active" : "documents-filter-pill"}
                key={type}
                onClick={() => setActiveDocType(type)}
                type="button"
              >
                {type}
              </button>
            ))}
          </div>
        </div>
        <div className="documents-filter-row">
          <span className="documents-filter-label">Taxonomy</span>
          <div className="documents-filter-pills">
            {taxonomies.map((taxonomy) => (
              <button
                className={taxonomy === activeTaxonomy ? "documents-filter-pill is-active" : "documents-filter-pill"}
                key={taxonomy}
                onClick={() => setActiveTaxonomy(taxonomy)}
                type="button"
              >
                {taxonomy}
              </button>
            ))}
          </div>
        </div>
        <div className="documents-filter-row">
          <span className="documents-filter-label">Set</span>
          <div className="documents-filter-pills">
            {groups.map((group) => (
              <button
                className={group === activeGroup ? "documents-filter-pill is-active" : "documents-filter-pill"}
                key={group}
                onClick={() => setActiveGroup(group)}
                type="button"
              >
                {group}
              </button>
            ))}
          </div>
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
                <div className="documents-card-score-overlay" aria-hidden="true">
                  <span>C {feedback.contentScore}</span>
                  <span>F {feedback.formattingScore}</span>
                </div>
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
                  <span className="documents-card-eyebrow">{document.docType}</span>
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
                <div className="documents-card-feedback">
                  <label>
                    <span>Content</span>
                    <output>{feedback.contentScore}/10</output>
                    <input
                      max="10"
                      min="1"
                      onChange={(event) => updateFeedback(document, { contentScore: Number(event.target.value) })}
                      type="range"
                      value={feedback.contentScore}
                    />
                  </label>
                  <label>
                    <span>Format</span>
                    <output>{feedback.formattingScore}/10</output>
                    <input
                      max="10"
                      min="1"
                      onChange={(event) => updateFeedback(document, { formattingScore: Number(event.target.value) })}
                      type="range"
                      value={feedback.formattingScore}
                    />
                  </label>
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
