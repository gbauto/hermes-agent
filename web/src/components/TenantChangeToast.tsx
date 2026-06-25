import { useEffect, useRef, useState } from "react";

import { tenantOption, useTenant } from "@/lib/tenant";

/**
 * Global, page-agnostic confirmation that the active client changed.
 *
 * Mounted once in the App shell; renders a fixed top-center pill for a
 * couple of seconds whenever the tenant selection changes. Because most
 * dashboard pages are org-wide (not client-scoped), this is the signal
 * that a selection actually registered.
 */
export function TenantChangeToast() {
  const tenant = useTenant();
  const previous = useRef(tenant);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (previous.current === tenant) return;
    previous.current = tenant;
    setVisible(true);
    const id = window.setTimeout(() => setVisible(false), 2400);
    return () => window.clearTimeout(id);
  }, [tenant]);

  if (!visible) return null;
  const option = tenantOption(tenant);

  return (
    <div className="tenant-toast" role="status" aria-live="polite">
      <span className="tenant-toast-dot" style={{ background: option.color }} />
      <span>
        Client&nbsp;&rarr;&nbsp;<b>{option.label}</b>
      </span>
    </div>
  );
}
