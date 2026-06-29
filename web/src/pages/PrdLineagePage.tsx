import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  Filter,
  GitCommit,
  GitPullRequest,
  Milestone,
  Search,
  Tags,
  Workflow,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { tenantOption, useTenant } from "@/lib/tenant";

interface LineageManifest {
  counts: {
    commits: number;
    kanbanLinks: number;
    linkedToCommit: number;
    linkedToKanban: number;
    linkedToPr: number;
    linkedToSprint: number;
    prds: number;
    prs: number;
    sprintLinks: number;
  };
  coverage: {
    commitPct: number;
    kanbanPct: number;
    prPct: number;
    sprintPct: number;
  };
  clients: Array<{ client: string; count: number }>;
  generatedAt: string;
  prds: PrdRecord[];
  sprintGoals: SprintGoal[];
  tags: Array<{ tag: string; count: number }>;
  timeline: TimelineEvent[];
  warnings: string[];
}

interface PrdRecord {
  agentTeam?: string | null;
  branch?: string | null;
  client: string;
  commits: CommitLink[];
  components: string[];
  hermesProfiles: string[];
  htmlPreviewUrl?: string | null;
  indexedAt?: string | null;
  kanbanLinks: KanbanLink[];
  lineage: {
    coveragePct: number;
    hasCommit: boolean;
    hasKanban: boolean;
    hasPr: boolean;
    hasSprint: boolean;
    latestAt?: string | null;
  };
  path?: string | null;
  priority: string;
  prdId: string;
  prs: PrLink[];
  schemaContractOk: boolean;
  sprintLinks: SprintLink[];
  status: string;
  tags: string[];
  title: string;
}

interface SprintGoal {
  commitCount: number;
  kanbanCount: number;
  latestAt?: string | null;
  prCount: number;
  prdCount: number;
  prds: Array<{
    coveragePct: number;
    prdId: string;
    relationship?: string | null;
    status: string;
    title: string;
  }>;
  rockId?: string | null;
  sprintId?: string | null;
  tags: string[];
}

interface SprintLink {
  relationship?: string | null;
  rockId?: string | null;
  sprintId?: string | null;
  status?: string | null;
  updatedAt?: string | null;
}

interface KanbanLink {
  dispatchSource?: string | null;
  parentTaskId?: string | null;
  profile?: string | null;
  relationship?: string | null;
  runId?: string | null;
  status?: string | null;
  taskId?: string | null;
  teamId?: string | null;
  updatedAt?: string | null;
}

interface CommitLink {
  branch?: string | null;
  committedAt?: string | null;
  message?: string | null;
  repo?: string | null;
  sha?: string | null;
  shortSha?: string | null;
}

interface PrLink {
  id?: string | null;
  indexedAt?: string | null;
  number?: number | null;
  repository?: string | null;
  status?: string | null;
  title?: string | null;
  url?: string | null;
}

interface TimelineEvent {
  at?: string | null;
  detail?: string | null;
  prdId: string;
  title?: string | null;
  type: "commit" | "kanban" | "pr" | "prd";
  url?: string | null;
}

type StageFilter = "all" | "missing-sprint" | "missing-kanban" | "missing-commit" | "missing-pr";

interface StageDatum {
  count: number;
  filter: StageFilter;
  label: string;
  missing: number;
  pct: number;
  sub: string;
}

const TENANT_CLIENT_ALIASES: Record<string, string[]> = {
  ecom: ["ecom", "the-mall", "mall-client"],
  gbautomation: ["gbautomation", "gbauto", "gb-automation", "unknown"],
  jid5274: ["jid5274", "jason-diaz"],
  "smoke-client": ["smoke-client"],
};

function matchesTenantClient(client: string, tenant: string) {
  const normalized = client.trim().toLowerCase();
  return (TENANT_CLIENT_ALIASES[tenant] ?? [tenant]).includes(normalized);
}

function formatDate(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" }).format(date);
}

function formatNumber(value: number) {
  return new Intl.NumberFormat(undefined).format(value);
}

