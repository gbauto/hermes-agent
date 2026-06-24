import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Database,
  GitBranch,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
  XCircle,
  Zap,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Select, SelectOption } from "@nous-research/ui/ui/components/select";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@/components/NouiTypography";
import { api } from "@/lib/api";
import type {
  CronJob,
  GbautoTriageItem,
  ProfileInfo,
  SupabaseCronLogsResponse,
  SupabaseLogRow,
} from "@/lib/api";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { useToast } from "@/hooks/useToast";
import { useConfirmDelete } from "@/hooks/useConfirmDelete";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { Toast } from "@/components/Toast";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { PluginSlot } from "@/plugins";

function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString();
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function valueText(row: SupabaseLogRow, key: string): string {
  const value = row[key];
  if (value === null || value === undefined) return "";
  return String(value);
}

function valueNumber(row: SupabaseLogRow, key: string): number {
  const value = row[key];
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function formatNumber(value: unknown): string {
  const number = typeof value === "number" ? value : Number(value ?? 0);
  if (!Number.isFinite(number)) return "0";
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(number);
}

function formatCurrency(value: unknown): string {
  const number = typeof value === "number" ? value : Number(value ?? 0);
  if (!Number.isFinite(number)) return "$0";
  return new Intl.NumberFormat(undefined, {
    currency: "USD",
    maximumFractionDigits: 4,
    style: "currency",
  }).format(number);
}

function truncateText(value: string, maxLength: number): string {
  return value.length > maxLength
    ? value.slice(0, maxLength) + "..."
    : value;
}

function getJobPrompt(job: CronJob): string {
  return asText(job.prompt);
}

function getJobName(job: CronJob): string {
  return asText(job.name).trim();
}

function getJobTitle(job: CronJob): string {
  const name = getJobName(job);
  if (name) return name;

  const prompt = getJobPrompt(job);
  if (prompt) return truncateText(prompt, 60);

  const script = asText(job.script);
  if (script) return truncateText(script, 60);

  return job.id || "Cron job";
}

function getJobScheduleDisplay(job: CronJob): string {
  return (
    asText(job.schedule_display) ||
    asText(job.schedule?.display) ||
    asText(job.schedule?.expr) ||
    "—"
  );
}

function getJobState(job: CronJob): string {
  return asText(job.state) || (job.enabled === false ? "disabled" : "scheduled");
}

function getJobProfile(job: CronJob): string {
  return asText(job.profile) || asText(job.profile_name) || "default";
}

function getJobKey(job: CronJob): string {
  return `${getJobProfile(job)}:${job.id}`;
}

function splitJobKey(key: string): { profile: string; id: string } {
  const idx = key.indexOf(":");
  if (idx === -1) return { profile: "default", id: key };
  return { profile: key.slice(0, idx) || "default", id: key.slice(idx + 1) };
}

function profileLabel(profile: string): string {
  return profile === "default" ? "default" : profile;
}

const STATUS_TONE: Record<string, "success" | "warning" | "destructive"> = {
  enabled: "success",
  scheduled: "success",
  paused: "warning",
  error: "destructive",
  completed: "destructive",
};

const CRON_OUTPUT_TONE: Record<
  string,
  "success" | "warning" | "destructive" | "secondary" | "outline"
> = {
  error: "destructive",
  failed: "destructive",
  failed_silent: "destructive",
  noop: "secondary",
  ok: "success",
  pr_opened: "success",
  pushed_no_pr: "warning",
  skipped: "warning",
  success: "success",
  triage_created: "success",
};

function getOutputTone(
  status: string,
): "success" | "warning" | "destructive" | "secondary" | "outline" {
  return CRON_OUTPUT_TONE[status.toLowerCase()] ?? "outline";
}

function getOutputStatus(row: SupabaseLogRow): string {
  return valueText(row, "status") || "unknown";
}

function formatSupabaseValue(value: unknown, key = ""): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object") return JSON.stringify(value);
  const text = String(value);
  if (key.includes("_at")) return formatTime(text);
  return text.length > 140 ? `${text.slice(0, 137)}...` : text;
}

