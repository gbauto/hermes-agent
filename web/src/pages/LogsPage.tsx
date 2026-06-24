import {
  useEffect,
  useLayoutEffect,
  useState,
  useCallback,
  useMemo,
  useRef,
} from "react";
import type { ReactNode, RefObject } from "react";
import {
  Activity,
  AlertTriangle,
  Clock3,
  Database,
  FileText,
  RadioTower,
  RefreshCw,
  Search,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { api } from "@/lib/api";
import type {
  SupabaseCronLogsResponse,
  SupabaseLogRow,
  SupabaseLogsSummaryResponse,
  SupabaseRowsResponse,
  SupabaseTracesLogsResponse,
} from "@/lib/api";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { FilterGroup, Segmented } from "@nous-research/ui/ui/components/segmented";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Switch } from "@nous-research/ui/ui/components/switch";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { PluginSlot } from "@/plugins";
import { cn } from "@/lib/utils";

const MODES = ["live", "evidence", "traces"] as const;
const FILES = ["agent", "errors", "gateway"] as const;
const LEVELS = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR"] as const;
const COMPONENTS = ["all", "gateway", "agent", "tools", "cli", "cron"] as const;
const LINE_COUNTS = [50, 100, 200, 500] as const;
const EVIDENCE_TABS = ["timeline", "failures", "cron", "host-jobs", "artifacts"] as const;
const SUPABASE_LIMITS = [50, 100, 200, 500] as const;
const SUPABASE_WINDOWS = [3, 7, 14, 30] as const;

type Mode = (typeof MODES)[number];
type EvidenceTab = (typeof EVIDENCE_TABS)[number];

function classifyLine(line: string): "error" | "warning" | "info" | "debug" {
  const upper = line.toUpperCase();
  if (
    upper.includes("ERROR") ||
    upper.includes("CRITICAL") ||
    upper.includes("FATAL")
  )
    return "error";
  if (upper.includes("WARNING") || upper.includes("WARN")) return "warning";
  if (upper.includes("DEBUG")) return "debug";
  return "info";
}

const LINE_COLORS: Record<string, string> = {
  error: "text-destructive",
  warning: "text-warning",
  info: "text-foreground",
  debug: "text-muted-foreground/60",
};

const EVIDENCE_COLUMNS: Record<EvidenceTab, string[]> = {
  timeline: ["started_at", "source_table", "status_family", "profile", "client_slug", "title", "trace_id"],
  failures: ["started_at", "source_table", "raw_status", "profile", "client_slug", "title", "trace_id"],
  cron: ["cron_name", "status", "cron_started_at", "issue_id", "branch", "duration_s", "agent_summary"],
  "host-jobs": ["created_at", "status", "pathway", "host_class", "task_name", "skill_name", "working_directory", "receipt_path"],
  artifacts: ["modified_at", "agent", "category", "client_slug", "repo_slug", "content_mode", "basename"],
};

const TRACE_COLUMNS = ["trace_timestamp", "trace_name", "agent", "profile", "runtime", "total_cost", "total_tokens", "langfuse_url"];
const GAP_COLUMNS = ["last_seen_at", "task_id", "run_id", "profile", "board", "repo", "has_langfuse_trace", "match_key"];
const COVERAGE_COLUMNS = ["run_day", "profile", "client_slug", "repo_slug", "run_count", "runs_with_trace_id", "runs_with_trace_mirror", "trace_mirror_coverage_pct"];

const toOptions = <T extends string | number>(values: readonly T[]) =>
  values.map((v) => ({ value: String(v), label: String(v) }));

const filterGroupClass =
  "flex min-w-0 w-full flex-col items-start gap-1.5 sm:w-auto sm:max-w-full sm:flex-row sm:items-center";

const segmentedClass =
  "w-fit max-w-full flex-wrap justify-start self-start";

function formatLabel(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatCompact(value: unknown) {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number)) return "-";
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1, notation: "compact" }).format(number);
}

