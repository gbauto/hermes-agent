import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { CSSProperties } from "react";
import {
  ArrowRight,
  ArrowLeft,
  BookOpen,
  ChevronDown,
  ExternalLink,
  FileText,
  Filter,
  GitBranch,
  LayoutGrid,
  Pencil,
  Plus,
  Sparkles,
  Terminal,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { Link, Navigate, useParams } from "react-router-dom";
import spinners from "unicode-animations";
import { H2 } from "@/components/NouiTypography";
import { api } from "@/lib/api";
import type { AgentProfileCatalogResponse, AgentProfileCatalogRow, AgentProfileRouteRow, AgentProfileTeamRow, ProfileInfo, SupabaseLogRow } from "@/lib/api";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { useToast } from "@/hooks/useToast";
import { useConfirmDelete } from "@/hooks/useConfirmDelete";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { Toast } from "@/components/Toast";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { ListItem } from "@nous-research/ui/ui/components/list-item";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { gbautoLibrary } from "@/generated/gbautoLibrary";
import { useTenant } from "@/lib/tenant";

const PROFILE_ART_VERSION = "tac-angels-20260623";

// Mirrors hermes_cli/profiles.py::_PROFILE_ID_RE so we can reject obviously
// invalid names (uppercase, spaces, …) before round-tripping a doomed POST.
const PROFILE_NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

/** Braille unicode spinner (`unicode-animations`); static first frame when reduced motion is preferred. */
function ProfilesLoadingSpinner() {
  const { frames, interval } = spinners.braille;
  const [frameIndex, setFrameIndex] = useState(0);

  useEffect(() => {
    if (
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      return;
    }
    const id = window.setInterval(
      () => setFrameIndex((i) => (i + 1) % frames.length),
      interval,
    );
    return () => window.clearInterval(id);
  }, [frames.length, interval]);

  return (
    <span
      aria-hidden
      className="inline-block select-none font-mono text-xl leading-none text-muted-foreground"
    >
      {frames[frameIndex]}
    </span>
  );
}

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<ProfileInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [libraryView, setLibraryView] = useState<ProfileLibraryView>("teams");
  const { toast, showToast } = useToast();
  const { t } = useI18n();
  const { setEnd } = usePageHeader();
  const tenant = useTenant();
  const liveProfileCatalog = useLiveAgentProfileCatalog(tenant);

  // Create modal
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [cloneFromDefault, setCloneFromDefault] = useState(true);
  const [creating, setCreating] = useState(false);
  const closeCreateModal = useCallback(() => setCreateModalOpen(false), []);

  // Inline rename state
  const [renamingFrom, setRenamingFrom] = useState<string | null>(null);
  const [renameTo, setRenameTo] = useState("");

  // Inline SOUL editor state
  const [editingSoulFor, setEditingSoulFor] = useState<string | null>(null);
  const [soulText, setSoulText] = useState("");
  const [soulSaving, setSoulSaving] = useState(false);
  // Tracks the latest SOUL request so out-of-order responses don't overwrite
  // newer state when the user switches profiles or closes the editor.
  const activeSoulRequest = useRef<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api
      .getProfiles()
      .then((res) => setProfiles(res.profiles))
      .catch((e) => showToast(`${t.status.error}: ${e}`, "error"))
      .finally(() => setLoading(false));
  }, [showToast, t.status.error]);

  useEffect(() => {
    if (libraryView !== "admin" || profiles.length !== 0) return;
    const id = window.setTimeout(load, 0);
    return () => window.clearTimeout(id);
  }, [libraryView, load, profiles.length]);

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) {
      showToast(t.profiles.nameRequired, "error");
      return;
    }
    if (!PROFILE_NAME_RE.test(name)) {
      showToast(`${t.profiles.invalidName}: ${t.profiles.nameRule}`, "error");
      return;
    }
    setCreating(true);
    try {
      await api.createProfile({ name, clone_from_default: cloneFromDefault });
      showToast(`${t.profiles.created}: ${name}`, "success");
      setNewName("");
      setCreateModalOpen(false);
      load();
    } catch (e) {
      showToast(`${t.status.error}: ${e}`, "error");
    } finally {
      setCreating(false);
    }
  };

  const createModalRef = useModalBehavior({
    open: createModalOpen,
    onClose: closeCreateModal,
    onSubmit: () => {
      void handleCreate();
    },
    canSubmit: !creating && !!newName.trim(),
    focusSelector: "#profile-name",
  });

  const handleRenameSubmit = async () => {
    if (!renamingFrom) return;
    const target = renameTo.trim();
    if (!target || target === renamingFrom) {
      setRenamingFrom(null);
      setRenameTo("");
      return;
    }
    if (!PROFILE_NAME_RE.test(target)) {
      showToast(`${t.profiles.invalidName}: ${t.profiles.nameRule}`, "error");
      return;
    }
    try {
      await api.renameProfile(renamingFrom, target);
      showToast(
        `${t.profiles.renamed}: ${renamingFrom} → ${target}`,
        "success",
      );
      setRenamingFrom(null);
      setRenameTo("");
      load();
    } catch (e) {
      showToast(`${t.status.error}: ${e}`, "error");
    }
  };

  const openSoulEditor = useCallback(
    async (name: string) => {
      if (editingSoulFor === name) {
        activeSoulRequest.current = null;
        setEditingSoulFor(null);
        return;
      }
      setEditingSoulFor(name);
      setSoulText("");
      activeSoulRequest.current = name;
      try {
        const soul = await api.getProfileSoul(name);
        if (activeSoulRequest.current === name) {
          setSoulText(soul.content);
        }
      } catch (e) {
        if (activeSoulRequest.current === name) {
          showToast(`${t.status.error}: ${e}`, "error");
        }
      }
    },
    [editingSoulFor, showToast, t.status.error],
  );

  const handleSaveSoul = async (name: string) => {
    setSoulSaving(true);
    try {
      await api.updateProfileSoul(name, soulText);
      showToast(`${t.profiles.soulSaved}: ${name}`, "success");
    } catch (e) {
      showToast(`${t.status.error}: ${e}`, "error");
    } finally {
      setSoulSaving(false);
    }
  };

  const handleCopyTerminalCommand = async (name: string) => {
    let cmd: string;
    try {
      const res = await api.getProfileSetupCommand(name);
      cmd = res.command;
    } catch (e) {
      showToast(`${t.status.error}: ${e}`, "error");
      return;
    }
    try {
      await navigator.clipboard.writeText(cmd);
      showToast(`${t.profiles.commandCopied}: ${cmd}`, "success");
    } catch {
      showToast(`${t.profiles.copyFailed}: ${cmd}`, "error");
    }
  };

  const profileDelete = useConfirmDelete<string>({
    onDelete: useCallback(
      async (name: string) => {
        try {
          await api.deleteProfile(name);
          showToast(`${t.profiles.deleted}: ${name}`, "success");
          load();
        } catch (e) {
          showToast(`${t.status.error}: ${e}`, "error");
          throw e;
        }
      },
      [load, showToast, t.profiles.deleted, t.status.error],
    ),
  });

  const pendingName = profileDelete.pendingId;

  // Put "Create" button in page header
  useLayoutEffect(() => {
    if (libraryView !== "admin") {
      setEnd(null);
      return;
    }
    setEnd(
      <Button size="sm" onClick={() => setCreateModalOpen(true)}>
        <Plus className="h-3 w-3" />
        {t.common.create}
      </Button>,
    );
    return () => {
      setEnd(null);
    };
  }, [setEnd, t.common.create, loading, libraryView]);

  const tacProfiles = useMemo(
    () =>
      gbautoLibrary.profiles.filter((profile) => profile.team === "tac-hermes"),
    [],
  );
  const activeProfileTeams = useMemo(() => profileTeamsForTenant(tenant, liveProfileCatalog), [liveProfileCatalog, tenant]);
  const activeProfileRows = useMemo(
    () => profileRowsForTenant(tenant, liveProfileCatalog),
    [liveProfileCatalog, tenant],
  );

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-live="polite"
        className="flex items-center justify-center py-24"
      >
        <span className="sr-only">{t.common.loading}</span>

        <ProfilesLoadingSpinner />
      </div>
    );
  }

  return (
    // Profile names, model slugs, and paths are case-sensitive; opt out of
    // the app shell's global ``uppercase`` so they render as the user typed.
    // Children that explicitly opt back in (Badges, etc.) keep their casing.
    <div className="flex flex-col gap-6 normal-case">
      <Toast toast={toast} />

      <DeleteConfirmDialog
        open={profileDelete.isOpen}
        onCancel={profileDelete.cancel}
        onConfirm={profileDelete.confirm}
        title={t.profiles.confirmDeleteTitle}
        description={
          pendingName
            ? t.profiles.confirmDeleteMessage.replace("{name}", pendingName)
            : t.profiles.confirmDeleteMessage
        }
        loading={profileDelete.isDeleting}
      />

      {/* Create profile modal */}
      {createModalOpen && (
        <div
          ref={createModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 backdrop-blur-sm p-4"
          onClick={(e) =>
            e.target === e.currentTarget && setCreateModalOpen(false)
          }
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-profile-title"
        >
          <div className="relative w-full max-w-md border border-border bg-card shadow-2xl flex flex-col">
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
                id="create-profile-title"
                className="font-display text-base tracking-wider uppercase"
              >
                {t.profiles.newProfile}
              </h2>
            </header>

            <div className="p-5 grid gap-4">
              <div className="grid gap-2">
                <Label htmlFor="profile-name">{t.profiles.name}</Label>
                <Input
                  id="profile-name"
                  autoFocus
                  placeholder={t.profiles.namePlaceholder}
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleCreate();
                  }}
                  aria-invalid={
                    newName.trim() !== "" &&
                    !PROFILE_NAME_RE.test(newName.trim())
                  }
                />
                <p className="text-xs text-muted-foreground">
                  {t.profiles.nameRule}
                </p>
              </div>

              <div className="flex items-center gap-2.5">
                <Checkbox
                  checked={cloneFromDefault}
                  id="clone-from-default"
                  onCheckedChange={(checked) =>
                    setCloneFromDefault(checked === true)
                  }
                />

                <Label
                  className="font-sans normal-case tracking-normal text-sm cursor-pointer"
                  htmlFor="clone-from-default"
                >
                  {t.profiles.cloneFromDefault}
                </Label>
              </div>

              <div className="flex justify-end">
                <Button size="sm" onClick={handleCreate} disabled={creating}>
                  <Plus className="h-3 w-3" />
                  {creating ? t.common.creating : t.common.create}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <aside aria-label="Profile views" className="sm:w-56 sm:shrink-0">
          <div className="sm:sticky sm:top-0">
            <div className="flex flex-col rounded-none border border-border bg-muted/20">
              <div className="hidden items-center gap-2 border-b border-border px-3 py-2 sm:flex">
                <Filter className="h-3 w-3 text-muted-foreground" />
                <span className="font-mondwest text-[0.65rem] uppercase tracking-[0.12em] text-muted-foreground">
                  Profiles
                </span>
              </div>
              <div className="flex gap-1 overflow-x-auto p-2 scrollbar-none sm:flex-col sm:overflow-x-visible">
                <ProfilePanelItem
                  active={libraryView === "teams"}
                  icon={LayoutGrid}
                  label={`Teams (${activeProfileTeams.length})`}
                  onClick={() => setLibraryView("teams")}
                />
                <ProfilePanelItem
                  active={libraryView === "profiles"}
                  icon={BookOpen}
                  label={`Index (${activeProfileRows.length})`}
                  onClick={() => setLibraryView("profiles")}
                />
                <ProfilePanelItem
                  active={libraryView === "tac"}
                  icon={Sparkles}
                  label="TAC"
                  onClick={() => setLibraryView("tac")}
                />
                <ProfilePanelItem
                  active={libraryView === "admin"}
                  icon={Users}
                  label={`Admin${profiles.length ? ` (${profiles.length})` : ""}`}
                  onClick={() => setLibraryView("admin")}
                />
              </div>
              <div className="hidden border-t border-border px-3 py-2 text-[11px] leading-relaxed text-muted-foreground sm:block">
                <strong className="block text-foreground">{tenant}</strong>
                Profile rosters and runtime evidence are scoped from Supabase; TAC and Core templates remain shared.
              </div>
            </div>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <ProfileRuntimeEvidence
            profileId={libraryView === "tac" ? "tac" : undefined}
            teamId={libraryView === "teams" ? activeProfileTeams[0]?.id : undefined}
          />

          {libraryView === "teams" && (
            <ProfileTeamsShowcase
              profileTeams={activeProfileTeams}
              tacProfiles={tacProfiles}
              tenant={tenant}
            />
          )}

          {libraryView === "profiles" && (
            <ProfileIndexShowcase
              profiles={activeProfileRows}
              source={liveProfileCatalog?.source ?? "generated:gbautoLibrary"}
              tenant={tenant}
            />
          )}

          {libraryView === "tac" && <TacLeadVariants />}

          {/* List */}
          {libraryView === "admin" && (
      <div className="flex flex-col gap-3">
        <H2
          variant="sm"
          className="flex items-center gap-2 text-muted-foreground"
        >
          <Users className="h-4 w-4" />
          {t.profiles.allProfiles} ({profiles.length})
        </H2>

        {profiles.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              {t.profiles.noProfiles}
            </CardContent>
          </Card>
        )}

        {profiles.map((p) => {
          const isRenaming = renamingFrom === p.name;
          const isEditingSoul = editingSoulFor === p.name;
          return (
            <Card key={p.name}>
              <CardContent className="flex items-start gap-4 py-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    {isRenaming ? (
                      <Input
                        autoFocus
                        value={renameTo}
                        onChange={(e) => setRenameTo(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleRenameSubmit();
                          if (e.key === "Escape") setRenamingFrom(null);
                        }}
                        aria-invalid={
                          renameTo.trim() !== "" &&
                          renameTo.trim() !== p.name &&
                          !PROFILE_NAME_RE.test(renameTo.trim())
                        }
                        className="max-w-xs"
                      />
                    ) : (
                      <span className="font-medium text-sm truncate">
                        {p.name}
                      </span>
                    )}
                    {p.is_default && (
                      <Badge tone="secondary">{t.profiles.defaultBadge}</Badge>
                    )}
                    {p.has_env && (
                      <Badge tone="outline">{t.profiles.hasEnv}</Badge>
                    )}
                    {p.canopy && (
                      <span
                        title={
                          p.canopy_inherits && p.canopy_inherits.length
                            ? `Canopy-composed · inherits: ${p.canopy_inherits.join(", ")}`
                            : "Canopy-composed profile"
                        }
                      >
                        <Badge tone="outline">
                          🌿 Canopy
                          {p.canopy_inherits && p.canopy_inherits.length
                            ? ` · ${p.canopy_inherits.length}`
                            : ""}
                        </Badge>
                      </span>
                    )}
                  </div>
                  {isRenaming &&
                    (() => {
                      const trimmed = renameTo.trim();
                      const invalid =
                        trimmed !== "" &&
                        trimmed !== p.name &&
                        !PROFILE_NAME_RE.test(trimmed);
                      return (
                        <p
                          className={
                            "text-xs mb-1 " +
                            (invalid
                              ? "text-destructive"
                              : "text-muted-foreground")
                          }
                        >
                          {invalid
                            ? `${t.profiles.invalidName}: ${t.profiles.nameRule}`
                            : t.profiles.nameRule}
                        </p>
                      );
                    })()}
                  <div className="flex items-center gap-4 text-xs text-muted-foreground flex-wrap">
                    {p.model && (
                      <span>
                        {t.profiles.model}: {p.model}
                        {p.provider ? ` (${p.provider})` : ""}
                      </span>
                    )}
                    <span>
                      {t.profiles.skills}: {p.skill_count}
                    </span>
                    <span className="font-mono truncate max-w-[28rem]">
                      {p.path}
                    </span>
                  </div>
                </div>

                <div className="flex items-center gap-1 shrink-0">
                  {isRenaming ? (
                    <>
                      <Button size="sm" onClick={handleRenameSubmit}>
                        {t.common.save}
                      </Button>
                      <Button
                        size="sm"
                        ghost
                        onClick={() => setRenamingFrom(null)}
                      >
                        {t.common.cancel}
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button
                        ghost
                        size="icon"
                        title={t.profiles.editSoul}
                        aria-label={t.profiles.editSoul}
                        onClick={() => openSoulEditor(p.name)}
                      >
                        {isEditingSoul ? (
                          <ChevronDown className="h-4 w-4" />
                        ) : (
                          <span aria-hidden className="text-xs font-bold">
                            S
                          </span>
                        )}
                      </Button>
                      <Button
                        ghost
                        size="icon"
                        title={t.profiles.openInTerminal}
                        aria-label={t.profiles.openInTerminal}
                        onClick={() => handleCopyTerminalCommand(p.name)}
                      >
                        <Terminal className="h-4 w-4" />
                      </Button>
                      {!p.is_default && (
                        <Button
                          ghost
                          size="icon"
                          title={t.profiles.rename}
                          aria-label={t.profiles.rename}
                          onClick={() => {
                            setRenamingFrom(p.name);
                            setRenameTo(p.name);
                          }}
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                      )}
                      {!p.is_default && (
                        <Button
                          ghost
                          size="icon"
                          title={t.common.delete}
                          aria-label={t.common.delete}
                          onClick={() => profileDelete.requestDelete(p.name)}
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      )}
                    </>
                  )}
                </div>
              </CardContent>

              {isEditingSoul && (
                <div className="border-t border-border px-4 pb-4 pt-3 flex flex-col gap-2">
                  <Label
                    htmlFor={`soul-editor-${p.name}`}
                    className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground"
                  >
                    {t.profiles.soulSection}
                  </Label>
                  <textarea
                    id={`soul-editor-${p.name}`}
                    className="flex min-h-[180px] w-full border border-input bg-transparent px-3 py-2 text-sm font-mono shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    placeholder={t.profiles.soulPlaceholder}
                    value={soulText}
                    onChange={(e) => setSoulText(e.target.value)}
                  />
                  <div>
                    <Button
                      size="sm"
                      onClick={() => handleSaveSoul(p.name)}
                      disabled={soulSaving}
                    >
                      {soulSaving ? t.common.saving : t.profiles.saveSoul}
                    </Button>
                  </div>
                </div>
              )}
            </Card>
          );
        })}
      </div>
      )}
        </div>
      </div>
    </div>
  );
}

