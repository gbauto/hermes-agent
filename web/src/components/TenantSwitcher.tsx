import { Select, SelectOption } from "@nous-research/ui/ui/components/select";

import { TENANTS, setTenant, tenantOption, useTenant } from "@/lib/tenant";

/**
 * Sidebar tenant scope selector for the GBAutomation dashboard section.
 *
 * Switches which tenant's data the GB views show (default: smoke-client).
 * A persistent colored chip shows the active client on every page; the
 * role suffix ("· admin" / "· user") is label-only for now — see
 * lib/tenant for the store, persistence, default, and colors.
 */
export function TenantSwitcher() {
  const tenant = useTenant();
  const active = tenantOption(tenant);
  return (
    <div className="grid gap-1 px-5 pt-1 pb-2">
      <div className="gb-tenant-active" title={`Active client: ${active.label}`}>
        <span className="gb-tenant-dot" style={{ background: active.color }} />
        <span className="gb-tenant-name">{active.label}</span>
        <span className="gb-tenant-role">{active.role}</span>
      </div>
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