function formatDate(value: unknown) {
  if (!value) return "-";
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function formatValue(value: unknown, key = "") {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") {
    if (key.includes("cost")) return `$${value.toFixed(4)}`;
    if (key.includes("pct")) return `${value}%`;
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  const text = String(value);
  if (key.includes("_at") || key.includes("timestamp") || key === "run_day") return formatDate(text);
  return text.length > 140 ? `${text.slice(0, 137)}...` : text;
}

function columnsFor(rows: SupabaseLogRow[], preferred: string[]) {
  const keys = Object.keys(rows[0] ?? {});
  return [
    ...preferred.filter((key) => keys.includes(key)),
    ...keys.filter((key) => !preferred.includes(key)),
  ].slice(0, 8);
}

function rowKey(row: SupabaseLogRow, index: number) {
  return String(
    row.run_key ??
      row.run_id ??
      row.trace_id ??
      row.source_id ??
      row.output_id ??
      row.task_id ??
      index,
  );
}

function statusClass(value: unknown) {
  const text = String(value ?? "").toLowerCase();
  if (["ok", "success", "succeeded", "true", "passed", "done", "triage_created"].includes(text)) {
    return "border-emerald-500/25 bg-emerald-500/10 text-emerald-300";
  }
  if (["failed", "error", "false", "blocked", "timeout", "failed_silent"].includes(text)) {
    return "border-destructive/25 bg-destructive/10 text-destructive";
  }
  if (["running", "pending", "unknown", "noop", "skipped"].includes(text)) {
    return "border-amber-500/25 bg-amber-500/10 text-amber-300";
  }
  return "border-border bg-muted/30 text-muted-foreground";
}

export default function LogsPage() {
  const [mode, setMode] = useState<Mode>("live");
  const [file, setFile] = useState<(typeof FILES)[number]>("agent");
  const [level, setLevel] = useState<(typeof LEVELS)[number]>("ALL");
  const [component, setComponent] =
    useState<(typeof COMPONENTS)[number]>("all");
  const [lineCount, setLineCount] = useState<(typeof LINE_COUNTS)[number]>(100);
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>("timeline");
  const [supabaseDays, setSupabaseDays] = useState<(typeof SUPABASE_WINDOWS)[number]>(14);
  const [supabaseLimit, setSupabaseLimit] = useState<(typeof SUPABASE_LIMITS)[number]>(100);
  const [search, setSearch] = useState("");
  const [repoFilter, setRepoFilter] = useState("");
  const [workdirFilter, setWorkdirFilter] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  const [summary, setSummary] = useState<SupabaseLogsSummaryResponse | null>(null);
  const [rowsResponse, setRowsResponse] = useState<SupabaseRowsResponse | null>(null);
  const [cronResponse, setCronResponse] = useState<SupabaseCronLogsResponse | null>(null);
  const [tracesResponse, setTracesResponse] = useState<SupabaseTracesLogsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { t } = useI18n();
  const { setAfterTitle, setEnd } = usePageHeader();

  const fetchLiveLogs = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .getLogs({ file, lines: lineCount, level, component })
      .then((resp) => {
        setLines(resp.lines);
        setTimeout(() => {
          if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
          }
        }, 50);
      })
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false));
  }, [file, lineCount, level, component]);

  const supabaseParams = useMemo(
    () => ({
      days: supabaseDays,
      limit: supabaseLimit,
      repo: repoFilter.trim() || undefined,
      search: search.trim() || undefined,
      workdir: workdirFilter.trim() || undefined,
    }),
    [repoFilter, search, supabaseDays, supabaseLimit, workdirFilter],
  );

  const fetchSupabaseLogs = useCallback(() => {
    setLoading(true);
    setError(null);
    void api
      .getSupabaseLogsSummary()
      .then(setSummary)
      .catch((err) => setError(String(err)));

    const request =
      mode === "traces"
        ? api.getSupabaseLogsTraces({ ...supabaseParams, days: supabaseDays || 7 })
        : evidenceTab === "cron"
          ? api.getSupabaseLogsCron(supabaseParams)
          : evidenceTab === "failures"
            ? api.getSupabaseLogsFailures(supabaseParams)
            : evidenceTab === "host-jobs"
              ? api.getSupabaseLogsHostJobs(supabaseParams)
              : evidenceTab === "artifacts"
                ? api.getSupabaseLogsArtifacts(supabaseParams)
                : api.getSupabaseLogsTimeline(supabaseParams);

    request
      .then((resp) => {
        if (mode === "traces") {
          setTracesResponse(resp as SupabaseTracesLogsResponse);
          setRowsResponse(null);
          setCronResponse(null);
        } else if (evidenceTab === "cron") {
          setCronResponse(resp as SupabaseCronLogsResponse);
          setRowsResponse(null);
          setTracesResponse(null);
        } else {
          setRowsResponse(resp as SupabaseRowsResponse);
          setCronResponse(null);
          setTracesResponse(null);
        }
        if (!resp.ok && resp.error) setError(resp.error);
      })
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false));
  }, [evidenceTab, mode, supabaseDays, supabaseParams]);

  const fetchCurrent = useCallback(() => {
    if (mode === "live") {
      fetchLiveLogs();
    } else {
      fetchSupabaseLogs();
    }
  }, [fetchLiveLogs, fetchSupabaseLogs, mode]);

  useLayoutEffect(() => {
    setAfterTitle(
      <span className="flex items-center gap-2">
        {loading && <Spinner className="shrink-0 text-base text-primary" />}
        <Badge tone="secondary" className="text-[10px]">
          {mode === "live"
            ? `${file} · ${level} · ${component}`
            : `${formatLabel(mode)} · ${supabaseDays}d · ${supabaseLimit}`}
        </Badge>
      </span>,
    );
    setEnd(
      <div className="flex w-full min-w-0 flex-wrap items-center justify-start gap-2 sm:justify-end sm:gap-3">
        <div className="flex items-center gap-2">
          <Switch
            checked={autoRefresh}
            onCheckedChange={setAutoRefresh}
            id="logs-auto-refresh"
          />
          <Label htmlFor="logs-auto-refresh" className="text-xs cursor-pointer">
            {t.logs.autoRefresh}
          </Label>
          {autoRefresh && (
            <Badge tone="success" className="text-[10px]">
              <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
              {t.common.live}
            </Badge>
          )}
        </div>
        <Button
          type="button"
          size="sm"
          outlined
          onClick={fetchCurrent}
          disabled={loading}
          prefix={loading ? <Spinner /> : <RefreshCw />}
        >
          {t.common.refresh}
        </Button>
      </div>,
    );
    return () => {
      setAfterTitle(null);
      setEnd(null);
    };
  }, [
    autoRefresh,
    component,
    fetchCurrent,
    file,
    level,
    loading,
    mode,
    setAfterTitle,
    setEnd,
    supabaseDays,
    supabaseLimit,
    t.common.live,
    t.common.refresh,
    t.logs.autoRefresh,
  ]);

  useEffect(() => {
    const timeout = window.setTimeout(fetchCurrent, 0);
    return () => window.clearTimeout(timeout);
  }, [fetchCurrent]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(fetchCurrent, mode === "live" ? 5000 : 30000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchCurrent, mode]);

  return (
    <div className="flex min-w-0 max-w-full flex-col gap-4">
      <PluginSlot name="logs:top" />
      <Segmented
        className={segmentedClass}
        value={mode}
        onChange={(value) => setMode(value as Mode)}
        options={MODES.map((value) => ({
          value,
          label: formatLabel(value === "live" ? "live_tail" : value),
        }))}
      />

      {mode === "live" ? (
        <LiveTailToolbar
          component={component}
          file={file}
          level={level}
          lineCount={lineCount}
          setComponent={setComponent}
          setFile={setFile}
          setLevel={setLevel}
          setLineCount={setLineCount}
        />
      ) : (
        <SupabaseToolbar
          evidenceTab={evidenceTab}
          mode={mode}
          repoFilter={repoFilter}
          search={search}
          setEvidenceTab={setEvidenceTab}
          setRepoFilter={setRepoFilter}
          setSearch={setSearch}
          setSupabaseDays={setSupabaseDays}
          setSupabaseLimit={setSupabaseLimit}
          setWorkdirFilter={setWorkdirFilter}
          supabaseDays={supabaseDays}
          supabaseLimit={supabaseLimit}
          workdirFilter={workdirFilter}
        />
      )}

      {error && (
        <div className="rounded-none border border-destructive/20 bg-destructive/10 p-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {mode === "live" ? (
        <LiveTailCard
          file={file}
          lineCount={lineCount}
          lines={lines}
          loading={loading}
          scrollRef={scrollRef}
          noLogLinesLabel={t.logs.noLogLines}
        />
      ) : mode === "evidence" ? (
        <EvidenceView
          cronResponse={cronResponse}
          evidenceTab={evidenceTab}
          loading={loading}
          rowsResponse={rowsResponse}
          summary={summary}
        />
      ) : (
        <TracesView
          loading={loading}
          summary={summary}
          tracesResponse={tracesResponse}
        />
      )}
      <PluginSlot name="logs:bottom" />
    </div>
  );
}

