import { createPortal } from "react-dom";
import { AlertTriangle } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { cn } from "@/lib/utils";

export function ConfirmDialog({
  cancelLabel = "Cancel",
  confirmLabel = "Confirm",
  description,
  destructive = false,
  loading = false,
  onCancel,
  onConfirm,
  open,
  title,
}: ConfirmDialogProps) {
  const dialogRef = useModalBehavior({
    open,
    onClose: onCancel,
    onSubmit: onConfirm,
    canSubmit: !loading,
    focusSelector: "[data-confirm]",
  });

  if (!open) return null;

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      aria-describedby={description ? "confirm-dialog-desc" : undefined}
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
      className={cn(
        "fixed inset-0 z-50 flex items-center justify-center",
        "bg-black/60 backdrop-blur-sm",
        "animate-[fade-in_150ms_ease-out]",
      )}
    >
      <div
        ref={dialogRef}
        className={cn(
          "relative w-full max-w-md mx-4",
          "border border-border bg-card shadow-lg",
          "animate-[dialog-in_180ms_ease-out]",
        )}
      >
        <div className="flex items-start gap-3 p-4 border-b border-border">
          {destructive && (
            <div
              aria-hidden
              className="mt-0.5 shrink-0 text-destructive"
            >
              <AlertTriangle className="h-4 w-4" />
            </div>
          )}

          <div className="flex-1 min-w-0 flex flex-col gap-1">
            <h2
              id="confirm-dialog-title"
              className="font-expanded text-sm font-bold tracking-[0.08em] uppercase blend-lighter"
            >
              {title}
            </h2>

            {description && (
              <p
                id="confirm-dialog-desc"
                className="font-mondwest text-xs text-muted-foreground leading-relaxed"
              >
                {description}
              </p>
            )}
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 p-3">
          <Button
            type="button"
            outlined
            onClick={onCancel}
            disabled={loading}
          >
            {cancelLabel}
          </Button>
          <Button
            data-confirm
            type="button"
            destructive={destructive}
            onClick={onConfirm}
            disabled={loading}
          >
            {loading ? "…" : confirmLabel}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

interface ConfirmDialogProps {
  cancelLabel?: string;
  confirmLabel?: string;
  description?: string;
  destructive?: boolean;
  loading?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  open: boolean;
  title: string;
}