function CronMetricCard({
  icon: Icon,
  label,
  sub,
  value,
}: {
  icon: typeof Clock;
  label: string;
  sub?: string;
  value: string | number;
}) {
  return (
    <article className="cron-supabase-stat">
      <Icon className="h-4 w-4" />
      <strong>{value}</strong>
      <span>{label}</span>
      {sub ? <small>{sub}</small> : null}
    </article>
  );
}

function CronRollupCard({ row }: { row: SupabaseLogRow }) {
  const name = valueText(row, "cron_name") || "unnamed cron";
  const failed = valueNumber(row, "failed_outputs");
  return (
    <article className="cron-supabase-rollup-card">
      <header>
        <div>
          <h3>{name}</h3>
          <p>{valueText(row, "hosts") || "host not recorded"}</p>
        </div>
        <Badge tone={failed > 0 ? "destructive" : "success"}>
          {failed > 0 ? `${failed} failed` : "healthy"}
        </Badge>
      </header>
      <dl>
        <div>
          <dt>Runs</dt>
          <dd>{formatNumber(row.runs)}</dd>
        </div>
        <div>
          <dt>Picked</dt>
          <dd>{formatNumber(row.picked_outputs)}</dd>
        </div>
        <div>
          <dt>OK</dt>
          <dd>{formatNumber(row.ok_outputs)}</dd>
        </div>
        <div>
          <dt>Skipped</dt>
          <dd>{formatNumber(row.skipped_outputs)}</dd>
        </div>
        <div>
          <dt>Cost</dt>
          <dd>{formatCurrency(row.cost_usd)}</dd>
        </div>
        <div>
          <dt>Latest</dt>
          <dd>{formatTime(valueText(row, "latest_started_at"))}</dd>
        </div>
      </dl>
    </article>
  );
}