function LiveTailToolbar({
  component,
  file,
  level,
  lineCount,
  setComponent,
  setFile,
  setLevel,
  setLineCount,
}: {
  component: (typeof COMPONENTS)[number];
  file: (typeof FILES)[number];
  level: (typeof LEVELS)[number];
  lineCount: (typeof LINE_COUNTS)[number];
  setComponent: (value: (typeof COMPONENTS)[number]) => void;
  setFile: (value: (typeof FILES)[number]) => void;
  setLevel: (value: (typeof LEVELS)[number]) => void;
  setLineCount: (value: (typeof LINE_COUNTS)[number]) => void;
}) {
  const { t } = useI18n();
  return (
    <div
      role="toolbar"
      aria-label={t.logs.title}
      className="flex min-w-0 max-w-full flex-col items-start gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:gap-x-6 sm:gap-y-3"
    >
      <FilterGroup label={t.logs.file} className={filterGroupClass}>
        <Segmented className={segmentedClass} value={file} onChange={(v) => setFile(v as (typeof FILES)[number])} options={toOptions(FILES)} />
      </FilterGroup>
      <FilterGroup label={t.logs.level} className={filterGroupClass}>
        <Segmented className={segmentedClass} value={level} onChange={(v) => setLevel(v as (typeof LEVELS)[number])} options={toOptions(LEVELS)} />
      </FilterGroup>
      <FilterGroup label={t.logs.component} className={filterGroupClass}>
        <Segmented className={segmentedClass} value={component} onChange={(v) => setComponent(v as (typeof COMPONENTS)[number])} options={toOptions(COMPONENTS)} />
      </FilterGroup>
      <FilterGroup label={t.logs.lines} className={filterGroupClass}>
        <Segmented
          className={segmentedClass}
          value={String(lineCount)}
          onChange={(v) => setLineCount(Number(v) as (typeof LINE_COUNTS)[number])}
          options={toOptions(LINE_COUNTS)}
        />
      </FilterGroup>
    </div>
  );
}

