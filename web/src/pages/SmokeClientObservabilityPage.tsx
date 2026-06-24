import { useEffect, useMemo } from "react";
import { HERMES_BASE_PATH } from "@/lib/api";

export default function SmokeClientObservabilityPage() {
  useEffect(() => {
    document.title = "Hermes Observability | GBAutomation";
  }, []);

  const iframeSrc = useMemo(() => {
    const token = window.__HERMES_SESSION_TOKEN__ ?? "";
    const query = new URLSearchParams();
    if (token) query.set("token", token);
    return `${HERMES_BASE_PATH}/hermes-observability/index.html?${query.toString()}#view=swimlane&pool=smoke-client&auto_add=1`;
  }, []);

  return (
    <iframe
      className="hermes-observability-frame"
      src={iframeSrc}
      title="Hermes Observability"
    />
  );
}