function CronOutputTable({ rows }: { rows: SupabaseLogRow[] }) {
  const columns = [
    "status",
    "cron_name",
    "host",
    "issue_id",
    "branch",
    "duration_s",
    "cost_usd",
    "cron_started_at",
  ];

  if (!rows.length) {
    return (
      <div className="cron-supabase-empty">
        No Supabase cron outputs matched this filter.
      </div>
    );
  }

  return (
    <div className="cron-supabase-table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column.replace(/_/g, " ")}</th>
            ))}
            <th>summary</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const status = getOutputStatus(row);
            const prUrl = valueText(row, "pr_url");
            return (
              <tr
                key={
                  valueText(row, "output_id") ||
                  `${valueText(row, "tick_id")}:${index}`
                }
              >
                {columns.map((column) => (
                  <td key={column}>
                    {column === "status" ? (
                      <Badge tone={getOutputTone(status)}>{status}</Badge>
                    ) : column === "branch" && valueText(row, column) ? (
                      <span className="cron-supabase-branch">
                        <GitBranch className="h-3 w-3" />
                        {formatSupabaseValue(row[column], column)}
                      </span>
                    ) : column === "cost_usd" ? (
                      formatCurrency(row[column])
                    ) : (
                      formatSupabaseValue(row[column], column)
                    )}
                  </td>
                ))}
                <td>
                  <div className="cron-supabase-summary-cell">
                    <span>{formatSupabaseValue(row.agent_summary)}</span>
                    {prUrl ? (
                      <a href={prUrl} target="_blank" rel="noreferrer">
                        PR
                      </a>
                    ) : null}
                    {valueText(row, "stderr_tail") ? (
                      <small>{truncateText(valueText(row, "stderr_tail"), 220)}</small>
                    ) : null}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TriageGatePanel({
  approvingSlug,
  items,
  onApprove,
}: {
  approvingSlug: string;
  items: GbautoTriageItem[];
  onApprove: (slug: string) => void;
}) {
  const gatedItems = items.filter(
    (item) => item.human_gate_required && item.status !== "approved",
  );

  return (
    <section className="cron-triage-panel">
      <header>
        <div>
          <p className="cron-supabase-eyebrow">Smoke-client triage gate</p>
          <h3>Pending approval</h3>
          <p>
            Individual action items from the triage vault. Approve dry-run stages the TAC
            dispatch and verifies the gate without writing Kanban cards.
          </p>
        </div>
        <Badge tone={gatedItems.length ? "warning" : "success"}>
          {gatedItems.length} gated
        </Badge>
      </header>

      {gatedItems.length ? (
        <div className="cron-triage-list">
          {gatedItems.map((item) => (
            <article key={item.slug} className="cron-triage-card">
              <div>
                <div className="cron-triage-card-kicker">
                  <Badge tone={item.status === "approved" ? "success" : "outline"}>
                    {item.status || "unknown"}
                  </Badge>
                  {item.priority ? <span>{item.priority}</span> : null}
                  {item.origin ? <span>{item.origin}</span> : null}
                </div>
                <h4>{item.title}</h4>
                <p>{truncateText(item.summary, 220)}</p>
                <small>{item.slug}</small>
              </div>
              <Button
                size="sm"
                onClick={() => onApprove(item.slug)}
                disabled={approvingSlug === item.slug}
                prefix={approvingSlug === item.slug ? <Spinner /> : <CheckCircle2 />}
              >
                Approve dry-run
              </Button>
            </article>
          ))}
        </div>
      ) : (
        <div className="cron-supabase-empty">No smoke-client triage items are waiting.</div>
      )}
    </section>
  );
}

export default function CronPage() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [profiles, setProfiles] = useState<ProfileInfo[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("all");
  const [loading, setLoading] = useState(true);
  const [supabaseCron, setSupabaseCron] = useState<SupabaseCronLogsResponse | null>(null);
  const [supabaseLoading, setSupabaseLoading] = useState(true);
  const [supabaseError, setSupabaseError] = useState("");
  const [supabaseDays, setSupabaseDays] = useState("14");
  const [supabaseLimit, setSupabaseLimit] = useState("50");
  const [supabaseSearch, setSupabaseSearch] = useState("");
  const [supabaseRepo, setSupabaseRepo] = useState("");
  const [supabaseWorkdir, setSupabaseWorkdir] = useState("");
  const [triageItems, setTriageItems] = useState<GbautoTriageItem[]>([]);
  const [triageError, setTriageError] = useState("");
  const [approvingSlug, setApprovingSlug] = useState("");
  const { toast, showToast } = useToast();
  const { t } = useI18n();
  const { setEnd } = usePageHeader();

  // New job modal state
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [schedule, setSchedule] = useState("");
  const [name, setName] = useState("");
  const closeCreateModal = useCallback(() => setCreateModalOpen(false), []);
  const createModalRef = useModalBehavior({
    open: createModalOpen,
    onClose: closeCreateModal,
  });
  const [deliver, setDeliver] = useState("local");
  const [creating, setCreating] = useState(false);
  const createProfile = selectedProfile === "all" ? "default" : selectedProfile;

  const loadJobs = useCallback(() => {
    setLoading(true);
    api
      .getCronJobs(selectedProfile)
      .then(setJobs)
      .catch(() => showToast(t.common.loading, "error"))
      .finally(() => setLoading(false));
  }, [selectedProfile, showToast, t.common.loading]);

  const loadSupabaseCron = useCallback(() => {
    setSupabaseLoading(true);
    setSupabaseError("");
    api
      .getSupabaseLogsCron({
        days: Number(supabaseDays) || 14,
        limit: Number(supabaseLimit) || 50,
        repo: supabaseRepo.trim() || undefined,
        search: supabaseSearch.trim() || undefined,
        workdir: supabaseWorkdir.trim() || undefined,
      })
      .then((res) => {
        setSupabaseCron(res);
        if (!res.ok) {
          setSupabaseError(res.error || "Supabase cron query failed.");
        }
      })
      .catch((error: unknown) => {
        setSupabaseCron(null);
        setSupabaseError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => setSupabaseLoading(false));
  }, [supabaseDays, supabaseLimit, supabaseRepo, supabaseSearch, supabaseWorkdir]);

  const loadTriageItems = useCallback(() => {
    setTriageError("");
    api
      .getGbautoTriageItems("smoke-client")
      .then((res) => setTriageItems(res.items))
      .catch((error: unknown) => {
        setTriageItems([]);
        setTriageError(error instanceof Error ? error.message : String(error));
      });
  }, []);

  useEffect(() => {
    api
      .getProfiles()
      .then((res) => setProfiles(res.profiles))
      .catch(() => setProfiles([]));
  }, []);

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  useEffect(() => {
    loadSupabaseCron();
  }, [loadSupabaseCron]);

  useEffect(() => {
    loadTriageItems();
  }, [loadTriageItems]);

  const handleApproveTriageDryRun = useCallback(
    async (slug: string) => {
      setApprovingSlug(slug);
      try {
        const result = await api.approveGbautoTriageItem(slug, {
          client: "smoke-client",
          write: false,
        });
        const planned = valueText(result.dispatch?.dispatch as SupabaseLogRow, "stdout");
        showToast(
          result.ok
            ? `Gate approved in dry-run: ${slug}`
            : `Gate returned ${result.mode}: ${slug}`,
          result.ok ? "success" : "error",
        );
        if (planned) {
          console.info("Triage dry-run dispatch", planned);
        }
        loadTriageItems();
      } catch (error) {
        showToast(`${t.status.error}: ${error}`, "error");
      } finally {
        setApprovingSlug("");
      }
    },
    [loadTriageItems, showToast, t.status.error],
  );

  const handleCreate = async () => {
    if (!prompt.trim() || !schedule.trim()) {
      showToast(`${t.cron.prompt} & ${t.cron.schedule} required`, "error");
      return;
    }
    setCreating(true);
    try {
      await api.createCronJob(
        {
          prompt: prompt.trim(),
          schedule: schedule.trim(),
          name: name.trim() || undefined,
          deliver,
        },
        createProfile,
      );
      showToast(t.common.create + " ✓", "success");
      setPrompt("");
      setSchedule("");
      setName("");
      setDeliver("local");
      setCreateModalOpen(false);
      loadJobs();
    } catch (e) {
      showToast(`${t.config.failedToSave}: ${e}`, "error");
    } finally {
      setCreating(false);
    }
  };

  const handlePauseResume = async (job: CronJob) => {
    try {
      const isPaused = getJobState(job) === "paused";
      const profile = getJobProfile(job);
      if (isPaused) {
        await api.resumeCronJob(job.id, profile);
        showToast(
          `${t.cron.resume}: "${truncateText(getJobTitle(job), 30)}"`,
          "success",
        );
      } else {
        await api.pauseCronJob(job.id, profile);
        showToast(
          `${t.cron.pause}: "${truncateText(getJobTitle(job), 30)}"`,
          "success",
        );
      }
      loadJobs();
    } catch (e) {
      showToast(`${t.status.error}: ${e}`, "error");
    }
  };

  const handleTrigger = async (job: CronJob) => {
    try {
      await api.triggerCronJob(job.id, getJobProfile(job));
      showToast(
        `${t.cron.triggerNow}: "${truncateText(getJobTitle(job), 30)}"`,
        "success",
      );
      loadJobs();
    } catch (e) {
      showToast(`${t.status.error}: ${e}`, "error");
    }
  };

  const jobDelete = useConfirmDelete({
    onDelete: useCallback(
      async (key: string) => {
        const { profile, id } = splitJobKey(key);
        const job = jobs.find((j) => getJobKey(j) === key);
        try {
          await api.deleteCronJob(id, profile);
          showToast(
            `${t.common.delete}: "${job ? truncateText(getJobTitle(job), 30) : id}"`,
            "success",
          );
          loadJobs();
        } catch (e) {
          showToast(`${t.status.error}: ${e}`, "error");
          throw e;
        }
      },
      [jobs, loadJobs, showToast, t.common.delete, t.status.error],
    ),
  });

  // Put "Create" button in page header
  useLayoutEffect(() => {
    setEnd(
      <Button
        size="sm"
        onClick={() => setCreateModalOpen(true)}
      >
        <Plus className="h-3 w-3" />
        {t.common.create}
      </Button>,
    );
    return () => {
      setEnd(null);
    };
  }, [setEnd, t.common.create, loading]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  const pendingJob = jobDelete.pendingId
    ? jobs.find((j) => getJobKey(j) === jobDelete.pendingId)
    : null;
  const rollupRows = supabaseCron?.rollup ?? [];
  const outputRows = supabaseCron?.outputs ?? [];
  const totalRuns = rollupRows.reduce((total, row) => total + valueNumber(row, "runs"), 0);
  const totalFailures = rollupRows.reduce(
    (total, row) => total + valueNumber(row, "failed_outputs"),
    0,
  );
  const totalCost = rollupRows.reduce((total, row) => total + valueNumber(row, "cost_usd"), 0);
  const openedPrs = outputRows.filter((row) => valueText(row, "pr_url")).length;

  return (
    <div className="flex flex-col gap-6">
      <PluginSlot name="cron:top" />
      <Toast toast={toast} />

      <DeleteConfirmDialog
        open={jobDelete.isOpen}
        onCancel={jobDelete.cancel}
        onConfirm={jobDelete.confirm}
        title={t.cron.confirmDeleteTitle}
        description={
          pendingJob
            ? `"${truncateText(getJobTitle(pendingJob), 40)}" — ${
                t.cron.confirmDeleteMessage
              }`
            : t.cron.confirmDeleteMessage
        }
        loading={jobDelete.isDeleting}
      />

      {/* Create job modal */}
      {createModalOpen && (
        <div
          ref={createModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 backdrop-blur-sm p-4"
          onClick={(e) => e.target === e.currentTarget && setCreateModalOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-cron-title"
        >
          <div className="relative w-full max-w-lg border border-border bg-card shadow-2xl flex flex-col">
            <Button
              ghost
              size="icon"
              onClick={() => setCreateModalOpen(false)}
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X />
            </Button>

            <header className="p-5 pb-3 border-b border-border">
              <h2
                id="create-cron-title"
                className="font-display text-base tracking-wider uppercase"
              >
                {t.cron.newJob}
              </h2>
            </header>

            <div className="p-5 grid gap-4">
              <div className="grid gap-2">
                <Label htmlFor="cron-profile">Profile</Label>
                <Select
                  id="cron-profile"
                  value={createProfile}
                  onValueChange={(v) => setSelectedProfile(v)}
                >
                  {profiles.map((profile) => (
                    <SelectOption key={profile.name} value={profile.name}>
                      {profileLabel(profile.name)}
                    </SelectOption>
                  ))}
                </Select>
              </div>

              <div className="grid gap-2">
                <Label htmlFor="cron-name">{t.cron.nameOptional}</Label>
                <Input
                  id="cron-name"
                  autoFocus
                  placeholder={t.cron.namePlaceholder}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="cron-prompt">{t.cron.prompt}</Label>
                <textarea
                  id="cron-prompt"
                  className="flex min-h-[80px] w-full border border-border bg-background/40 px-3 py-2 text-sm font-courier shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-foreground/30 focus-visible:border-foreground/25"
                  placeholder={t.cron.promptPlaceholder}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="grid gap-2">
                  <Label htmlFor="cron-schedule">{t.cron.schedule}</Label>
                  <Input
                    id="cron-schedule"
                    placeholder={t.cron.schedulePlaceholder}
                    value={schedule}
                    onChange={(e) => setSchedule(e.target.value)}
                  />
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="cron-deliver">{t.cron.deliverTo}</Label>
                  <Select
                    id="cron-deliver"
                    value={deliver}
                    onValueChange={(v) => setDeliver(v)}
                  >
                    <SelectOption value="local">
                      {t.cron.delivery.local}
                    </SelectOption>
                    <SelectOption value="telegram">
                      {t.cron.delivery.telegram}
                    </SelectOption>
                    <SelectOption value="discord">
                      {t.cron.delivery.discord}
                    </SelectOption>
                    <SelectOption value="slack">
                      {t.cron.delivery.slack}
                    </SelectOption>
                    <SelectOption value="email">
                      {t.cron.delivery.email}
                    </SelectOption>
                  </Select>
                </div>
              </div>

              <div className="flex justify-end">
                <Button
                  size="sm"
                  onClick={handleCreate}
                  disabled={creating}
                  prefix={creating ? <Spinner /> : <Plus />}
                >
                  {creating ? t.common.creating : t.common.create}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      <section className="cron-supabase-panel">
        <header className="cron-supabase-header">
          <div>
            <p className="cron-supabase-eyebrow">Supabase cron evidence</p>
            <h2>Run history and outputs</h2>
            <p>
              Live persisted cron ticks from <code>cron_runs</code> joined to output receipts in{" "}
              <code>cron_run_outputs</code>.
            </p>
          </div>
          <Button
            size="sm"
            onClick={loadSupabaseCron}
            disabled={supabaseLoading}
            prefix={supabaseLoading ? <Spinner /> : <RefreshCw />}
          >
            Refresh
          </Button>
        </header>

        <div className="cron-supabase-toolbar">
          <div className="cron-supabase-search">
            <Search className="h-4 w-4" />
            <input
              aria-label="Search cron evidence"
              placeholder="Search cron, issue, branch, PR, summary..."
              value={supabaseSearch}
              onChange={(event) => setSupabaseSearch(event.target.value)}
            />
          </div>
          <Input
            aria-label="Repo or host filter"
            placeholder="repo / host"
            value={supabaseRepo}
            onChange={(event) => setSupabaseRepo(event.target.value)}
          />
          <Input
            aria-label="Working directory or summary filter"
            placeholder="workdir / summary"
            value={supabaseWorkdir}
            onChange={(event) => setSupabaseWorkdir(event.target.value)}
          />
          <Select
            aria-label="Supabase cron days"
            value={supabaseDays}
            onValueChange={setSupabaseDays}
          >
            <SelectOption value="3">3 days</SelectOption>
            <SelectOption value="7">7 days</SelectOption>
            <SelectOption value="14">14 days</SelectOption>
            <SelectOption value="30">30 days</SelectOption>
          </Select>
          <Select
            aria-label="Supabase cron limit"
            value={supabaseLimit}
            onValueChange={setSupabaseLimit}
          >
            <SelectOption value="25">25 rows</SelectOption>
            <SelectOption value="50">50 rows</SelectOption>
            <SelectOption value="100">100 rows</SelectOption>
            <SelectOption value="250">250 rows</SelectOption>
          </Select>
        </div>

        {supabaseError ? (
          <div className="cron-supabase-error">
            <AlertTriangle className="h-4 w-4" />
            {supabaseError}
          </div>
        ) : null}

        <div className="cron-supabase-stat-grid">
          <CronMetricCard
            icon={Activity}
            label="Runs"
            sub={`${supabaseDays} day window`}
            value={formatNumber(totalRuns)}
          />
          <CronMetricCard
            icon={CheckCircle2}
            label="Outputs"
            sub="output receipts"
            value={formatNumber(outputRows.length)}
          />
          <CronMetricCard
            icon={XCircle}
            label="Failures"
            sub="rollup fail count"
            value={formatNumber(totalFailures)}
          />
          <CronMetricCard
            icon={Database}
            label="Cost"
            sub={`${openedPrs} PR-linked outputs`}
            value={formatCurrency(totalCost)}
          />
        </div>

        {supabaseLoading ? (
          <div className="cron-supabase-loading">
            <Spinner />
            Loading Supabase cron receipts...
          </div>
        ) : (
          <>
            <div className="cron-supabase-rollup-grid">
              {rollupRows.length ? (
                rollupRows.slice(0, 6).map((row, index) => (
                  <CronRollupCard
                    key={valueText(row, "cron_name") || `rollup:${index}`}
                    row={row}
                  />
                ))
              ) : (
                <div className="cron-supabase-empty">
                  No cron rollups returned for the current filters.
                </div>
              )}
            </div>

            <CronOutputTable rows={outputRows.slice(0, Number(supabaseLimit) || 50)} />
          </>
        )}
      </section>

      {triageError ? (
        <div className="cron-supabase-error">
          <AlertTriangle className="h-4 w-4" />
          {triageError}
        </div>
      ) : null}

      <TriageGatePanel
        approvingSlug={approvingSlug}
        items={triageItems}
        onApprove={handleApproveTriageDryRun}
      />

      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <H2
            variant="sm"
            className="flex items-center gap-2 text-muted-foreground"
          >
            <Clock className="h-4 w-4" />
            {t.cron.scheduledJobs} ({jobs.length})
          </H2>

          <div className="grid gap-1 min-w-[220px]">
            <Label htmlFor="cron-profile-filter">Profile</Label>
            <Select
              id="cron-profile-filter"
              value={selectedProfile}
              onValueChange={(v) => setSelectedProfile(v)}
            >
              <SelectOption value="all">All profiles</SelectOption>
              {profiles.map((profile) => (
                <SelectOption key={profile.name} value={profile.name}>
                  {profileLabel(profile.name)}
                </SelectOption>
              ))}
            </Select>
          </div>
        </div>

        {jobs.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              {t.cron.noJobs}
            </CardContent>
          </Card>
        )}

        {jobs.map((job) => {
          const state = getJobState(job);
          const promptText = getJobPrompt(job);
          const title = getJobTitle(job);
          const hasName = Boolean(getJobName(job));
          const deliver = asText(job.deliver);
          const profile = getJobProfile(job);
          const jobKey = getJobKey(job);

          return (
            <Card key={jobKey}>
              <CardContent className="flex items-start gap-4 py-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium text-sm truncate">
                      {title}
                    </span>
                    <Badge tone={STATUS_TONE[state] ?? "secondary"}>
                      {state}
                    </Badge>
                    <Badge tone="outline">{profileLabel(profile)}</Badge>
                    {deliver && deliver !== "local" && (
                      <Badge tone="outline">{deliver}</Badge>
                    )}
                  </div>
                  {hasName && promptText && (
                    <p className="text-xs text-muted-foreground truncate mb-1">
                      {truncateText(promptText, 100)}
                    </p>
                  )}
                  <div className="flex items-center gap-4 text-xs text-muted-foreground">
                    <span className="font-mono">{getJobScheduleDisplay(job)}</span>
                    <span>
                      {t.cron.last}: {formatTime(job.last_run_at)}
                    </span>
                    <span>
                      {t.cron.next}: {formatTime(job.next_run_at)}
                    </span>
                  </div>
                  {job.last_error && (
                    <p className="text-xs text-destructive mt-1">
                      {job.last_error}
                    </p>
                  )}
                </div>

                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    ghost
                    size="icon"
                    title={state === "paused" ? t.cron.resume : t.cron.pause}
                    aria-label={
                      state === "paused" ? t.cron.resume : t.cron.pause
                    }
                    onClick={() => handlePauseResume(job)}
                    className={
                      state === "paused" ? "text-success" : "text-warning"
                    }
                  >
                    {state === "paused" ? <Play /> : <Pause />}
                  </Button>

                  <Button
                    ghost
                    size="icon"
                    title={t.cron.triggerNow}
                    aria-label={t.cron.triggerNow}
                    onClick={() => handleTrigger(job)}
                  >
                    <Zap />
                  </Button>

                  <Button
                    ghost
                    destructive
                    size="icon"
                    title={t.common.delete}
                    aria-label={t.common.delete}
                    onClick={() => jobDelete.requestDelete(jobKey)}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <PluginSlot name="cron:bottom" />
    </div>
  );
}
