import { Select, SelectOption } from "@nous-research/ui/ui/components/select";

import { TENANTS, setTenant, useTenant } from "@/lib/tenant";

/**
 * Sidebar tenant scope selector for the GBAutomation dashboard section.
 *
 * Switches which tenant's data the GB views show (default: smoke-client).
 * The role suffix ("· admin" / "· user") is label-only for now — see
 * lib/tenant for the store, persistence, and default.
 */
export function TenantSwitcher() {
  const tenant = useTenant();
  return (
    <div className="grid gap-1 px-5 pt-1 pb-2">
      <Select
        id="gbauto-tenant-filter"
        aria-label="Tenant scope"
        value={tenant}
        onValueChange={(v) => setTenant(v)}
      >
        {TENANTS.map((tn) => (
          <SelectOption key={tn.slug} value={tn.slug}>
            {`${tn.label} · ${tn.role}`}
          </SelectOption>
        ))}
      </Select>
    </div>
  );
}