type ProfileLibraryView = "teams" | "profiles" | "tac" | "admin";
type HermesProfile = (typeof gbautoLibrary.profiles)[number];
type LibraryAgent = (typeof gbautoLibrary.agents)[number];
type ProfileIndexEntry = {
  id: string;
  displayName: string;
  role?: string | null;
  team?: string | null;
  model?: string | null;
  runtime?: string | null;
  sourcePath?: string | null;
  skillCount?: number | null;
  routeCount?: number | null;
  localProfile?: HermesProfile;
  liveProfile?: AgentProfileCatalogRow;
};
type ProfileTeamCatalogEntry = {
  agents?: string[];
  agentTeamId?: string;
  artSeed: string;
  clientSlug?: string;
  displayName: string;
  id: string;
  liveProfiles?: AgentProfileCatalogRow[];
  metadata?: Record<string, unknown>;
  profiles: string[];
  purpose: string;
  routes?: AgentProfileRouteRow[];
  runtime: string;
  sourcePath: string;
};

function useLiveAgentProfileCatalog(tenant: string) {
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

function agentIdsForTeam(teamId: string) {
  return gbautoLibrary.agents
    .filter((agent) => agent.team === teamId)
    .map((agent) => agent.id);
}

function agentTeamIdFromPath(value?: string | null) {
  return String(value ?? "")
    .replace(/^ai-library\/teams\//, "")
    .replace(/\/$/, "");
}

function teamProfileCount(team: ProfileTeamCatalogEntry) {
  return team.profiles.length;
}

function teamAgentCount(team: ProfileTeamCatalogEntry) {
  return team.agents?.length ?? 0;
}

function agentsForTeamEntry(team: ProfileTeamCatalogEntry): LibraryAgent[] {
  return (team.agents ?? [])
    .map((agentId) => gbautoLibrary.agents.find((agent) => agent.id === agentId))
    .filter((agent): agent is LibraryAgent => Boolean(agent));
}

function libraryProfileById(profileId: string) {
  return gbautoLibrary.profiles.find((profile) => profile.id === profileId);
}

function profileModelLabel(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function liveProfileLabel(profileId: string, team: ProfileTeamCatalogEntry) {
  const liveProfile = team.liveProfiles?.find((profile) => profile.profile_id === profileId);
  return liveProfile?.display_name || profileId;
}

function routeMetadataValue(route: AgentProfileRouteRow, key: string) {
  const value = route.metadata?.[key];
  return typeof value === "string" ? value : undefined;
}

function routesForTeam(
  team: AgentProfileTeamRow,
  routes: readonly AgentProfileRouteRow[],
) {
  return routes.filter(
    (route) =>
      route.source_path === team.source_path ||
      routeMetadataValue(route, "team_id") === team.team_id,
  );
}

function isCoverageTeam(team: ProfileTeamCatalogEntry) {
  return team.metadata?.team_kind === "coverage";
}

function isDeployedTelegramFleet(team: ProfileTeamCatalogEntry) {
  return team.metadata?.deployed_telegram_fleet === true;
}

function clientSlugForTeamId(teamId: string) {
  if (teamId.startsWith("ecom")) return "ecom";
  if (teamId.startsWith("jason")) return "jid5274";
  return undefined;
}

function uniqueStrings(values: Array<string | null | undefined>) {
  return Array.from(new Set(values.map((value) => String(value ?? "").trim()).filter(Boolean)));
}

function profileTeamFromSupabase(
  team: AgentProfileTeamRow,
  profiles: readonly AgentProfileCatalogRow[],
  routes: readonly AgentProfileRouteRow[],
): ProfileTeamCatalogEntry {
  const agentTeamId = agentTeamIdFromPath(team.canonical_agent_team);
  const specialistProfiles = team.specialist_profiles ?? [];
  const existingSpecialistProfiles = team.existing_specialist_profiles ?? [];
  const teamProfiles = profiles.filter(
    (profile) =>
      profile.team_id === team.team_id ||
      profile.profile_id === team.orchestrator_profile ||
      profile.profile_id === team.lead_profile ||
      specialistProfiles.includes(profile.profile_id) ||
      existingSpecialistProfiles.includes(profile.profile_id),
  );
  return {
    id: team.team_id,
    displayName: team.display_name,
    runtime: team.runtime || "hermes",
    clientSlug: team.tenant || clientSlugForTeamId(team.team_id),
    purpose: team.purpose || "Profile team indexed from the Supabase/repo profile-team readback.",
    sourcePath: team.source_path || "supabase:agent_profile_teams",
    profiles: uniqueStrings(teamProfiles.map((profile) => profile.profile_id)),
    liveProfiles: teamProfiles,
    routes: routesForTeam(team, routes),
    metadata: team.metadata,
    agents: agentTeamId ? agentIdsForTeam(agentTeamId) : [],
    agentTeamId: agentTeamId || undefined,
    artSeed: team.team_id,
  };
}

const GBAUTO_CORE_PROFILE_IDS = new Set([
  "artifact-manager",
  "browser-manager",
  "database-manager",
  "gbauto-ceo",
  "gbautomation",
  "git-manager",
  "gmail-manager",
  "google-workspace-manager",
  "observability-manager",
  "report-manager",
  "sprint-manager",
  "supabase-manager",
  "telegram-discord-manager",
]);

const PROFILE_TEAM_OVERLAYS: ProfileTeamCatalogEntry[] = [
  {
    id: "aws-cloud-ops",
    displayName: "AWS Cloud Operations",
    runtime: "hermes",
    purpose: "Operate AWS accounts and microservices with admin/security, platform release, and data/observability profiles.",
    sourcePath: "resources/skills/hermes-profile-templates/profile-teams/aws-cloud-ops.yaml",
    profiles: ["aws-admin-security", "aws-platform-release", "aws-data-observability"],
    artSeed: "aws-cloud-ops",
  },
  {
    id: "browser-automation",
    displayName: "Browser Automation",
    runtime: "hermes",
    purpose: "Run reusable browser workflows through Hermes with scraping, authenticated operation, evidence, and policy gates.",
    sourcePath: "resources/skills/hermes-profile-templates/profile-teams/browser-automation.yaml",
    profiles: ["browser-manager"],
    artSeed: "browser-automation",
  },
  {
    id: "ecom-intelligence",
    displayName: "Ecom Intelligence",
    runtime: "hermes",
    clientSlug: "ecom",
    purpose: "Run ecommerce scanner workflows through Hermes with source discovery, browser runs, Supabase contracts, and reports.",
    sourcePath: "resources/skills/hermes-profile-templates/profile-teams/ecom-intelligence.yaml",
    profiles: [
      "ecom",
      "browser-manager",
      "ecom-supabase-datafeed-manager",
      "supabase-manager",
      "database-manager",
      "observability-manager",
      "report-manager",
      "sprint-manager",
      "telegram-discord-manager",
    ],
    agents: agentIdsForTeam("ecom-intelligence"),
    agentTeamId: "ecom-intelligence",
    artSeed: "ecom-intelligence",
  },
  {
    id: "jason-operations",
    displayName: "Jason Operations",
    runtime: "hermes",
    clientSlug: "jid5274",
    purpose: "Run Jason Diaz's daily operations through Carlos plus communication, research, content, knowledge, and delivery specialists.",
    sourcePath: "resources/skills/hermes-profile-templates/profile-teams/jason-operations.yaml",
    profiles: [
      "jason-diaz",
      "chief-of-staff",
      "gmail-manager",
      "google-workspace-manager",
      "content-creator",
      "research-analyst",
      "sales-agent",
      "knowledge-manager",
      "client-delivery-manager",
      "telegram-discord-manager",
    ],
    artSeed: "jason-operations",
  },
  {
    id: "jason-marketing",
    displayName: "Jason Marketing",
    runtime: "hermes",
    clientSlug: "jid5274",
    purpose: "Plan, draft, and review Jason Diaz marketing work: LinkedIn posts, outreach copy, prospect research, campaign ideas, and follow-up drafts.",
    sourcePath: "resources/skills/hermes-profile-templates/profile-teams/jason-marketing.yaml",
    profiles: [
      "jason-diaz",
      "content-creator",
      "research-analyst",
      "sales-agent",
      "gmail-manager",
      "knowledge-manager",
    ],
    artSeed: "jason-marketing",
  },
];

function profileTeamCatalog(liveCatalog?: AgentProfileCatalogResponse | null): ProfileTeamCatalogEntry[] {
  const generated = gbautoLibrary.profileTeams.map((team) => ({
    id: team.id,
    displayName: team.displayName,
    runtime: team.runtime,
    purpose: team.purpose,
    sourcePath: team.sourcePath,
    profiles: [...team.profiles],
    agents: "agents" in team && Array.isArray(team.agents) ? [...team.agents] : [],
    agentTeamId: "agentTeamId" in team && typeof team.agentTeamId === "string" ? team.agentTeamId : undefined,
    artSeed: team.artSeed,
  }));
  const live = (liveCatalog?.teams ?? []).map((team) =>
    profileTeamFromSupabase(team, liveCatalog?.profiles ?? [], liveCatalog?.routes ?? []),
  );
  const generatedIds = new Set<string>([
    ...live.map((team) => team.id),
    ...generated.map((team) => team.id),
  ]);
  return [
    ...live,
    ...generated.filter((team) => !live.some((liveTeam) => liveTeam.id === team.id)),
    ...PROFILE_TEAM_OVERLAYS.filter((team) => !generatedIds.has(team.id)),
  ];
}

function profileTeamsForTenant(tenant: string, liveCatalog?: AgentProfileCatalogResponse | null) {
  const teams = profileTeamCatalog(liveCatalog);
  if (tenant === "gbautomation") return teams;
  return teams
    .filter(
      (team) =>
        team.clientSlug === tenant ||
        team.id === "tac-hermes" ||
        team.id === "browser-automation",
    )
    .sort((a, b) => {
      const rank = (team: ProfileTeamCatalogEntry) =>
        team.clientSlug === tenant ? 0 : team.id === "tac-hermes" ? 1 : 2;
      return rank(a) - rank(b) || a.displayName.localeCompare(b.displayName);
    });
}

function profileRowsForTenant(
  tenant: string,
  liveCatalog?: AgentProfileCatalogResponse | null,
): ProfileIndexEntry[] {
  if (liveCatalog?.profiles?.length) {
    return liveCatalog.profiles.map((profile) => {
      const localProfile = libraryProfileById(profile.profile_id);
      return {
        id: profile.profile_id,
        displayName: profile.display_name || localProfile?.displayName || profile.profile_id,
        role: profile.role || localProfile?.role,
        team: profile.team_id || localProfile?.team || profile.profile_type,
        model: profileModelLabel(profile.model) || profileModelLabel(localProfile?.model),
        runtime: profile.runtime || localProfile?.runtime,
        sourcePath: profile.source_path || localProfile?.sourcePath,
        skillCount: profile.skill_count,
        routeCount: profile.route_count,
        localProfile,
        liveProfile: profile,
      };
    });
  }

  if (tenant === "gbautomation") {
    return gbautoLibrary.profiles.map((profile) => ({
      id: profile.id,
      displayName: profile.displayName,
      role: profile.role,
      team: profile.team,
      model: profileModelLabel(profile.model),
      runtime: profile.runtime,
      sourcePath: profile.sourcePath,
      skillCount: profile.primaryTools.length,
      routeCount: profile.operatingModes.length,
      localProfile: profile,
    }));
  }

  return gbautoLibrary.profiles
    .filter((profile) => profile.team === tenant || profile.id.startsWith(`${tenant}-`))
    .map((profile) => ({
      id: profile.id,
      displayName: profile.displayName,
      role: profile.role,
      team: profile.team,
      model: profileModelLabel(profile.model),
      runtime: profile.runtime,
      sourcePath: profile.sourcePath,
      skillCount: profile.primaryTools.length,
      routeCount: profile.operatingModes.length,
      localProfile: profile,
    }));
}

function isGbautoCoreProfile(profile: HermesProfile) {
  return GBAUTO_CORE_PROFILE_IDS.has(profile.id);
}

function profileFromRun(row: SupabaseLogRow) {
  return String(row.profile ?? row.assignee ?? "unknown");
}

function runStatus(row: SupabaseLogRow) {
  return String(row.status_family ?? row.status ?? "").toLowerCase();
}

function formatRouteLabel(value: string) {
  return value.replace(/_/g, " ");
}

function routeTargetLabel(route: AgentProfileRouteRow) {
  const target =
    route.target_profile_id ||
    route.target_team_id ||
    route.target_profile_key ||
    "unassigned";
  const policy = routeMetadataValue(route, "route_policy") ?? "";
  const routeName = route.route_name.toLowerCase();
  const routesToTac =
    target === "carlos" &&
    (policy.toLowerCase().includes("tac") ||
      routeName.includes("coding") ||
      routeName.includes("build") ||
      routeName.includes("repo") ||
      routeName.includes("deploy"));
  return routesToTac ? "carlos -> TAC" : target;
}

function uniqueRoutes(routes: readonly AgentProfileRouteRow[]) {
  const seen = new Set<string>();
  return routes.filter((route) => {
    const key = `${route.route_name}:${route.target_type}:${route.target_profile_id}:${route.target_team_id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function ProfileRouteMap({
  compact = false,
  max,
  routes,
  team,
}: {
  compact?: boolean;
  max?: number;
  routes: readonly AgentProfileRouteRow[];
  team?: ProfileTeamCatalogEntry;
}) {
  const visibleRoutes = uniqueRoutes(routes);
  const displayedRoutes = max ? visibleRoutes.slice(0, max) : visibleRoutes;
  const remaining = Math.max(visibleRoutes.length - displayedRoutes.length, 0);

  if (!visibleRoutes.length) {
    return compact ? null : (
      <article className="profile-route-map">
        <div className="library-card-topline">
          <span>Routes</span>
          <span>0</span>
        </div>
        <p>No Supabase route rows are indexed for this profile team yet.</p>
      </article>
    );
  }

  return (
    <article className={compact ? "profile-route-map is-compact" : "profile-route-map"}>
      <div className="profile-route-map-header">
        <div>
          <span className="library-eyebrow">Route Map</span>
          {!compact && <h3>{team ? `${team.displayName} request routing` : "Profile team routing"}</h3>}
        </div>
        <div className="library-tag-row">
          <span>{visibleRoutes.length} routes</span>
          {team && isCoverageTeam(team) ? <span>coverage team</span> : null}
          {team && isDeployedTelegramFleet(team) ? <span>Telegram fleet</span> : null}
          {team && isCoverageTeam(team) && !isDeployedTelegramFleet(team) ? <span>not Telegram fleet</span> : null}
        </div>
      </div>
      <div className="profile-route-flow" aria-label="Profile request routes">
        {displayedRoutes.map((route) => {
          const target = routeTargetLabel(route);
          const isTacRoute = target.includes("TAC");
          return (
            <div
              className={isTacRoute ? "profile-route-row routes-to-tac" : "profile-route-row"}
              key={`${route.route_name}-${route.target_type}-${route.target_profile_id}-${route.target_team_id}`}
            >
              <span className="profile-route-intent">
                <GitBranch className="h-3.5 w-3.5" />
                {formatRouteLabel(route.route_name)}
              </span>
              <span className="profile-route-arrow" aria-hidden>
                <ArrowRight className="h-3.5 w-3.5" />
              </span>
              <span className="profile-route-target">{target}</span>
            </div>
          );
        })}
        {remaining ? (
          <div className="profile-route-row profile-route-more">
            <span>{remaining} more routes</span>
          </div>
        ) : null}
      </div>
    </article>
  );
}

function latestRunAt(rows: readonly SupabaseLogRow[]) {
  let latest = 0;
  let latestRaw = "";
  for (const row of rows) {
    const raw = String(row.started_at ?? row.source_updated_at ?? row.created_at ?? "");
    const time = Date.parse(raw);
    if (Number.isFinite(time) && time > latest) {
      latest = time;
      latestRaw = raw;
    }
  }
  return latestRaw;
}

function formatProfileEvidenceDate(value: string) {
  if (!value) return "No recent run";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function ProfileRuntimeEvidence({ profileId, teamId }: { profileId?: string; teamId?: string }) {
  const tenant = useTenant();
  const [rows, setRows] = useState<SupabaseLogRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getSupabaseLogsTimeline({
        client: tenant,
        days: 14,
        limit: 200,
        search: profileId || teamId,
        source: "agent_runs",
      })
      .then((response) => {
        if (!cancelled) setRows(response.rows ?? []);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [profileId, teamId, tenant]);

  const profileCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of rows) {
      const profile = profileFromRun(row);
      counts.set(profile, (counts.get(profile) ?? 0) + 1);
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6);
  }, [rows]);
  const failures = rows.filter((row) => ["failed", "error", "blocked"].includes(runStatus(row))).length;
  const traced = rows.filter((row) => Boolean(row.trace_id)).length;
  const latest = latestRunAt(rows);

  return (
    <article className="profile-contract-panel">
      <div>
        <span className="library-eyebrow">Supabase Runtime Evidence</span>
        <h3>Client-scoped profile activity</h3>
        <p>
          Scope: {tenant}. Read from <code>agent_runs</code> via the timeline
          mirror; profile templates stay shared, but runtime evidence follows
          the active client.
        </p>
      </div>
      <div className="profile-contract-grid">
        <section>
          <div className="library-card-topline">
            <span>Runs</span>
            <span>{loading ? "loading" : "14d"}</span>
          </div>
          <strong>{rows.length}</strong>
          <p>{formatProfileEvidenceDate(latest)}</p>
        </section>
        <section>
          <div className="library-card-topline">
            <span>Failures</span>
            <span>recent</span>
          </div>
          <strong>{failures}</strong>
          <p>{rows.length ? `${Math.max(rows.length - failures, 0)} non-failing runs` : "No rows for this scope."}</p>
        </section>
        <section>
          <div className="library-card-topline">
            <span>Trace links</span>
            <span>coverage</span>
          </div>
          <strong>{traced}</strong>
          <p>{rows.length ? `${Math.round((traced / rows.length) * 100)}% have trace ids` : "No trace candidates."}</p>
        </section>
        <section>
          <div className="library-card-topline">
            <span>Profiles</span>
            <span>{profileCounts.length}</span>
          </div>
          <strong>{profileCounts[0]?.[0] ?? "none"}</strong>
          <p>{profileCounts.map(([name, count]) => `${name} ${count}`).join(", ") || error?.replace(/^Error:\s*/, "") || "Waiting for Supabase."}</p>
        </section>
      </div>
    </article>
  );
}

const profileReports: Record<
  string,
  {
    description: string;
    publicPath: string;
    sourceLabel: string;
    summaryPath: string;
    title: string;
  }
> = {
  "tac-lead": {
    description:
      "Structured GBauto report view with KPI ranks, skill affinity, handoff map, full prompt, and current Hermes profile YAML.",
    publicPath: "/profile-reports/tac-lead/index.html",
    sourceLabel: "tac-lead-profile-artifacts-2026-06-13",
    summaryPath: "/profile-reports/tac-lead/data-summary.json",
    title: "TAC Lead Profile Report",
  },
};

export function ProfileDetailPage() {
  const { profileId } = useParams();
  const profile = gbautoLibrary.profiles.find((item) => item.id === profileId);
  const report = profileId ? profileReports[profileId] : null;

  if (!profile) {
    return <Navigate to="/profiles" replace />;
  }

  return (
    <section className="library-page profile-detail-page normal-case">
      <div className="profile-detail-topbar">
        <Link className="profile-detail-back" to="/profiles">
          <ArrowLeft className="h-3.5 w-3.5" />
          Profiles
        </Link>
        <div className="library-tag-row">
          <span>{profile.team || "template"}</span>
          {isGbautoCoreProfile(profile) && <span>gbauto Core</span>}
          <span>{profile.runtime || "runtime tbd"}</span>
          <span>{profile.model || "model tbd"}</span>
        </div>
      </div>

      <div className="library-hero profile-detail-hero">
        <div className="profile-detail-copy">
          <span className="library-eyebrow">Individual Profile View</span>
          <h2>{profile.displayName}</h2>
          <p>{profile.role || "Reusable Hermes profile template."}</p>
          <div className="library-mini-roster">
            <span>{profile.status || "status tbd"}</span>
            <span>{profile.primaryTools.length} tools</span>
            <span>{profile.operatingModes.length} modes</span>
            <span>{profile.canonicalSources.length} sources</span>
          </div>
        </div>
        <ProfileArt profile={profile} compact />
      </div>

      <ProfileRuntimeEvidence profileId={profile.id} teamId={profile.team} />

      <div className="profile-detail-grid">
        <article className="profile-detail-panel">
          <div className="library-card-topline">
            <span>Operating modes</span>
            <span>{profile.operatingModes.length}</span>
          </div>
          <div className="profile-detail-list">
            {profile.operatingModes.map((mode) => (
              <div key={mode.id}>
                <strong>{mode.label}</strong>
                <p>{mode.purpose}</p>
              </div>
            ))}
          </div>
        </article>

        <article className="profile-detail-panel">
          <div className="library-card-topline">
            <span>Primary tools</span>
            <span>{profile.primaryTools.length}</span>
          </div>
          <div className="library-mini-roster">
            {profile.primaryTools.map((tool) => (
              <span key={tool}>{tool}</span>
            ))}
          </div>
        </article>

        <article className="profile-detail-panel">
          <div className="library-card-topline">
            <span>Canonical sources</span>
            <span>{profile.canonicalSources.length}</span>
          </div>
          <div className="profile-detail-source-list">
            {profile.canonicalSources.slice(0, 8).map((source) => (
              <code key={source}>{source}</code>
            ))}
          </div>
        </article>
      </div>

      {report ? (
        <article className="profile-report-viewer">
          <div className="profile-report-header">
            <div>
              <span className="library-eyebrow">Embedded report</span>
              <h3>{report.title}</h3>
              <p>{report.description}</p>
            </div>
            <div className="profile-report-actions">
              <a href={report.summaryPath} rel="noreferrer" target="_blank">
                <FileText className="h-3.5 w-3.5" />
                Summary data
              </a>
              <a href={report.publicPath} rel="noreferrer" target="_blank">
                <ExternalLink className="h-3.5 w-3.5" />
                Open raw report
              </a>
            </div>
          </div>
          <div className="profile-report-frame-wrap">
            <iframe
              className="profile-report-frame"
              loading="eager"
              src={report.publicPath}
              title={`${profile.displayName} profile report`}
            />
          </div>
        </article>
      ) : (
        <article className="profile-detail-panel">
          <div className="library-card-topline">
            <span>Report</span>
            <span>Not generated</span>
          </div>
          <p>
            This profile has metadata in the AI Library index, but no individual
            HTML profile report has been generated yet.
          </p>
        </article>
      )}
    </section>
  );
}

export function ProfileTeamDetailPage() {
  const { teamId } = useParams();
  const tenant = useTenant();
  const liveProfileCatalog = useLiveAgentProfileCatalog(tenant);
  const team = profileTeamCatalog(liveProfileCatalog).find((item) => item.id === teamId);
  const memberProfiles = team
    ? team.profiles
        .map((profileId) => gbautoLibrary.profiles.find((profile) => profile.id === profileId))
        .filter(Boolean) as HermesProfile[]
    : [];
  const memberAgents = team ? agentsForTeamEntry(team) : [];
  const liveOnlyProfiles = team
    ? (team.liveProfiles ?? []).filter(
        (profile) => !gbautoLibrary.profiles.some((item) => item.id === profile.profile_id),
      )
    : [];

  if (!team) {
    return <Navigate to="/profiles" replace />;
  }

  return (
    <section className="library-page profile-detail-page normal-case">
      <div className="profile-detail-topbar">
        <Link className="profile-detail-back" to="/profiles">
          <ArrowLeft className="h-3.5 w-3.5" />
          Profiles
        </Link>
        <div className="library-tag-row">
          <span>{team.clientSlug ?? "gbauto Core"}</span>
          <span>{team.runtime}</span>
          <span>{teamProfileCount(team)} profiles</span>
          {teamAgentCount(team) ? <span>{teamAgentCount(team)} agents</span> : null}
          {team.routes?.length ? <span>{team.routes.length} routes</span> : null}
          {isCoverageTeam(team) ? <span>coverage</span> : null}
          {isCoverageTeam(team) && !isDeployedTelegramFleet(team) ? <span>not Telegram fleet</span> : null}
        </div>
      </div>

      <div className="library-hero profile-detail-hero">
        <div className="profile-detail-copy">
          <span className="library-eyebrow">Profile Team</span>
          <h2>{team.displayName}</h2>
          <p>{team.purpose}</p>
          <div className="library-mini-roster">
            <span>{team.id}</span>
            <span>{team.runtime}</span>
            {team.clientSlug ? <span>{team.clientSlug}</span> : <span>shared</span>}
            {team.routes?.length ? <span>{team.routes.length} routes</span> : null}
            {isCoverageTeam(team) ? <span>coverage team</span> : null}
            {isCoverageTeam(team) && !isDeployedTelegramFleet(team) ? <span>not a Telegram fleet</span> : null}
          </div>
        </div>
      </div>

      <ProfileRuntimeEvidence teamId={team.id} />

      <div className="profile-detail-grid">
        <article className="profile-detail-panel">
          <div className="library-card-topline">
            <span>Source</span>
            <span>template</span>
          </div>
          <div className="profile-detail-source-list">
            <code>{team.sourcePath}</code>
          </div>
        </article>

        <article className="profile-detail-panel">
          <div className="library-card-topline">
            <span>Roster</span>
            <span>{teamProfileCount(team)} profiles</span>
          </div>
          <div className="library-mini-roster">
            {team.profiles.map((profileName) => (
              <span key={profileName}>{liveProfileLabel(profileName, team)}</span>
            ))}
          </div>
        </article>

        {memberAgents.length ? (
          <article className="profile-detail-panel">
            <div className="library-card-topline">
              <span>Specialist Agents</span>
              <span>{memberAgents.length}</span>
            </div>
            <div className="library-mini-roster">
              {memberAgents.map((agent) => (
                <span key={agent.id}>{agent.displayName}</span>
              ))}
            </div>
          </article>
        ) : null}
      </div>

      <ProfileRouteMap routes={team.routes ?? []} team={team} />

      <div className="profile-index-grid">
        {memberProfiles.map((profile) => (
          <Link
            className="profile-index-card"
            key={profile.id}
            to={`/profiles/${profile.id}`}
          >
            <ProfileArt profile={profile} compact />
            <div>
              <div className="library-card-topline">
                <span>{isGbautoCoreProfile(profile) ? "gbauto Core" : profile.team || "template"}</span>
                <span>{profile.model || "model tbd"}</span>
              </div>
              <h3>{profile.displayName}</h3>
              <p>{profile.role || "Reusable Hermes profile template."}</p>
            </div>
          </Link>
        ))}
      </div>

      {liveOnlyProfiles.length ? (
        <div className="profile-index-grid">
          {liveOnlyProfiles.map((profile) => (
            <article className="profile-index-card" key={profile.profile_key}>
              <div>
                <div className="library-card-topline">
                  <span>{profile.profile_type}</span>
                  <span>{profile.skill_count ?? 0} skills / {profile.route_count ?? 0} routes</span>
                </div>
                <h3>{profile.display_name}</h3>
                <p>{profile.role || profile.source_kind || "Live Supabase profile."}</p>
                <div className="library-mini-roster">
                  <span>{profile.profile_id}</span>
                  {profile.deploy_user ? <span>{profile.deploy_user}</span> : null}
                  {profile.deploy_path ? <span>{profile.deploy_path}</span> : null}
                </div>
              </div>
            </article>
          ))}
        </div>
      ) : null}

      {memberAgents.length ? (
        <div className="profile-index-grid">
          {memberAgents.map((agent) => (
            <article className="profile-index-card" key={agent.id}>
              <div>
                <div className="library-card-topline">
                  <span>{agent.teamDisplayName || team.displayName}</span>
                  <span>{agent.role || "agent"}</span>
                </div>
                <h3>{agent.displayName}</h3>
                <p>{agent.description || "Canonical ai-library specialist agent."}</p>
                <div className="library-mini-roster">
                  <span>{agent.id}</span>
                  {agent.sourcePath ? <span>{agent.sourcePath}</span> : null}
                </div>
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function ProfilePanelItem({
  active,
  icon: Icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  onClick: () => void;
}) {
  return (
    <ListItem
      active={active}
      className={
        "rounded-none whitespace-nowrap px-2.5 py-1.5 font-mondwest text-[0.7rem] uppercase tracking-[0.08em] " +
        (active ? "bg-foreground/90 text-background hover:text-background" : "")
      }
      onClick={onClick}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="flex-1 truncate">{label}</span>
    </ListItem>
  );
}

function ProfileTeamsShowcase({
  profileTeams,
  tacProfiles,
  tenant,
}: {
  profileTeams: readonly ProfileTeamCatalogEntry[];
  tacProfiles: readonly HermesProfile[];
  tenant: string;
}) {
  return (
    <section className="library-page">
      <div className="library-hero">
        <div>
          <span className="library-eyebrow">Supabase Profile Teams</span>
          <h2>{tenant === "jid5274" ? "Jason Operations" : "Profile Team Catalog"}</h2>
          <p>
            Profile team membership is loaded from Supabase agent_profile_teams
            for the active client. Generated library data only enriches cards
            with art, local links, and static profile details.
          </p>
        </div>
        <div className="library-hero-stats">
          <strong>{profileTeams.length}</strong>
          <span>teams</span>
          <strong>{profileTeams.reduce((total, team) => total + teamProfileCount(team), 0)}</strong>
          <span>profiles</span>
          <strong>{profileTeams.reduce((total, team) => total + teamAgentCount(team), 0)}</strong>
          <span>agents</span>
          <strong>{profileTeams.reduce((total, team) => total + (team.routes?.length ?? 0), 0)}</strong>
          <span>routes</span>
        </div>
      </div>

      <div className="profile-index-grid">
        {profileTeams.map((team) => (
          <Link
            className="profile-index-card"
            key={team.id}
            to={`/profiles/teams/${team.id}`}
          >
            <div>
              <div className="library-card-topline">
                <span>{team.clientSlug ?? "gbauto Core"}</span>
                <span>{teamProfileCount(team)} profiles / {teamAgentCount(team)} agents</span>
              </div>
              <h3>{team.displayName}</h3>
              <p>{team.purpose}</p>
              <div className="library-mini-roster">
                {team.profiles.slice(0, 8).map((profileName) => (
                  <span key={profileName}>{liveProfileLabel(profileName, team)}</span>
                ))}
                {team.agents?.slice(0, 6).map((agentId) => {
                  const agent = gbautoLibrary.agents.find((item) => item.id === agentId);
                  return <span key={agentId}>{agent?.displayName ?? agentId}</span>;
                })}
                {isCoverageTeam(team) ? <span>coverage</span> : null}
                {isCoverageTeam(team) && !isDeployedTelegramFleet(team) ? <span>not Telegram fleet</span> : null}
              </div>
              <ProfileRouteMap compact max={5} routes={team.routes ?? []} team={team} />
            </div>
          </Link>
        ))}
      </div>

      {tenant === "gbautomation" && (
        <div className="profile-expanding-cards">
        {tacProfiles.map((profile) => (
          <Link
            className="profile-feature-card"
            key={profile.id}
            to={`/profiles/${profile.id}`}
          >
            <ProfileArt profile={profile} />
            <div className="profile-feature-title">
              <span>{profile.displayName}</span>
            </div>
            <div className="profile-feature-reveal">
              <div>
                <h3>{profile.displayName}</h3>
                <p>{profile.role || "Hermes TAC profile."}</p>
                <div className="library-tag-row">
                  <span>{profile.model || "model tbd"}</span>
                  <span>{profile.status || "planned"}</span>
                  <span>{profile.primaryTools.length} tools</span>
                </div>
              </div>
            </div>
          </Link>
        ))}
      </div>
      )}
    </section>
  );
}

function ProfileIndexShowcase({
  profiles,
  source,
  tenant,
}: {
  profiles: readonly ProfileIndexEntry[];
  source: string;
  tenant: string;
}) {
  return (
    <section className="library-page">
      <div className="library-toolbar">
        <div>
          <span className="library-eyebrow">Supabase Profile Catalog</span>
          <h2>Hermes Profile Index</h2>
        </div>
        <Badge tone="secondary" className="text-[10px]">
          {profiles.length} profiles from {tenant}
        </Badge>
      </div>
      <div className="supabase-note">
        <BookOpen className="h-4 w-4" />
        <span>Roster source: {source}. Generated profile metadata is used only for local art and detail enrichment.</span>
      </div>
      <div className="profile-index-grid">
        {profiles.map((profile) => {
          const content = (
            <div>
              <div className="library-card-topline">
                <span>{profile.team || "template"}</span>
                <span>
                  {profile.localProfile && isGbautoCoreProfile(profile.localProfile)
                    ? "gbauto Core"
                    : profile.model || profile.runtime || "model tbd"}
                </span>
              </div>
              <h3>{profile.displayName}</h3>
              {profile.localProfile && isGbautoCoreProfile(profile.localProfile) && (
                <Badge tone="outline" className="mb-2 w-fit text-[10px]">
                  gbauto Core
                </Badge>
              )}
              <p>{profile.role || "Reusable Hermes profile template."}</p>
              <div className="library-mini-roster">
                {profile.localProfile && isGbautoCoreProfile(profile.localProfile) && <span>gbauto Core</span>}
                {profile.liveProfile ? <span>{profile.liveProfile.profile_type}</span> : null}
                {profile.skillCount != null ? <span>{profile.skillCount} skills</span> : null}
                {profile.routeCount != null ? <span>{profile.routeCount} routes</span> : null}
                {!profile.liveProfile && profile.localProfile
                  ? profile.localProfile.primaryTools.slice(0, 4).map((tool) => (
                      <span key={tool}>{tool}</span>
                    ))
                  : null}
              </div>
            </div>
          );
          return profile.localProfile ? (
            <Link
              className="profile-index-card"
              key={profile.id}
              to={`/profiles/${profile.id}`}
            >
              <ProfileArt profile={profile.localProfile} compact />
              {content}
            </Link>
          ) : (
            <article className="profile-index-card" key={profile.id}>
              {content}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function TacLeadVariants() {
  const tacLead = gbautoLibrary.profiles.find((profile) => profile.id === "tac-lead");
  return (
    <section className="library-page">
      <div className="library-hero tac-lead-hero">
        <div>
          <span className="library-eyebrow">Selectable TAC Lead Concepts</span>
          <h2>TAC Lead Variants</h2>
          <p>
            Three UI treatments for the same profile: advisor, retrieval, and
            dispatch. Each maps to an operating mode in the profile spec.
          </p>
        </div>
        {tacLead && <ProfileArt profile={tacLead} compact />}
      </div>

      <div className="tac-variant-grid">
        {gbautoLibrary.tacLeadVariants.map((variant) => {
          const mode = tacLead?.operatingModes.find(
            (item) => item.id === variant.modeId,
          );
          return (
            <Link
              className="tac-variant-card"
              key={variant.id}
              to={`/profiles/${variant.profileId}`}
            >
              <div className="library-card-topline">
                <span>{variant.modeId}</span>
                <span>option</span>
              </div>
              <h3>{variant.title}</h3>
              <p>{mode?.purpose || variant.summary}</p>
              <strong>{variant.emphasis}</strong>
            </Link>
          );
        })}
      </div>
    </section>
  );
}

function ProfileArt({
  compact = false,
  profile,
}: {
  compact?: boolean;
  profile: HermesProfile;
}) {
  const externalArtUrl = profileMtgArtUrl(profile.mtg);
  const localArtUrl = externalArtUrl
    ? `/profile-art/${profile.id}.jpg?v=${PROFILE_ART_VERSION}`
    : "";
  const [artUrl, setArtUrl] = useState(localArtUrl || externalArtUrl);
  const [imageFailed, setImageFailed] = useState(false);
  const hue = hashProfileSeed(profile.artSeed) % 360;

  useEffect(() => {
    const id = window.setTimeout(() => {
      setArtUrl(localArtUrl || externalArtUrl);
      setImageFailed(false);
    }, 0);
    return () => window.clearTimeout(id);
  }, [externalArtUrl, localArtUrl]);

  if (artUrl && !imageFailed) {
    return (
      <img
        alt={`${profile.displayName} MTG art`}
        className={compact ? "library-art is-compact" : "library-art"}
        loading="lazy"
        onError={() => {
          if (externalArtUrl && artUrl !== externalArtUrl) {
            setArtUrl(externalArtUrl);
          } else {
            setImageFailed(true);
          }
        }}
        src={artUrl}
      />
    );
  }
  return (
    <div
      aria-label={`${profile.displayName} generated art panel`}
      className={
        compact
          ? "library-art library-art-fallback is-compact"
          : "library-art library-art-fallback"
      }
      role="img"
      style={{ "--library-hue": `${hue}deg` } as CSSProperties}
    >
      <LayoutGrid className="h-7 w-7" />
      <span>{profile.displayName}</span>
    </div>
  );
}

function hashProfileSeed(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return hash;
}

function profileMtgArtUrl(value: unknown) {
  if (!value || typeof value !== "object") return "";
  const mtg = value as { artUrl?: string; imageUrl?: string };
  return mtg.artUrl || mtg.imageUrl || "";
}
