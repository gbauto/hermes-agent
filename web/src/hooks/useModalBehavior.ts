import { useEffect, useRef } from "react";

const DEFAULT_FOCUS_SELECTOR =
  "input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex='-1'])";

/**
 * Hook that adds standard modal behaviors when `open` is true:
 * - Escape key calls `onClose`
 * - Ctrl/Cmd+Enter calls `onSubmit`, when supplied
 * - Body scroll is locked
 * - The first interactive control is focused
 * - Focus is restored to the previously focused element on close
 *
 * Returns a ref to attach to the modal container.
 */
export function useModalBehavior({
  canSubmit = true,
  focusSelector = DEFAULT_FOCUS_SELECTOR,
  open,
  onClose,
  onSubmit,
}: {
  canSubmit?: boolean;
  focusSelector?: string;
  open: boolean;
  onClose: () => void;
  onSubmit?: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canSubmitRef = useRef(canSubmit);
  const onCloseRef = useRef(onClose);
  const onSubmitRef = useRef(onSubmit);

  useEffect(() => {
    canSubmitRef.current = canSubmit;
    onCloseRef.current = onClose;
    onSubmitRef.current = onSubmit;
  }, [canSubmit, onClose, onSubmit]);

  useEffect(() => {
    if (!open) return;

    const prevActive = document.activeElement as HTMLElement | null;

    const focusTimer = window.setTimeout(() => {
      containerRef.current
        ?.querySelector<HTMLElement>(focusSelector)
        ?.focus();
    }, 0);

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      if (
        e.key === "Enter" &&
        (e.ctrlKey || e.metaKey) &&
        !e.isComposing &&
        onSubmitRef.current &&
        canSubmitRef.current
      ) {
        e.preventDefault();
        onSubmitRef.current();
      }
    };

    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      prevActive?.focus?.();
    };
  }, [focusSelector, open]);

  return containerRef;
}
