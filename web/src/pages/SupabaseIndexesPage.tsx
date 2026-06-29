import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  GitBranch,
  RadioTower,
  Search,
  Table2,
  Workflow,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import {
  contractStats,
  useContractMap,
  useSupabaseContractsIndex,
} from "@/lib/gbautoSupabaseContracts";
import { api } from "@/lib/api";
import type { AgentProfileCatalogResponse } from "@/lib/api";
import { useTenant } from "@/lib/tenant";
import type {
  SupabaseContractRow,
  SupabaseContractsIndex,
} from "@/lib/gbautoSupabaseContracts";

type DashboardKey = "supabase" | "langfuse" | "kanban";

interface SupabaseSnapshot {
  dashboards: Record<string, { title: string; subtitle: string }>;
  generated_at: string;
  live: boolean;
  row_limit: number;
  tables: SupabaseTable[];
  window_days: number;
}

interface SupabaseTable {
  dashboard: string;
  description: string;
  error?: string | null;
  group_col?: string | null;
  label: string;
  name: string;
  rows: Record<string, unknown>[];
  status_col?: string | null;
  summary: {
    latest?: string | null;
    row_count: number;
    status_counts?: Record<string, number>;
    top_groups?: Record<string, number>;
  };
  time_col?: string | null;
}

const DASHBOARD_ROUTES = [
  { icon: Database, label: "Supabase", to: "/supabase" },
  { icon: RadioTower, label: "Langfuse", to: "/langfuse" },
  { icon: Workflow, label: "Kanban", to: "/kanban-data" },
];

const DASHBOARD_ORDER = ["ops", "kanban", "observability", "tac", "ecom"];

const PRIMARY_COLUMNS: Record<string, string[]> = {
  agent_runs: ["status", "title", "board_slug", "profile", "assignee", "client_slug", "repo_slug", "source_updated_at"],
  agent_log_artifacts: ["agent", "category", "client_slug", "repo_slug", "modified_at", "content_mode", "basename"],
  browser_control_runs: ["skill", "status", "started_at", "url", "error_message"],
  cron_run_outputs: ["status", "issue_id", "branch", "turns", "duration_s", "agent_summary"],
  cron_runs: ["cron_name", "started_at", "picked_count", "ok_count", "fail_count", "host"],
  kanban_tasks: ["title", "status", "kind", "client_slug", "created_at"],
  langfuse_traces: ["trace_name", "agent", "profile", "runtime", "trace_timestamp", "latency_sec", "total_cost", "total_tokens"],
  prd_kanban_dispatch_links: ["prd_id", "status", "relationship", "team_id", "profile", "task_id", "run_id", "created_at"],
  skill_runs: ["skill_name", "status", "started_at", "elapsed_ms", "category", "invoked_by"],
  tac_component_retrievals: ["retrieval_created_at", "query_count", "components_scanned", "match_count", "returned_count", "source"],
  tac_test_runs: ["suite", "status", "last_run_at", "tests_total", "tests_passed", "tests_failed"],
};