function statusTone(value?: string | null) {
  const status = String(value ?? "").toLowerCase();
  if (["done", "completed", "merged", "active", "linked", "success"].includes(status)) return "good";
  if (["blocked", "failed", "error", "missing"].includes(status)) return "bad";
  if (["draft", "pending", "proposed", "partial"].includes(status)) return "warn";
  return "";
}

function compactPath(value?: string | null) {
  if (!value) return "No path captured";
  const parts = value.split(/[\\/]/).filter(Boolean);
  return parts.length > 3 ? parts.slice(-3).join("/") : value;
}

function labelText(value: unknown) {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const preferred = record.id ?? record.name ?? record.slug ?? record.title;
  return typeof preferred === "string" ? preferred : null;
}

function cleanLabels(values: unknown[]) {
  return values
    .map(labelText)
    .filter((value): value is string => Boolean(value))
    .filter((value, index, all) => all.indexOf(value) === index);
}

function sourceGroupsFor(prd: PrdRecord) {
  const groups = new Set<string>();
  for (const commit of prd.commits) {
    if (commit.repo) groups.add(commit.repo);
  }
  for (const pr of prd.prs) {
    if (pr.repository) groups.add(pr.repository);
  }
  if (prd.path) {
    const first = prd.path.split(/[\\/]/).filter(Boolean)[0];
    if (first) groups.add(first);
  }
  return Array.from(groups);
}

function stageMatches(prd: PrdRecord, filter: StageFilter) {
  if (filter === "missing-sprint") return !prd.lineage.hasSprint;
  if (filter === "missing-kanban") return !prd.lineage.hasKanban;
  if (filter === "missing-commit") return !prd.lineage.hasCommit;
  if (filter === "missing-pr") return !prd.lineage.hasPr;
  return true;
}

