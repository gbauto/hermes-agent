import type { ReactNode } from "react";
import { X } from "lucide-react";

export function FloatingReviewPanel({
  actions,
  children,
  eyebrow,
  onClose,
  title,
  titleId,
}: FloatingReviewPanelProps) {
  return (
    <aside className="floating-review-panel">
      <div className="floating-review-panel-top">
        <div className="min-w-0">
          {eyebrow ? <p className="documents-card-eyebrow">{eyebrow}</p> : null}
          <h3 id={titleId}>{title}</h3>
        </div>
        <button aria-label="Close review panel" className="floating-review-panel-close" onClick={onClose} type="button">
          <X className="h-4 w-4" />
        </button>
      </div>
      {actions ? <div className="floating-review-panel-actions">{actions}</div> : null}
      {children}
    </aside>
  );
}

interface FloatingReviewPanelProps {
  actions?: ReactNode;
  children: ReactNode;
  eyebrow?: string;
  onClose: () => void;
  title: string;
  titleId?: string;
}
