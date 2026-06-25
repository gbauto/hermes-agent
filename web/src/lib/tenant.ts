import { useSyncExternalStore } from "react";

/**
 * GBAutomation tenant scope for the dashboard's GB data views.
 *
 * A "tenant" here mirrors the Supabase row-scoping the client portal uses
 * (e.g. tenant=smoke-client). The selected tenant is a global, persisted
 * choice surfaced by the sidebar TenantSwitcher; GB data pages read it via
 * `useTenant()` to filter their queries.
 *
 * `role` is informational/label-only for now — there is no permission
 * gating yet (jid5274 shows "admin", ecom shows "user", etc.).
 */
export type TenantRole = "admin" | "user";

export interface TenantOption {
  slug: string;
  label: string;
  role: TenantRole;
  /** Accent used by the active-client indicator + change toast. */
  color: string;
}

export const TENANTS: TenantOption[] = [
  { slug: "smoke-client", label: "smoke-client", role: "user", color: "#8c8a84" },
  { slug: "gbautomation", label: "gbautomation", role: "admin", color: "#d97757" },
  { slug: "jid5274", label: "jid5274", role: "admin", color: "#3d6ea8" },
  { slug: "ecom", label: "ecom", role: "user", color: "#4f9d69" },
];

export const DEFAULT_TENANT = "smoke-client";

const STORAGE_KEY = "hermes.dashboard.tenant";

function isKnown(slug: string | null | undefined): slug is string {
  return !!slug && TENANTS.some((tn) => tn.slug === slug);
}

function read(): string {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (isKnown(v)) return v;
  } catch {
    /* localStorage unavailable (SSR / privacy mode) — fall through */
  }
  return DEFAULT_TENANT;
}

const listeners = new Set<() => void>();
let current = read();

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

function getSnapshot(): string {
  return current;
}

/** Set the active tenant, persist it, and notify subscribers. */
export function setTenant(slug: string): void {
  if (!isKnown(slug) || slug === current) return;
  current = slug;
  try {
    localStorage.setItem(STORAGE_KEY, slug);
  } catch {
    /* persistence is best-effort */
  }
  listeners.forEach((cb) => cb());
}

/** Subscribe a component to the active tenant slug. */
export function useTenant(): string {
  return useSyncExternalStore(subscribe, getSnapshot, () => DEFAULT_TENANT);
}

/** Resolve a slug to its full option (falls back to the first tenant). */
export function tenantOption(slug: string): TenantOption {
  return TENANTS.find((tn) => tn.slug === slug) ?? TENANTS[0];
}