function SupabaseToolbar({
  evidenceTab,
  mode,
  repoFilter,
  search,
  setEvidenceTab,
  setRepoFilter,
  setSearch,
  setSupabaseDays,
  setSupabaseLimit,
  setWorkdirFilter,
  supabaseDays,
  supabaseLimit,
  workdirFilter,
}: {
  evidenceTab: EvidenceTab;
  mode: Mode;
  repoFilter: string;
  search: string;
  setEvidenceTab: (value: EvidenceTab) => void;
  setRepoFilter: (value: string) => void;
  setSearch: (value: string) => void;
  setSupabaseDays: (value: (typeof SUPABASE_WINDOWS)[number]) => void;
  setSupabaseLimit: (value: (typeof SUPABASE_LIMITS)[number]) => void;
  setWorkdirFilter: (value: string) => void;
  supabaseDays: (typeof SUPABASE_WINDOWS)[number];
  supabaseLimit: (typeof SUPABASE_LIMITS)[number];
  workdirFilter: string;
}) {
  return (
    <div className="flex min-w-0 max-w-full flex-col gap-3">
      {mode === "evidence" && (
        <FilterGroup label="Dataset" className={filterGroupClass}>
          <Segmented
            className={segmentedClass}
            value={evidenceTab}
            onChange={(v) => setEvidenceTab(v as EvidenceTab)}
            options={EVIDENCE_TABS.map((value) => ({
              value,
              label: formatLabel(value),
            }))}
          />
        </FilterGroup>
      )}
      <div className="flex min-w-0 max-w-full flex-col items-start gap-3 sm:flex-row sm:flex-wrap sm:items-center">
        <FilterGroup label="Window" className={filterGroupClass}>
          <Segmented
            className={segmentedClass}
            value={String(supabaseDays)}
            onChange={(v) => setSupabaseDays(Number(v) as (typeof SUPABASE_WINDOWS)[number])}
            options={SUPABASE_WINDOWS.map((days) => ({ value: String(days), label: `${days}d` }))}
          />
        </FilterGroup>
        <FilterGroup label="Rows" className={filterGroupClass}>
          <Segmented
            className={segmentedClass}
            value={String(supabaseLimit)}
            onChange={(v) => setSupabaseLimit(Number(v) as (typeof SUPABASE_LIMITS)[number])}
            options={toOptions(SUPABASE_LIMITS)}
          />
        </FilterGroup>
        <div className="relative w-full min-w-0 sm:max-w-sm">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            aria-label="Search Supabase logs"
            className="h-8 w-full rounded-none border border-border bg-background pl-8 pr-3 text-xs outline-none focus:border-primary"
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search IDs, profiles, clients, trace URLs"
            value={search}
          />
        </div>
        <div className="relative w-full min-w-0 sm:max-w-[220px]">
          <Database className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            aria-label="Filter by repo"
            className="h-8 w-full rounded-none border border-border bg-background pl-8 pr-3 text-xs outline-none focus:border-primary"
            onChange={(event) => setRepoFilter(event.target.value)}
            placeholder="Repo slug or path"
            value={repoFilter}
          />
        </div>
        <div className="relative w-full min-w-0 sm:max-w-[260px]">
          <FileText className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            aria-label="Filter by working directory"
            className="h-8 w-full rounded-none border border-border bg-background pl-8 pr-3 text-xs outline-none focus:border-primary"
            onChange={(event) => setWorkdirFilter(event.target.value)}
            placeholder="Working dir or artifact path"
            value={workdirFilter}
          />
        </div>
      </div>
    </div>
  );
}

