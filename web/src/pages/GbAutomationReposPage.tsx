import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { GitBranch, GitCommit, LayoutGrid, Search } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";

interface RepoCommit {
  client: string;
  date: string;
  message: string;
  repo: string;
  scope?: string | null;
  sha: string;
  short_sha: string;
}

interface RepoSlice {
  branch: string;
  client: string;
  commit_count: number;
  last_commit_at: string;
  recent_commits: RepoCommit[];
  repo: string;
  role: string;
}

interface ReposManifest {
  client_count: number;
  commit_count: number;
  generated_at: string;
  recent_activity: RepoCommit[];
  repo_count: number;
  repos: RepoSlice[];
  window_days: number;
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

function RepoCard({ repo }: { repo: RepoSlice }) {
  const color = clientColor(repo.client);
  return (
    <article className="gbhub-repo-card">
      <div className="gbhub-repo-topline">
        <h3>{repo.repo}</h3>
        <span style={{ borderColor: color, color }}>{repo.client}</span>
      </div>
      <p>{repo.role}</p>
      <div className="gbhub-repo-meta">
        <span><strong>{repo.commit_count}</strong> commits</span>
        <span>{repo.branch}</span>
        <span>{relativeTime(repo.last_commit_at)}</span>
      </div>
      <ul>
        {repo.recent_commits.slice(0, 4).map((commit) => (
          <li key={commit.sha}>
            <code>{commit.short_sha.slice(0, 7)}</code>
            <span>{commit.message}</span>
          </li>
        ))}
      </ul>
    </article>
  );
}

export default function GbAutomationReposPage() {
  const [data, setData] = useState<ReposManifest | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    globalThis.document.title = "Repos & Commits | GBAutomation";
    void fetch("/repos/repos-manifest.json").then((response) => response.json()).then(setData).catch(() => setData(null));
  }, []);

  const filteredRepos = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!data) return [];
    if (!normalized) return data.repos;
    return data.repos.filter((repo) =>
      `${repo.repo} ${repo.client} ${repo.role} ${repo.recent_commits.map((commit) => commit.message).join(" ")}`.toLowerCase().includes(normalized),
    );
  }, [data, query]);

  return (
    <div className="gbhub-page normal-case">
      <section className="gbhub-hero">
        <Link className="gbhub-brand-row" to="/overview">
          <span className="gbhub-mark">gb</span>
          <span>GBAutomation</span>
        </Link>
        <Badge tone="outline" className="gbhub-badge">
          <GitBranch className="h-3 w-3" />
          Repo index
        </Badge>
        <h2>Repos & Commits</h2>
        {data ? (
          <p>
            <strong>{data.commit_count}</strong> commits across <strong>{data.repo_count}</strong> repos
            and <strong>{data.client_count}</strong> clients, last {data.window_days} days.
          </p>
        ) : (
          <p>Loading the deployed GB Automation repo manifest.</p>
        )}
      </section>

      <section className="gbhub-repo-toolbar">
        <label>
          <Search className="h-4 w-4" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter repos, clients, commits" />
        </label>
        <span><LayoutGrid className="h-3.5 w-3.5" /> {filteredRepos.length} repos</span>
      </section>

      {data ? (
        <div className="gbhub-two-column">
          <section className="gbhub-repo-grid">
            {filteredRepos.map((repo) => <RepoCard key={repo.repo} repo={repo} />)}
          </section>

          <aside className="gbhub-activity-panel">
            <p className="gbhub-eyebrow">Recent Activity</p>
            <ul>
              {data.recent_activity.slice(0, 16).map((commit) => (
                <li key={commit.sha}>
                  <div>
                    <span className="gbhub-client-dot" style={{ background: clientColor(commit.client) }} />
                    <span>{commit.repo}</span>
                    <time>{relativeTime(commit.date)}</time>
                  </div>
                  <p><GitCommit className="h-3 w-3" /> {commit.message}</p>
                </li>
              ))}
            </ul>
          </aside>
        </div>
      ) : (
        <section className="gbhub-empty-state">Commit index unavailable.</section>
      )}
    </div>
  );
}
