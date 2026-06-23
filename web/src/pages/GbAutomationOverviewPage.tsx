import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Activity, Boxes, FileText, GitBranch, Network, Sparkles } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";

interface RepoCommit {
  client: string;
  date: string;
  message: string;
  repo: string;
  sha: string;
}

interface ReposManifest {
  client_count: number;
  commit_count: number;
  recent_activity: RepoCommit[];
  repo_count: number;
  window_days: number;
}

interface DagManifestItem {
  agent_count: number;
  date: string;
  node_count: number;
  slug: string;
  title: string;
}

interface PrdManifestItem {
  slug: string;
  title: string;
}

const CLIENT_COLORS: Record<string, string> = {
  ecom: "#3b82f6",
  "eagle-app": "#C08A3E",
  "fish-group": "#8A5FBF",
  gbautomation: "#D97757",
  "greg-trading": "#4F9D69",
  "jason-diaz": "#06b6d4",
  "the-mall": "#ec4899",
};

const spokes = [
  { to: "/repos", label: "Repos & Commits", desc: "Plan to commit traceability", icon: GitBranch },
  { to: "/artifacts", label: "Artifacts", desc: "HTML, page views, reports, PRDs, and visuals", icon: FileText },
  { to: "/logs", label: "Observability", desc: "Agent runs, traces, logs, and telemetry", icon: Activity },
  { to: "/plugins", label: "Apps & Plugins", desc: "Mini-apps, integrations, and installed surfaces", icon: Boxes },
];

function clientColor(client: string) {
  return CLIENT_COLORS[client] ?? "#6b6b6b";
}

function relativeTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const days = Math.floor((Date.now() - date.getTime()) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" }).format(date);
}

function StatCard({
  accent,
  label,
  sub,
  to,
  value,
}: {
  accent: string;
  label: string;
  sub?: string;
  to: string;
  value: string | number;
}) {
  return (
    <Link className="gbhub-stat-card" to={to}>
      <strong>{value}</strong>
      <span style={{ color: accent }}>{label}</span>
      {sub ? <small>{sub}</small> : null}
    </Link>
  );
}

export default function GbAutomationOverviewPage() {
  const [repos, setRepos] = useState<ReposManifest | null>(null);
  const [prds, setPrds] = useState<PrdManifestItem[] | null>(null);
  const [dags, setDags] = useState<DagManifestItem[] | null>(null);

  useEffect(() => {
    globalThis.document.title = "Overview | GBAutomation";
    void fetch("/repos/repos-manifest.json").then((response) => response.json()).then(setRepos).catch(() => setRepos(null));
    void fetch("/prds/prds-manifest.json").then((response) => response.json()).then(setPrds).catch(() => setPrds(null));
    void fetch("/observability/dags/dags-manifest.json").then((response) => response.json()).then(setDags).catch(() => setDags(null));
  }, []);

  const recentActivity = useMemo(() => repos?.recent_activity.slice(0, 14) ?? [], [repos]);

  return (
    <div className="gbhub-page normal-case">
      <section className="gbhub-hero">
        <div className="gbhub-brand-row">
          <span className="gbhub-mark">gb</span>
          <span>GBAutomation</span>
        </div>
        <Badge tone="outline" className="gbhub-badge">
          <Sparkles className="h-3 w-3" />
          Hermes hub
        </Badge>
        <h2>Operations Overview</h2>
        <p>
          Live status across the build pipeline: what shipped, what is tracked, and where the work is happening.
          Every number is backed by a local static index copied from the deployed GB Automation site.
        </p>
      </section>

      <section className="gbhub-stat-grid" aria-label="GBAutomation overview stats">
        <StatCard accent="#D97757" label="Commits" sub={repos ? `last ${repos.window_days}d` : "loading"} to="/repos" value={repos?.commit_count ?? "-"} />
        <StatCard accent="#8A5FBF" label="Repos" sub={repos ? `${repos.client_count} clients` : "loading"} to="/repos" value={repos?.repo_count ?? "-"} />
        <StatCard accent="#06b6d4" label="PRDs & Plans" sub="strategy artifacts" to="/artifacts" value={prds?.length ?? "-"} />
        <StatCard accent="#4F9D69" label="Agent DAGs" sub="Langfuse traces" to="/logs" value={dags?.length ?? "-"} />
      </section>

      <div className="gbhub-two-column">
        <section>
          <p className="gbhub-eyebrow">Workspace</p>
          <div className="gbhub-spoke-grid">
            {spokes.map((spoke) => {
              const Icon = spoke.icon;
              return (
                <Link className="gbhub-spoke-card" key={spoke.to} to={spoke.to}>
                  <span className="gbhub-spoke-icon"><Icon className="h-4 w-4" /></span>
                  <span>
                    <strong>{spoke.label}</strong>
                    <small>{spoke.desc}</small>
                  </span>
                </Link>
              );
            })}
          </div>
        </section>

        <aside className="gbhub-activity-panel">
          <p className="gbhub-eyebrow">Recent Activity</p>
          {recentActivity.length ? (
            <ul>
              {recentActivity.map((commit) => (
                <li key={commit.sha}>
                  <div>
                    <span className="gbhub-client-dot" style={{ background: clientColor(commit.client) }} />
                    <span>{commit.repo}</span>
                    <time>{relativeTime(commit.date)}</time>
                  </div>
                  <p>{commit.message}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="gbhub-muted">Loading repo activity...</p>
          )}
        </aside>
      </div>

      {dags?.length ? (
        <section className="gbhub-dag-strip">
          <p className="gbhub-eyebrow">Agent Graphs</p>
          <div>
            {dags.map((dag) => (
              <article key={dag.slug}>
                <Network className="h-4 w-4" />
                <strong>{dag.title}</strong>
                <span>{dag.agent_count} agents, {dag.node_count} nodes</span>
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