function LiveTailCard({
  file,
  lineCount,
  lines,
  loading,
  noLogLinesLabel,
  scrollRef,
}: {
  file: string;
  lineCount: number;
  lines: string[];
  loading: boolean;
  noLogLinesLabel: string;
  scrollRef: RefObject<HTMLDivElement | null>;
}) {
  return (
    <Card className="min-w-0 max-w-full overflow-hidden">
      <CardHeader className="px-4 py-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <FileText className="h-4 w-4" />
          {file}.log
          <Badge tone="secondary" className="text-[10px]">
            last {lineCount}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div
          ref={scrollRef}
          className="max-h-[calc(100vh-260px)] min-h-[400px] max-w-full overflow-auto p-4 font-mono-ui text-xs leading-5 break-words"
        >
          {lines.length === 0 && !loading && (
            <p className="py-8 text-center text-muted-foreground">
              {noLogLinesLabel}
            </p>
          )}
          {lines.map((line, i) => {
            const cls = classifyLine(line);
            return (
              <div
                key={`${i}:${line.slice(0, 32)}`}
                className={`${LINE_COLORS[cls]} -mx-1 px-1 hover:bg-secondary/20`}
              >
                {line}
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

function EvidenceView({
  cronResponse,
  evidenceTab,
  loading,
  rowsResponse,
  summary,
}: {
  cronResponse: SupabaseCronLogsResponse | null;
  evidenceTab: EvidenceTab;
  loading: boolean;
  rowsResponse: SupabaseRowsResponse | null;
  summary: SupabaseLogsSummaryResponse | null;
}) {
  const counts = summary?.counts ?? {};
  return (
    <div className="flex flex-col gap-4">
      <KpiGrid>
        <KpiCard icon={AlertTriangle} label="Failures" sub="14 day window" value={counts.recent_failures} />
        <KpiCard icon={Clock3} label="Cron Runs" sub="recent ticks" value={counts.cron_runs} />
        <KpiCard icon={Database} label="Host Receipts" sub="job rows" value={counts.host_receipts} />
        <KpiCard icon={FileText} label="Log Artifacts" sub="redacted index" value={counts.log_artifacts} />
      </KpiGrid>
      {evidenceTab === "cron" ? (
        <>
          <DataTable
            columns={["cron_name", "runs", "ok_outputs", "failed_outputs", "cost_usd", "latest_started_at"]}
            emptyLabel="No cron rollups returned."
            loading={loading}
            rows={cronResponse?.rollup ?? []}
            title="Cron Rollup"
          />
          <DataTable
            columns={EVIDENCE_COLUMNS.cron}
            emptyLabel="No cron output rows returned."
            loading={loading}
            rows={cronResponse?.outputs ?? []}
            title="Cron Outputs"
          />
        </>
      ) : (
        <DataTable
          columns={EVIDENCE_COLUMNS[evidenceTab]}
          emptyLabel="No Supabase evidence rows returned."
          loading={loading}
          rows={rowsResponse?.rows ?? []}
          title={formatLabel(evidenceTab)}
        />
      )}
    </div>
  );
}

function TracesView({
  loading,
  summary,
  tracesResponse,
}: {
  loading: boolean;
  summary: SupabaseLogsSummaryResponse | null;
  tracesResponse: SupabaseTracesLogsResponse | null;
}) {
  const counts = summary?.counts ?? {};
  const totals = summary?.trace_totals ?? {};
  const coverage = summary?.trace_coverage ?? {};
  const joinCandidates = Number(coverage.obs_join_candidates ?? 0);
  const matches = Number(coverage.obs_langfuse_matches ?? 0);
  const coveragePct = joinCandidates ? `${Math.round((matches / joinCandidates) * 100)}%` : "0%";
  return (
    <div className="flex flex-col gap-4">
      <KpiGrid>
        <KpiCard icon={RadioTower} label="Traces" sub="Langfuse mirror" value={counts.traces} />
        <KpiCard icon={Activity} label="Tokens" sub="7 day total" value={totals.tokens} />
        <KpiCard icon={Database} label="Cost" sub="7 day total" value={`$${Number(totals.cost ?? 0).toFixed(4)}`} />
        <KpiCard icon={AlertTriangle} label="Join Coverage" sub={`${matches}/${joinCandidates} matched`} value={coveragePct} />
      </KpiGrid>
      <DataTable
        columns={TRACE_COLUMNS}
        emptyLabel="No Langfuse traces returned."
        loading={loading}
        rows={tracesResponse?.rows ?? []}
        title="Langfuse Traces"
      />
      <DataTable
        columns={GAP_COLUMNS}
        emptyLabel="No missing trace join candidates returned."
        loading={loading}
        rows={tracesResponse?.gaps ?? []}
        title="Trace Join Gaps"
      />
      <DataTable
        columns={COVERAGE_COLUMNS}
        emptyLabel="No trace coverage rows returned."
        loading={loading}
        rows={tracesResponse?.coverage ?? []}
        title="Daily Trace Coverage"
      />
    </div>
  );
}

function KpiGrid({ children }: { children: ReactNode }) {
  return (
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {children}
    </section>
  );
}

function KpiCard({
  icon: Icon,
  label,
  sub,
  value,
}: {
  icon: LucideIcon;
  label: string;
  sub: string;
  value: unknown;
}) {
  return (
    <article className="rounded-none border border-border bg-muted/20 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <Icon className="h-4 w-4 text-muted-foreground" />
        <span className="text-[10px] uppercase text-muted-foreground">{sub}</span>
      </div>
      <strong className="block text-2xl font-semibold tabular-nums">
        {typeof value === "number" ? formatCompact(value) : String(value ?? "-")}
      </strong>
      <span className="text-xs text-muted-foreground">{label}</span>
    </article>
  );
}

function DataTable({
  columns,
  emptyLabel,
  loading,
  rows,
  title,
}: {
  columns: string[];
  emptyLabel: string;
  loading: boolean;
  rows: SupabaseLogRow[];
  title: string;
}) {
  const visibleColumns = columnsFor(rows, columns);
  return (
    <Card className="min-w-0 overflow-hidden rounded-none">
      <CardHeader className="px-4 py-3">
        <CardTitle className="flex items-center justify-between gap-3 text-sm">
          <span>{title}</span>
          <Badge tone="secondary" className="text-[10px]">
            {rows.length} rows
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {loading && rows.length === 0 ? (
          <div className="flex items-center justify-center py-12">
            <Spinner className="text-xl text-primary" />
          </div>
        ) : rows.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            {emptyLabel}
          </p>
        ) : (
          <div className="max-h-[520px] overflow-auto">
            <table className="w-full min-w-[760px] border-collapse text-left text-xs">
              <thead className="sticky top-0 z-10 border-b border-border bg-background">
                <tr>
                  {visibleColumns.map((column) => (
                    <th key={column} className="px-3 py-2 font-mondwest text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
                      {formatLabel(column)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={rowKey(row, index)} className="border-b border-border/60 hover:bg-muted/30">
                    {visibleColumns.map((column) => {
                      const value = row[column];
                      const isStatus = column.includes("status") || column === "has_langfuse_trace";
                      const isUrl = column.includes("url") && typeof value === "string" && value.startsWith("http");
                      return (
                        <td key={column} className="max-w-[300px] px-3 py-2 align-top">
                          {isUrl ? (
                            <a
                              className="text-primary underline-offset-2 hover:underline"
                              href={String(value)}
                              rel="noreferrer"
                              target="_blank"
                            >
                              Open
                            </a>
                          ) : isStatus ? (
                            <span className={cn("inline-flex border px-1.5 py-0.5 text-[10px]", statusClass(value))}>
                              {formatValue(value, column)}
                            </span>
                          ) : (
                            <span className={column.includes("id") || column.includes("path") ? "font-mono-ui text-[11px]" : ""}>
                              {formatValue(value, column)}
                            </span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