function formatDate(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function formatLabel(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatNumber(value: number) {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
}

function formatValue(value: unknown, key = "") {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object") return JSON.stringify(value);
  const text = String(value);
  if (key.includes("_at") || key.includes("timestamp")) return formatDate(text);
  return text.length > 120 ? `${text.slice(0, 117)}...` : text;
}

function statusTone(value: unknown) {
  const status = String(value ?? "").toLowerCase();
  if (["ok", "pass", "passed", "done", "completed", "active", "true", "success"].includes(status)) return "good";
  if (["fail", "failed", "error", "blocked", "timeout", "false", "cancelled"].includes(status)) return "bad";
  if (["running", "pending", "proposed", "partial", "skipped", "draft"].includes(status)) return "warn";
  return "";
}

function columnsFor(table: SupabaseTable, rows: Record<string, unknown>[]) {
  const row = rows[0];
  if (!row) return [];
  const keys = Object.keys(row);
  const preferred = (PRIMARY_COLUMNS[table.name] ?? []).filter((key) => keys.includes(key));
  return [...preferred, ...keys.filter((key) => !preferred.includes(key))].slice(0, 8);
}

function tableMatches(table: SupabaseTable, query: string) {
  if (!query) return true;
  const haystack = `${table.name} ${table.label} ${table.description} ${JSON.stringify(table.rows.slice(0, 20))}`.toLowerCase();
  return haystack.includes(query.toLowerCase());
}

function rowsFor(table: SupabaseTable, query: string) {
  const rows = query
    ? table.rows.filter((row) => JSON.stringify(row).toLowerCase().includes(query.toLowerCase()))
    : table.rows;
  return rows.slice(0, 10);
}

function getTable(snapshot: SupabaseSnapshot | null, name: string) {
  return snapshot?.tables.find((table) => table.name === name) ?? null;
}

// --- Tenant scoping -------------------------------------------------------
// Tables whose rows carry an explicit tenant/client marker are client-scoped
// and get filtered to the active tenant. This includes durable skill rows
// that use `invoked_by = tenant:<slug>` instead of `client_slug`.
function rowMatchesTenant(row: Record<string, unknown>, tenant: string) {
  const expected = tenant.toLowerCase();
  if (Object.prototype.hasOwnProperty.call(row, "client_slug")) {
    return String(row.client_slug ?? "").toLowerCase() === expected;
  }
  if (Object.prototype.hasOwnProperty.call(row, "tenant")) {
    return String(row.tenant ?? "").toLowerCase() === expected;
  }
  if (Object.prototype.hasOwnProperty.call(row, "invoked_by")) {
    return String(row.invoked_by ?? "").toLowerCase() === `tenant:${expected}`;
  }
  return true;
}

function isTenantScoped(rows: Record<string, unknown>[]) {
  const row = rows[0];
  return !!row && ["client_slug", "tenant", "invoked_by"].some((key) => Object.prototype.hasOwnProperty.call(row, key));
}

function countBy(rows: Record<string, unknown>[], col: string) {
  const counts: Record<string, number> = {};
  for (const row of rows) {
    const key = String(row[col] ?? "unknown");
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return counts;
}

function recomputeSummary(
  table: SupabaseTable,
  rows: Record<string, unknown>[],
): SupabaseTable["summary"] {
  const summary: SupabaseTable["summary"] = { ...table.summary, row_count: rows.length };
  if (table.time_col) {
    summary.latest =
      rows
        .map((row) => row[table.time_col as string])
        .filter(Boolean)
        .map((value) => String(value))
        .sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0] ?? null;
  }
  if (table.status_col) summary.status_counts = countBy(rows, table.status_col);
  if (table.group_col) summary.top_groups = countBy(rows, table.group_col);
  return summary;
}

function scopeTableToTenant(table: SupabaseTable, tenant: string): SupabaseTable {
  if (!isTenantScoped(table.rows)) return table;
  const rows = table.rows.filter((row) => rowMatchesTenant(row, tenant));
  return { ...table, rows, summary: recomputeSummary(table, rows) };
}

function scopeSnapshotToTenant(
  snapshot: SupabaseSnapshot | null,
  tenant: string,
): SupabaseSnapshot | null {
  if (!snapshot) return snapshot;
  return { ...snapshot, tables: snapshot.tables.map((table) => scopeTableToTenant(table, tenant)) };
}

function sumRows(tables: SupabaseTable[]) {
  return tables.reduce((total, table) => total + table.rows.length, 0);
}

function latestDate(tables: SupabaseTable[]) {
  return tables
    .map((table) => table.summary.latest)
    .filter(Boolean)
    .sort((a, b) => new Date(String(b)).getTime() - new Date(String(a)).getTime())[0];
}

function StatCard({
  icon: Icon,
  label,
  sub,
  value,
}: {
  icon: typeof Database;
  label: string;
  sub?: string;
  value: string | number;
}) {
  return (
    <article className="supabase-stat-card">
      <Icon className="h-4 w-4" />
      <strong>{value}</strong>
      <span>{label}</span>
      {sub ? <small>{sub}</small> : null}
    </article>
  );
}

function ValuePills({ values }: { values: Record<string, number> | undefined }) {
  const entries = Object.entries(values ?? {}).slice(0, 5);
  if (!entries.length) return <span className="supabase-muted">No grouped values</span>;
  return (
    <div className="supabase-pill-row">
      {entries.map(([key, value]) => (
        <span className={`supabase-chip ${statusTone(key)}`} key={key}>
          {formatLabel(key)} <b>{value}</b>
        </span>
      ))}
    </div>
  );
}

function ContractContext({ contract }: { contract?: SupabaseContractRow }) {
  if (!contract) {
    return (
      <div className="supabase-contract-card is-empty">
        <span>Contract</span>
        <strong>Not cataloged yet</strong>
        <p>Add this object to <code>ops_schema_catalog</code> to expose owner, access, read path, and retention metadata.</p>
      </div>
    );
  }

  return (
    <div className="supabase-contract-card">
      <div className="supabase-contract-topline">
        <span>{contract.object_type ?? "object"}</span>
        <span>{contract.lifecycle ?? "lifecycle tbd"}</span>
      </div>
      <div className="supabase-contract-grid">
        <span><b>Domain</b>{contract.domain ?? "-"}</span>
        <span><b>Owner</b>{contract.owner_agent ?? "-"}</span>
        <span><b>Access</b>{formatLabel(contract.access_model ?? "-")}</span>
        <span><b>Schema</b>v{contract.schema_version ?? 1}</span>
      </div>
      <dl className="supabase-contract-paths">
        <div>
          <dt>Write path</dt>
          <dd>{contract.write_path ?? "-"}</dd>
        </div>
        <div>
          <dt>Read path</dt>
          <dd>{contract.read_path ?? "-"}</dd>
        </div>
        <div>
          <dt>Retention</dt>
          <dd>{contract.retention_policy ?? "-"}</dd>
        </div>
      </dl>
      {contract.notes ? <p className="supabase-contract-note">{contract.notes}</p> : null}
    </div>
  );
}

function DataTable({ query, table }: { query: string; table: SupabaseTable }) {
  const rows = rowsFor(table, query);
  const columns = columnsFor(table, rows);

  if (!rows.length) {
    return (
      <div className="supabase-empty">
        {table.error ? `Unavailable: ${table.error}` : "No matching rows in this snapshot."}
      </div>
    );
  }

  return (
    <div className="supabase-table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{formatLabel(column)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${table.name}:${rowIndex}`}>
              {columns.map((column) => {
                const value = row[column];
                const isStatus = column.includes("status") || column === "active" || column === "content_mode";
                return (
                  <td key={column}>
                    {isStatus ? (
                      <span className={`supabase-chip ${statusTone(value)}`}>{formatValue(value, column)}</span>
                    ) : (
                      formatValue(value, column)
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TableCard({
  contract,
  query,
  table,
}: {
  contract?: SupabaseContractRow;
  query: string;
  table: SupabaseTable;
}) {
  return (
    <article className="supabase-table-card">
      <header>
        <div>
          <p className="gbhub-eyebrow">{table.dashboard}</p>
          <h3>{table.label}</h3>
          <p>{table.description}</p>
        </div>
        <Badge tone={table.error ? "destructive" : "outline"}>{table.rows.length}</Badge>
      </header>
      <div className="supabase-card-metrics">
        <span><b>{table.summary.row_count}</b> rows</span>
        <span><b>{formatDate(table.summary.latest)}</b> latest</span>
      </div>
      <ContractContext contract={contract} />
      <ValuePills values={table.summary.status_counts ?? table.summary.top_groups} />
      <DataTable query={query} table={table} />
    </article>
  );
}

function ContractSummary({ index }: { index: SupabaseContractsIndex | null }) {
  const stats = contractStats(index);

  return (
    <section className="supabase-contract-summary">
      <div>
        <p className="gbhub-eyebrow">Supabase Index Contract</p>
        <h3>Cataloged data contracts</h3>
        <p>
          Sanitized readback from <code>ops_schema_catalog</code>, <code>ops_skills_registry</code>,
          and the public object catalog. No database keys or service-role data are shipped to the browser.
        </p>
      </div>
      <div className="supabase-contract-summary-grid">
        <span><b>{stats.contracts || "-"}</b> contracts</span>
        <span><b>{stats.domains || "-"}</b> domains</span>
        <span><b>{stats.owners || "-"}</b> owners</span>
        <span><b>{stats.anonReadable || "-"}</b> anon views</span>
        <span><b>{stats.registrySkills || "-"}</b> skill rows</span>
        <span><b>{stats.tableObjects || "-"}</b> public objects</span>
      </div>
    </section>
  );
}

function PageShell({
  children,
  description,
  eyebrow,
  icon: Icon,
  snapshot,
  tenant,
  title,
}: {
  children: React.ReactNode;
  description: string;
  eyebrow: string;
  icon: typeof Database;
  snapshot: SupabaseSnapshot | null;
  tenant: string;
  title: string;
}) {
  return (
    <div className="gbhub-page supabase-page normal-case">
      <section className="gbhub-hero supabase-hero">
        <div className="gbhub-brand-row">
          <span className="gbhub-mark"><Icon className="h-4 w-4" /></span>
          <span>{eyebrow}</span>
        </div>
        <Badge tone="outline" className="gbhub-badge">
          {snapshot?.live ? <CheckCircle2 className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
          {snapshot?.live ? "live snapshot" : "loading"}
        </Badge>
        <Badge tone="outline" className="gbhub-badge">scope: {tenant}</Badge>
        <h2>{title}</h2>
        <p>{description}</p>
        <nav className="supabase-route-tabs" aria-label="Supabase dashboard routes">
          {DASHBOARD_ROUTES.map((route) => {
            const RouteIcon = route.icon;
            return (
              <Link key={route.to} to={route.to}>
                <RouteIcon className="h-3.5 w-3.5" />
                {route.label}
              </Link>
            );
          })}
        </nav>
      </section>
      {children}
    </div>
  );
}

function useSupabaseSnapshot() {
  const [snapshot, setSnapshot] = useState<SupabaseSnapshot | null>(null);

  useEffect(() => {
    void fetch("/gbauto-supabase/snapshot.json")
      .then((response) => response.json())
      .then((data: SupabaseSnapshot) => setSnapshot(data))
      .catch(() => setSnapshot(null));
  }, []);

  return snapshot;
}

function useAgentProfileCatalog(tenant: string) {
  const [catalog, setCatalog] = useState<AgentProfileCatalogResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api
      .getAgentProfileCatalog(tenant)
      .then((data) => {
        if (!cancelled) setCatalog(data.ok ? data : null);
      })
      .catch(() => {
        if (!cancelled) setCatalog(null);
      });
    return () => {
      cancelled = true;
    };
  }, [tenant]);

  return catalog;
}

function SupabaseAllTables({
  contractsIndex,
  snapshot,
}: {
  contractsIndex: SupabaseContractsIndex | null;
  snapshot: SupabaseSnapshot | null;
}) {
  const [query, setQuery] = useState("");
  const [dashboard, setDashboard] = useState("all");
  const contractMap = useContractMap(contractsIndex);
  const tables = useMemo(() => {
    const base = snapshot?.tables ?? [];
    return base
      .filter((table) => dashboard === "all" || table.dashboard === dashboard)
      .filter((table) => tableMatches(table, query));
  }, [dashboard, query, snapshot]);
  const orderedDashboards = DASHBOARD_ORDER.filter((key) => snapshot?.dashboards[key]);

  return (
    <>
      <section className="supabase-stat-grid">
        <StatCard icon={Table2} label="Tables" sub={`${orderedDashboards.length} dashboard groups`} value={snapshot?.tables.length ?? "-"} />
        <StatCard icon={Database} label="Rows Loaded" sub={`cap ${snapshot?.row_limit ?? "-"} per table`} value={snapshot ? sumRows(snapshot.tables) : "-"} />
        <StatCard icon={AlertTriangle} label="Unavailable" sub="PostgREST or schema gap" value={snapshot ? snapshot.tables.filter((table) => table.error).length : "-"} />
        <StatCard icon={Activity} label="Latest Event" value={formatDate(latestDate(snapshot?.tables ?? []))} />
      </section>

      <ContractSummary index={contractsIndex} />

      <section className="supabase-toolbar">
        <div className="supabase-search">
          <Search className="h-4 w-4" />
          <input aria-label="Filter Supabase tables" onChange={(event) => setQuery(event.target.value)} placeholder="Filter tables or rows" value={query} />
        </div>
        <div className="supabase-filter-pills" role="tablist">
          {["all", ...orderedDashboards].map((key) => (
            <button className={dashboard === key ? "is-active" : ""} key={key} onClick={() => setDashboard(key)} type="button">
              {key === "all" ? "All" : (snapshot?.dashboards[key]?.title ?? key)}
            </button>
          ))}
        </div>
      </section>

      <section className="supabase-table-grid">
        {tables.map((table) => (
          <TableCard
            contract={contractMap.get(table.name)}
            key={table.name}
            query={query}
            table={table}
          />
        ))}
      </section>
    </>
  );
}

function LangfuseFocused({
  contractsIndex,
  snapshot,
}: {
  contractsIndex: SupabaseContractsIndex | null;
  snapshot: SupabaseSnapshot | null;
}) {
  const [query, setQuery] = useState("");
  const contractMap = useContractMap(contractsIndex);
  const traces = getTable(snapshot, "langfuse_traces");
  const artifacts = getTable(snapshot, "agent_log_artifacts");
  const traceRows = traces?.rows ?? [];
  const totalCost = traceRows.reduce((sum, row) => sum + Number(row.total_cost ?? 0), 0);
  const totalTokens = traceRows.reduce((sum, row) => sum + Number(row.total_tokens ?? 0), 0);
  const agents = new Set(traceRows.map((row) => row.agent).filter(Boolean)).size;

  return (
    <>
      <section className="supabase-stat-grid">
        <StatCard icon={RadioTower} label="Mirrored Traces" sub={`${snapshot?.window_days ?? "-"} day window`} value={traceRows.length} />
        <StatCard icon={Activity} label="Agents" sub="distinct trace agents" value={agents} />
        <StatCard icon={Database} label="Tokens" sub="input + output" value={formatNumber(totalTokens)} />
        <StatCard icon={GitBranch} label="Cost" sub="mirrored trace total" value={`$${totalCost.toFixed(4)}`} />
      </section>
      <section className="supabase-toolbar">
        <div className="supabase-search">
          <Search className="h-4 w-4" />
          <input aria-label="Filter Langfuse traces" onChange={(event) => setQuery(event.target.value)} placeholder="Filter trace names, agents, tags" value={query} />
        </div>
      </section>
      <section className="supabase-table-grid focused">
        {traces ? <TableCard contract={contractMap.get(traces.name)} query={query} table={traces} /> : null}
        {artifacts ? <TableCard contract={contractMap.get(artifacts.name)} query={query} table={artifacts} /> : null}
      </section>
    </>
  );
}

function KanbanFocused({
  contractsIndex,
  snapshot,
}: {
  contractsIndex: SupabaseContractsIndex | null;
  snapshot: SupabaseSnapshot | null;
}) {
  const [query, setQuery] = useState("");
  const tenant = useTenant();
  const contractMap = useContractMap(contractsIndex);
  const agentProfileCatalog = useAgentProfileCatalog(tenant);
  const agentRuns = getTable(snapshot, "agent_runs");
  const dispatchLinks = getTable(snapshot, "prd_kanban_dispatch_links");
  const kanbanTasks = getTable(snapshot, "kanban_tasks");
  const profileTeams = getTable(snapshot, "agent_profile_teams");
  const agentProfiles = getTable(snapshot, "agent_profiles");
  const agentRows = agentRuns?.rows ?? [];
  const boards = new Set(agentRows.map((row) => row.board_slug).filter(Boolean)).size;

  return (
    <>
      <section className="supabase-stat-grid">
        <StatCard icon={Workflow} label="Agent Runs" sub="Hermes board mirror" value={agentRows.length} />
        <StatCard icon={GitBranch} label="Boards" sub="distinct board slugs" value={boards} />
        <StatCard icon={Table2} label="Dispatch Links" sub="PRD to task/run" value={dispatchLinks?.rows.length ?? 0} />
        <StatCard icon={Database} label="Profile Teams" sub="Supabase agent_profile_teams" value={profileTeams?.rows.length || agentProfileCatalog?.teams.length || "-"} />
      </section>
      <section className="supabase-toolbar">
        <div className="supabase-search">
          <Search className="h-4 w-4" />
          <input aria-label="Filter Kanban rows" onChange={(event) => setQuery(event.target.value)} placeholder="Filter cards, profiles, board, status" value={query} />
        </div>
      </section>
      {!profileTeams && agentProfileCatalog?.teams.length ? (
        <section className="supabase-note">
          <CheckCircle2 className="h-4 w-4" />
          <span>Profile teams are loaded live from Supabase agent_profile_teams for {tenant}; the bundled snapshot is stale.</span>
        </section>
      ) : null}
      {agentProfileCatalog?.teams.length ? (
        <section className="supabase-team-strip">
          {agentProfileCatalog.teams.slice(0, 6).map((team) => (
            <article key={team.team_id}>
              <strong>{team.display_name ?? team.team_id}</strong>
              <span>{team.orchestrator_profile ?? "no orchestrator"} · {team.specialist_profiles?.length ?? 0} specialists</span>
            </article>
          ))}
        </section>
      ) : null}
      <section className="supabase-table-grid focused">
        {agentRuns ? <TableCard contract={contractMap.get(agentRuns.name)} query={query} table={agentRuns} /> : null}
        {dispatchLinks ? <TableCard contract={contractMap.get(dispatchLinks.name)} query={query} table={dispatchLinks} /> : null}
        {kanbanTasks ? <TableCard contract={contractMap.get(kanbanTasks.name)} query={query} table={kanbanTasks} /> : null}
        {agentProfiles ? <TableCard contract={contractMap.get(agentProfiles.name)} query={query} table={agentProfiles} /> : null}
        {profileTeams ? <TableCard contract={contractMap.get(profileTeams.name)} query={query} table={profileTeams} /> : null}
      </section>
    </>
  );
}

function SupabaseIndexesPage({ view }: { view: DashboardKey }) {
  const rawSnapshot = useSupabaseSnapshot();
  const tenant = useTenant();
  const snapshot = useMemo(
    () => scopeSnapshotToTenant(rawSnapshot, tenant),
    [rawSnapshot, tenant],
  );
  const contractsIndex = useSupabaseContractsIndex();

  useEffect(() => {
    const title = view === "langfuse" ? "Langfuse" : view === "kanban" ? "Kanban" : "Supabase";
    globalThis.document.title = `${title} | GBAutomation`;
  }, [view]);

  if (view === "langfuse") {
    return (
      <PageShell
        description="Sanitized Langfuse trace metadata mirrored into Supabase, with cost, token, agent, and evidence rollups for review."
        eyebrow="GBAutomation Observability"
        icon={RadioTower}
        snapshot={snapshot}
        tenant={tenant}
        title="Langfuse Trace Index"
      >
        <LangfuseFocused contractsIndex={contractsIndex} snapshot={snapshot} />
      </PageShell>
    );
  }

  if (view === "kanban") {
    return (
      <PageShell
        description="Hermes Kanban runs, PRD dispatch links, and profile-team routing data, rendered from the same Supabase index snapshot."
        eyebrow="GBAutomation Control Plane"
        icon={Workflow}
        snapshot={snapshot}
        tenant={tenant}
        title="Kanban Data Plane"
      >
        <KanbanFocused contractsIndex={contractsIndex} snapshot={snapshot} />
      </PageShell>
    );
  }

  return (
    <PageShell
      description="A live static snapshot of the GBAutomation Supabase indexes: operations, Kanban, observability, TAC quality, and ecom intelligence."
      eyebrow="GBAutomation Data"
      icon={Database}
      snapshot={snapshot}
      tenant={tenant}
      title="Supabase Indexes"
    >
      <SupabaseAllTables contractsIndex={contractsIndex} snapshot={snapshot} />
    </PageShell>
  );
}

export function SupabasePage() {
  return <SupabaseIndexesPage view="supabase" />;
}

export function LangfusePage() {
  return <SupabaseIndexesPage view="langfuse" />;
}

export function KanbanPage() {
  return <SupabaseIndexesPage view="kanban" />;
}