function searchablePrd(prd: PrdRecord) {
  return [
    prd.prdId,
    prd.title,
    prd.client,
    prd.status,
    prd.priority,
    prd.path,
    cleanLabels(prd.tags).join(" "),
    cleanLabels(prd.components).join(" "),
    cleanLabels(prd.hermesProfiles).join(" "),
    sourceGroupsFor(prd).join(" "),
    prd.sprintLinks.map((link) => `${link.sprintId} ${link.rockId}`).join(" "),
    prd.kanbanLinks.map((link) => `${link.taskId} ${link.teamId} ${link.profile}`).join(" "),
    prd.commits.map((commit) => `${commit.repo} ${commit.message} ${commit.shortSha}`).join(" "),
    prd.prs.map((pr) => `${pr.repository} ${pr.title} ${pr.number}`).join(" "),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function countPercent(count: number, total: number) {
  return total ? Math.round((count / total) * 100) : 0;
}

function hasHtmlPreview(prd: PrdRecord) {
  return Boolean(prd.htmlPreviewUrl);
}

function stageData(prds: PrdRecord[]): StageDatum[] {
  const total = prds.length;
  const html = prds.filter(hasHtmlPreview).length;
  const sprint = prds.filter((prd) => prd.lineage.hasSprint).length;
  const board = prds.filter((prd) => prd.lineage.hasKanban).length;
  const commit = prds.filter((prd) => prd.lineage.hasCommit).length;
  const pr = prds.filter((prd) => prd.lineage.hasPr).length;

  return [
    {
      count: total,
      filter: "all",
      label: "Specs",
      missing: 0,
      pct: 100,
      sub: "indexed work objects",
    },
    {
      count: html,
      filter: "all",
      label: "HTML",
      missing: total - html,
      pct: countPercent(html, total),
      sub: "preview links",
    },
    {
      count: sprint,
      filter: "missing-sprint",
      label: "Sprint",
      missing: total - sprint,
      pct: countPercent(sprint, total),
      sub: "goal links",
    },
    {
      count: board,
      filter: "missing-kanban",
      label: "Board",
      missing: total - board,
      pct: countPercent(board, total),
      sub: "Kanban cards",
    },
    {
      count: commit,
      filter: "missing-commit",
      label: "Commit",
      missing: total - commit,
      pct: countPercent(commit, total),
      sub: "git evidence",
    },
    {
      count: pr,
      filter: "missing-pr",
      label: "PR",
      missing: total - pr,
      pct: countPercent(pr, total),
      sub: "review receipts",
    },
  ];
}

function StatCard({
  icon: Icon,
  label,
  sub,
  value,
}: {
  icon: typeof Milestone;
  label: string;
  sub: string;
  value: string | number;
}) {
  return (
    <article className="supabase-stat-card">
      <Icon className="h-4 w-4" />
      <strong>{value}</strong>
      <span>{label}</span>
      <small>{sub}</small>
    </article>
  );
}

function CoveragePills({ prd }: { prd: PrdRecord }) {
  const steps = [
    ["Sprint", prd.lineage.hasSprint],
    ["Kanban", prd.lineage.hasKanban],
    ["Commit", prd.lineage.hasCommit],
    ["PR", prd.lineage.hasPr],
  ] as const;

  return (
    <div className="supabase-pill-row lineage-pill-row">
      {steps.map(([label, ok]) => (
        <span className={`supabase-chip ${ok ? "good" : "bad"}`} key={label}>
          {ok ? <CheckCircle2 className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
          {label}
        </span>
      ))}
    </div>
  );
}

function DeliveryFunnel({
  active,
  onChange,
  prds,
}: {
  active: StageFilter;
  onChange: (filter: StageFilter) => void;
  prds: PrdRecord[];
}) {
  const stages = stageData(prds);
  const svgWidth = 1080;
  const rowHeight = 62;
  const topY = 18;
  const maxBar = 820;
  const centerX = 560;
  const minBar = 26;

  return (
    <section className="lineage-visual-panel" aria-label="Delivery funnel diagram">
      <div className="lineage-visual-heading">
        <div>
          <p className="gbhub-eyebrow">Visual Funnel</p>
          <h3>Spec evidence narrowing by stage</h3>
        </div>
        <span>{formatNumber(stages[0]?.count ?? 0)} scoped specs</span>
      </div>
      <svg className="lineage-funnel-svg" viewBox={`0 0 ${svgWidth} 420`} role="img" aria-label="Tapered delivery funnel">
        {stages.map((stage, index) => {
          const y = topY + index * rowHeight;
          const width = Math.max(minBar, Math.round((stage.pct / 100) * maxBar));
          const left = centerX - width / 2;
          const right = centerX + width / 2;
          const nextWidth = Math.max(
            minBar,
            Math.round(((stages[index + 1]?.pct ?? stage.pct) / 100) * maxBar),
          );
          const nextLeft = centerX - nextWidth / 2;
          const nextRight = centerX + nextWidth / 2;
          const isActive = active === stage.filter;
          const canFilter = stage.filter !== "all" || index === 0;
          const missingLabel = stage.missing ? `${formatNumber(stage.missing)} missing` : "complete";
          return (
            <g
              className={`lineage-funnel-band ${isActive ? "is-active" : ""} ${canFilter ? "is-clickable" : ""}`}
              key={stage.label}
              onClick={() => onChange(stage.filter)}
              role="button"
              tabIndex={0}
            >
              <polygon points={`${left},${y} ${right},${y} ${nextRight},${y + 42} ${nextLeft},${y + 42}`} />
              <text className="lineage-funnel-label" x="90" y={y + 18}>{stage.label}</text>
              <text className="lineage-funnel-count" x={centerX} y={y + 28}>{formatNumber(stage.count)}</text>
              <text className="lineage-funnel-sub" x="1000" y={y + 18} textAnchor="end">{stage.pct}% linked</text>
              <text className="lineage-funnel-sub" x="1000" y={y + 34} textAnchor="end">{missingLabel}</text>
            </g>
          );
        })}
      </svg>
    </section>
  );
}

function EvidenceFlow({ prds }: { prds: PrdRecord[] }) {
  const stages = stageData(prds);
  const total = stages[0]?.count ?? 0;
  const rows = stages.slice(1);
  return (
    <section className="lineage-flow-panel" aria-label="Evidence flow">
      <div className="lineage-visual-heading">
        <div>
          <p className="gbhub-eyebrow">Evidence Flow</p>
          <h3>Where scoped specs fall out</h3>
        </div>
        <span>{formatNumber(total)} inputs</span>
      </div>
      <svg className="lineage-flow-svg" viewBox="0 0 1080 330" role="img" aria-label="Sankey-style evidence flow">
        <defs>
          <linearGradient id="lineageFlowGood" x1="0" x2="1">
            <stop offset="0%" stopColor="rgba(79,157,105,.16)" />
            <stop offset="100%" stopColor="rgba(79,157,105,.68)" />
          </linearGradient>
          <linearGradient id="lineageFlowGap" x1="0" x2="1">
            <stop offset="0%" stopColor="rgba(185,74,72,.10)" />
            <stop offset="100%" stopColor="rgba(217,119,87,.58)" />
          </linearGradient>
        </defs>
        <rect className="lineage-flow-source" x="32" y="98" width="155" height="108" rx="8" />
        <text className="lineage-flow-title" x="54" y="136">Specs</text>
        <text className="lineage-flow-number" x="54" y="176">{formatNumber(total)}</text>
        {rows.map((stage, index) => {
          const y = 32 + index * 54;
          const goodWidth = Math.max(2, stage.pct * 5.8);
          const gapWidth = Math.max(2, (100 - stage.pct) * 5.8);
          return (
            <g key={stage.label}>
              <path className="lineage-flow-line" d={`M 188 152 C 320 152, 310 ${y + 18}, 420 ${y + 18}`} />
              <rect className="lineage-flow-good" x="420" y={y} width={goodWidth} height="20" rx="10" />
              <rect className="lineage-flow-gap" x={420 + goodWidth + 8} y={y} width={gapWidth} height="20" rx="10" />
              <text className="lineage-flow-label" x="420" y={y + 42}>{stage.label}</text>
              <text className="lineage-flow-sub" x="540" y={y + 42}>{formatNumber(stage.count)} linked</text>
              <text className="lineage-flow-sub bad" x="675" y={y + 42}>{formatNumber(stage.missing)} missing</text>
            </g>
          );
        })}
      </svg>
    </section>
  );
}

function ArtifactRail({ prds }: { prds: PrdRecord[] }) {
  const stages = stageData(prds);
  return (
    <section className="lineage-rail-panel" aria-label="Evidence rail">
      <div className="lineage-visual-heading">
        <div>
          <p className="gbhub-eyebrow">Smallest Traceable Unit</p>
          <h3>Evidence atom rollup path</h3>
        </div>
        <span>code atom to PRD</span>
      </div>
      <div className="lineage-rail">
        {[
          { label: "Code atom", value: "line/hunk", state: "pending" },
          { label: "Commit", value: stages.find((stage) => stage.label === "Commit")?.count ?? 0, state: "good" },
          { label: "PR", value: stages.find((stage) => stage.label === "PR")?.count ?? 0, state: "bad" },
          { label: "HTML", value: stages.find((stage) => stage.label === "HTML")?.count ?? 0, state: "bad" },
          { label: "Board", value: stages.find((stage) => stage.label === "Board")?.count ?? 0, state: "bad" },
          { label: "Sprint", value: stages.find((stage) => stage.label === "Sprint")?.count ?? 0, state: "bad" },
        ].map((stage, index, all) => (
          <div className={`lineage-rail-node ${stage.state}`} key={stage.label}>
            <span>{stage.label}</span>
            <strong>{typeof stage.value === "number" ? formatNumber(stage.value) : stage.value}</strong>
            {index < all.length - 1 ? <i aria-hidden /> : null}
          </div>
        ))}
      </div>
    </section>
  );
}

function VisualStack({
  active,
  onChange,
  prds,
}: {
  active: StageFilter;
  onChange: (filter: StageFilter) => void;
  prds: PrdRecord[];
}) {
  return (
    <div className="lineage-visual-stack">
      <DeliveryFunnel active={active} onChange={onChange} prds={prds} />
      <div className="lineage-visual-grid">
        <EvidenceFlow prds={prds} />
        <ArtifactRail prds={prds} />
      </div>
    </div>
  );
}

function PrdCard({
  active,
  onSelect,
  prd,
}: {
  active: boolean;
  onSelect: () => void;
  prd: PrdRecord;
}) {
  return (
    <button className={`lineage-prd-card ${active ? "is-active" : ""}`} onClick={onSelect} type="button">
      <div className="lineage-prd-topline">
        <span>{prd.client}</span>
        <span>{prd.lineage.coveragePct}%</span>
      </div>
      <strong>{prd.title}</strong>
      <p>{compactPath(prd.path)}</p>
      <CoveragePills prd={prd} />
      <div className="lineage-prd-meta">
        <span className={`supabase-chip ${statusTone(prd.status)}`}>{prd.status}</span>
        <span>{formatDate(prd.lineage.latestAt ?? prd.indexedAt)}</span>
      </div>
    </button>
  );
}

function DetailPanel({ prd }: { prd: PrdRecord | null }) {
  if (!prd) {
    return (
      <aside className="lineage-detail-panel">
        <p className="gbhub-eyebrow">Selected spec</p>
        <h3>No spec selected</h3>
        <p>Select a spec to inspect its sprint, Kanban, commit, and PR evidence.</p>
      </aside>
    );
  }

  return (
    <aside className="lineage-detail-panel">
      <p className="gbhub-eyebrow">Selected spec</p>
      <h3>{prd.title}</h3>
      <p>{prd.prdId}</p>
      <CoveragePills prd={prd} />

      <div className="lineage-detail-grid">
        <span><b>{prd.sprintLinks.length}</b>Sprint links</span>
        <span><b>{prd.kanbanLinks.length}</b>Kanban links</span>
        <span><b>{prd.commits.length || (prd.lineage.hasCommit ? 1 : 0)}</b>Commits</span>
        <span><b>{prd.prs.length}</b>PR receipts</span>
      </div>

      <section>
        <h4>Sprint goals</h4>
        {prd.sprintLinks.length ? (
          <ul className="lineage-detail-list">
            {prd.sprintLinks.map((link, index) => (
              <li key={`${link.sprintId}:${link.rockId}:${index}`}>
                <span>{link.sprintId ?? "sprint tbd"} / {link.rockId ?? "rock tbd"}</span>
                <small>{link.relationship ?? "linked"} - {link.status ?? "status tbd"}</small>
              </li>
            ))}
          </ul>
        ) : (
          <p className="supabase-muted">No sprint rock link yet.</p>
        )}
      </section>

      <section>
        <h4>Kanban dispatch</h4>
        {prd.kanbanLinks.length ? (
          <ul className="lineage-detail-list">
            {prd.kanbanLinks.map((link, index) => (
              <li key={`${link.taskId}:${link.runId}:${index}`}>
                <span>{link.taskId ?? link.parentTaskId ?? link.runId ?? "task tbd"}</span>
                <small>{link.teamId ?? link.profile ?? "team tbd"} - {link.status ?? "status tbd"}</small>
              </li>
            ))}
          </ul>
        ) : (
          <p className="supabase-muted">No Kanban task assignment yet.</p>
        )}
      </section>

      <section>
        <h4>Commits and PRs</h4>
        {prd.commits.length || prd.prs.length ? (
          <ul className="lineage-detail-list">
            {prd.commits.map((commit) => (
              <li key={commit.sha ?? commit.shortSha}>
                <span>{commit.shortSha ?? "commit"} - {commit.repo ?? "repo"}</span>
                <small>{commit.message ?? "No commit message"}</small>
              </li>
            ))}
            {prd.prs.map((pr) => (
              <li key={pr.id ?? pr.url}>
                <span>{pr.repository ?? "repo"} #{pr.number ?? "?"}</span>
                <small>{pr.title ?? "No PR title"} - {pr.status ?? "status tbd"}</small>
              </li>
            ))}
          </ul>
        ) : (
          <p className="supabase-muted">No PR receipt is connected to this PRD yet.</p>
        )}
      </section>

      <div className="lineage-tag-cloud">
        {cleanLabels([...prd.tags, ...prd.components, ...prd.hermesProfiles]).slice(0, 16).map((tag) => (
          <span className="supabase-chip" key={tag}>{tag}</span>
        ))}
      </div>
    </aside>
  );
}

function SprintGoals({ goals }: { goals: SprintGoal[] }) {
  return (
    <section className="lineage-sprint-strip">
      <div>
        <p className="gbhub-eyebrow">Sprint rollup</p>
        <h3>Work specs rolling up to sprint goals</h3>
      </div>
      <div className="lineage-sprint-grid">
        {goals.slice(0, 6).map((goal) => (
          <article key={`${goal.sprintId}:${goal.rockId}`}>
            <strong>{goal.rockId ?? "Rock tbd"}</strong>
            <span>{goal.sprintId ?? "Sprint tbd"} - {goal.prdCount} PRDs</span>
            <small>{goal.kanbanCount} cards, {goal.commitCount} commits, {goal.prCount} PRs</small>
            <div className="lineage-tag-cloud">
              {goal.tags.slice(0, 5).map((tag) => (
                <span className="supabase-chip" key={tag}>{tag}</span>
              ))}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function RecentTimeline({ events }: { events: TimelineEvent[] }) {
  return (
    <section className="lineage-timeline">
      <p className="gbhub-eyebrow">Recent evidence</p>
      <ul>
        {events.slice(0, 12).map((event, index) => (
          <li key={`${event.type}:${event.prdId}:${event.at}:${index}`}>
            <span className={`supabase-chip ${event.type === "pr" ? "good" : ""}`}>{event.type}</span>
            <div>
              <strong>{event.title || event.prdId}</strong>
              <small>{event.detail || event.prdId} - {formatDate(event.at)}</small>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function PrdLineagePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<LineageManifest | null>(null);
  const [query, setQuery] = useState("");
  const [scope, setScopeState] = useState(searchParams.get("scope") || "tenant");
  const [sourceGroup, setSourceGroupState] = useState(searchParams.get("source") || "all");
  const [stageFilter, setStageFilterState] = useState<StageFilter>(
    (searchParams.get("stage") as StageFilter | null) || "all",
  );
  const [taxonomy, setTaxonomyState] = useState(searchParams.get("taxonomy") || "all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const tenant = useTenant();
  const activeTenant = tenantOption(tenant);

  const updateParam = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value === "all" || (key === "scope" && value === "tenant")) next.delete(key);
    else next.set(key, value);
    setSearchParams(next, { replace: true });
  };

  const setScope = (value: string) => {
    setScopeState(value);
    setSourceGroupState("all");
    setStageFilterState("all");
    setSelectedId(null);
    const next = new URLSearchParams(searchParams);
    if (value === "tenant") next.delete("scope");
    else next.set("scope", value);
    next.delete("source");
    next.delete("stage");
    setSearchParams(next, { replace: true });
  };

  const setSourceGroup = (value: string) => {
    setSourceGroupState(value);
    setSelectedId(null);
    updateParam("source", value);
  };

  const setStageFilter = (value: StageFilter) => {
    setStageFilterState(value);
    setSelectedId(null);
    updateParam("stage", value);
  };

  const setTaxonomy = (value: string) => {
    setTaxonomyState(value);
    setSelectedId(null);
    updateParam("taxonomy", value);
  };

  useEffect(() => {
    globalThis.document.title = "PRD Lineage | GBAutomation";
    void fetch("/gbauto-lineage/prd-lineage.json")
      .then((response) => response.json())
      .then((manifest: LineageManifest) => setData(manifest))
      .catch(() => setData(null));
  }, []);

  useEffect(() => {
    if (searchParams.get("scope")) return;
    setScope("tenant");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenant]);

  useEffect(() => {
    const nextScope = searchParams.get("scope") || "tenant";
    const nextSource = searchParams.get("source") || "all";
    const nextStage = ((searchParams.get("stage") as StageFilter | null) || "all");
    const nextTaxonomy = searchParams.get("taxonomy") || "all";
    setScopeState(nextScope);
    setSourceGroupState(nextSource);
    setStageFilterState(nextStage);
    setTaxonomyState(nextTaxonomy);
  }, [searchParams]);

  const clientOptions = useMemo(() => data?.clients.map((client) => client.client) ?? [], [data]);

  const scopedPrds = useMemo(() => {
    const prds = data?.prds ?? [];
    if (scope === "all") return prds;
    if (scope === "tenant") return prds.filter((prd) => matchesTenantClient(prd.client, tenant));
    return prds.filter((prd) => prd.client === scope);
  }, [data, scope, tenant]);

  const sourceOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const prd of scopedPrds) {
      for (const group of sourceGroupsFor(prd)) {
        counts.set(group, (counts.get(group) ?? 0) + 1);
      }
    }
    return Array.from(counts.entries())
      .map(([group, count]) => ({ count, group }))
      .sort((a, b) => b.count - a.count || a.group.localeCompare(b.group))
      .slice(0, 24);
  }, [scopedPrds]);

  const baseFilteredPrds = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return scopedPrds.filter((prd) => {
      const sourceMatch = sourceGroup === "all" || sourceGroupsFor(prd).includes(sourceGroup);
      const tagMatch =
        taxonomy === "all" ||
        prd.tags.includes(taxonomy) ||
        prd.components.includes(taxonomy) ||
        prd.hermesProfiles.includes(taxonomy);
      const queryMatch = !normalizedQuery || searchablePrd(prd).includes(normalizedQuery);
      return sourceMatch && tagMatch && queryMatch;
    });
  }, [query, scopedPrds, sourceGroup, taxonomy]);

  const filteredPrds = useMemo(
    () => baseFilteredPrds.filter((prd) => stageMatches(prd, stageFilter)),
    [baseFilteredPrds, stageFilter],
  );

  const selectedPrd = useMemo(() => {
    if (!filteredPrds.length) return null;
    return filteredPrds.find((prd) => prd.prdId === selectedId) ?? filteredPrds[0];
  }, [filteredPrds, selectedId]);

  const visibleIds = useMemo(() => new Set(filteredPrds.map((prd) => prd.prdId)), [filteredPrds]);
  const filteredGoals = useMemo(
    () =>
      (data?.sprintGoals ?? []).filter((goal) =>
        goal.prds.some((prd) => visibleIds.has(prd.prdId)),
      ),
    [data, visibleIds],
  );
  const filteredTimeline = useMemo(
    () => (data?.timeline ?? []).filter((event) => visibleIds.has(event.prdId)),
    [data, visibleIds],
  );

  const coverage = useMemo(() => {
    const total = filteredPrds.length || 1;
    return {
      commit: Math.round((filteredPrds.filter((prd) => prd.lineage.hasCommit).length / total) * 100),
      kanban: Math.round((filteredPrds.filter((prd) => prd.lineage.hasKanban).length / total) * 100),
      pr: Math.round((filteredPrds.filter((prd) => prd.lineage.hasPr).length / total) * 100),
      sprint: Math.round((filteredPrds.filter((prd) => prd.lineage.hasSprint).length / total) * 100),
    };
  }, [filteredPrds]);

  return (
    <div className="gbhub-page supabase-page lineage-page normal-case">
      <section className="gbhub-hero supabase-hero">
        <Link className="gbhub-brand-row" to="/overview">
          <span className="gbhub-mark"><Milestone className="h-4 w-4" /></span>
          <span>GBAutomation Spine</span>
        </Link>
        <Badge tone="outline" className="gbhub-badge">
          <Workflow className="h-3 w-3" />
          spec to delivery
        </Badge>
        <Badge tone="outline" className="gbhub-badge">scope: {scope === "tenant" ? activeTenant.label : `admin: ${scope}`}</Badge>
        <h2>Delivery Funnel</h2>
        <p>
          Work specs flowing into sprint goals, Kanban cards, commits, and PR receipts. The default view follows
          the selected tenant; admin all-client scope is available as an explicit filter.
        </p>
        <nav className="supabase-route-tabs" aria-label="Lineage related routes">
          <Link to="/kanban-data"><Workflow className="h-3.5 w-3.5" /> Kanban data</Link>
          <Link to="/repos"><GitCommit className="h-3.5 w-3.5" /> Repos</Link>
          <Link to="/artifacts"><Tags className="h-3.5 w-3.5" /> Artifacts</Link>
        </nav>
      </section>

      <section className="supabase-stat-grid">
        <StatCard icon={Milestone} label="Specs" sub={data ? `of ${formatNumber(data.counts.prds)} indexed` : "loading"} value={data ? formatNumber(filteredPrds.length) : "-"} />
        <StatCard icon={Workflow} label="Sprint Coverage" sub={data ? `${formatDate(data.generatedAt)} snapshot` : "loading"} value={data ? `${coverage.sprint}%` : "-"} />
        <StatCard icon={GitCommit} label="Commit Coverage" sub={`${filteredPrds.filter((prd) => prd.lineage.hasCommit).length} linked in view`} value={data ? `${coverage.commit}%` : "-"} />
        <StatCard icon={GitPullRequest} label="PR Coverage" sub={`${filteredPrds.filter((prd) => !prd.lineage.hasPr).length} missing in view`} value={data ? `${coverage.pr}%` : "-"} />
      </section>

      {data?.warnings.length ? (
        <section className="supabase-note">
          <AlertTriangle className="h-4 w-4" />
          <span>{data.warnings.join(" | ")}</span>
        </section>
      ) : null}

      <section className="supabase-toolbar lineage-toolbar">
        <div className="supabase-search">
          <Search className="h-4 w-4" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter specs, cards, commits, tags" />
        </div>
        <div className="supabase-filter-pills">
          <button className={scope === "tenant" ? "is-active" : ""} onClick={() => setScope("tenant")} type="button">{activeTenant.label}</button>
          <button className={scope === "all" ? "is-active" : ""} onClick={() => setScope("all")} type="button">Admin all</button>
          {clientOptions.slice(0, 6).map((client) => (
            <button className={scope === client ? "is-active" : ""} key={client} onClick={() => setScope(client)} type="button">
              {client}
            </button>
          ))}
        </div>
        <label className="lineage-select">
          <Filter className="h-4 w-4" />
          <select value={sourceGroup} onChange={(event) => setSourceGroup(event.target.value)}>
            <option value="all">All repos/folders</option>
            {sourceOptions.map((option) => (
              <option key={option.group} value={option.group}>{option.group} ({option.count})</option>
            ))}
          </select>
        </label>
        <label className="lineage-select">
          <Tags className="h-4 w-4" />
          <select value={taxonomy} onChange={(event) => setTaxonomy(event.target.value)}>
            <option value="all">All taxonomy</option>
            {data?.tags.slice(0, 80).map((tag) => (
              <option key={tag.tag} value={tag.tag}>{tag.tag} ({tag.count})</option>
            ))}
          </select>
        </label>
      </section>

      {data ? (
        <>
          <VisualStack active={stageFilter} onChange={setStageFilter} prds={baseFilteredPrds} />

          <section className="lineage-layout">
            <div className="lineage-prd-list">
              {filteredPrds.map((prd) => (
                <PrdCard
                  active={selectedPrd?.prdId === prd.prdId}
                  key={prd.prdId}
                  onSelect={() => setSelectedId(prd.prdId)}
                  prd={prd}
                />
              ))}
            </div>
            <DetailPanel prd={selectedPrd} />
          </section>

          <SprintGoals goals={filteredGoals} />
          <RecentTimeline events={filteredTimeline} />
        </>
      ) : (
        <section className="gbhub-empty-state">PRD lineage snapshot unavailable. Run <code>npm run gbauto:lineage</code>.</section>
      )}
    </div>
  );
}
