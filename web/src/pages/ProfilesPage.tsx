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
  ArrowLeft,
  BookOpen,
  ChevronDown,
  ExternalLink,
  FileText,
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
import type { ProfileInfo } from "@/lib/api";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { useToast } from "@/hooks/useToast";
import { useConfirmDelete } from "@/hooks/useConfirmDelete";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { Toast } from "@/components/Toast";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { gbautoLibrary } from "@/generated/gbautoLibrary";
import {
  relatedContractsForProfile,
  useSupabaseContractsIndex,
} from "@/lib/gbautoSupabaseContracts";
import type {
  SupabaseContractRow,
  SupabaseContractsIndex,
} from "@/lib/gbautoSupabaseContracts";

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
  const [libraryView, setLibraryView] = useState<
    "teams" | "profiles" | "tac" | "admin"
  >("teams");
  const { toast, showToast } = useToast();
  const { t } = useI18n();
  const { setEnd } = usePageHeader();
  const contractsIndex = useSupabaseContractsIndex();

  // Create modal
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [cloneFromDefault, setCloneFromDefault] = useState(true);
  const [creating, setCreating] = useState(false);
  const closeCreateModal = useCallback(() => setCreateModalOpen(false), []);
  const createModalRef = useModalBehavior({
    open: createModalOpen,
    onClose: closeCreateModal,
  });

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

      <ProfileLibraryTabs active={libraryView} onChange={setLibraryView} />

      {libraryView === "teams" && (
        <ProfileTeamsShowcase contractsIndex={contractsIndex} tacProfiles={tacProfiles} />
      )}

      {libraryView === "profiles" && (
        <ProfileIndexShowcase contractsIndex={contractsIndex} profiles={gbautoLibrary.profiles} />
      )}

      {libraryView === "tac" && <TacLeadVariants contractsIndex={contractsIndex} />}

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
  );
}

type ProfileLibraryView = "teams" | "profiles" | "tac" | "admin";
type HermesProfile = (typeof gbautoLibrary.profiles)[number];

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
  const contractsIndex = useSupabaseContractsIndex();
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

      <ProfileContractPanel
        contracts={relatedContractsForProfile(contractsIndex, profile.id, profile.team)}
        subtitle="Runtime, trace, Kanban, and prompt-profile contract surfaces for this profile."
        title="Profile data contracts"
      />

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

function ProfileLibraryTabs({
  active,
  onChange,
}: {
  active: ProfileLibraryView;
  onChange: (view: ProfileLibraryView) => void;
}) {
  const tabs: Array<{
    icon: React.ComponentType<{ className?: string }>;
    id: ProfileLibraryView;
    label: string;
  }> = [
    { id: "teams", label: "Team Profiles", icon: LayoutGrid },
    { id: "profiles", label: "Profile Index", icon: BookOpen },
    { id: "tac", label: "TAC Lead Variants", icon: Sparkles },
    { id: "admin", label: "Admin", icon: Users },
  ];

  return (
    <div className="library-view-switcher" role="tablist" aria-label="Profile views">
      {tabs.map((tab) => {
        const Icon = tab.icon;
        return (
          <button
            aria-selected={active === tab.id}
            className={active === tab.id ? "is-active" : ""}
            key={tab.id}
            onClick={() => onChange(tab.id)}
            role="tab"
            type="button"
          >
            <Icon className="h-3.5 w-3.5" />
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

function ProfileTeamsShowcase({
  contractsIndex,
  tacProfiles,
}: {
  contractsIndex: SupabaseContractsIndex | null;
  tacProfiles: readonly HermesProfile[];
}) {
  return (
    <section className="library-page">
      <div className="library-hero">
        <div>
          <span className="library-eyebrow">Hermes Profile Teams</span>
          <h2>TAC Hermes Build Council</h2>
          <p>
            Hover the profile cards to reveal role details. TAC profiles use
            the upvoted Angel image set from the avatar review gallery.
          </p>
        </div>
        <div className="library-hero-stats">
          <strong>{tacProfiles.length}</strong>
          <span>profiles</span>
          <strong>{gbautoLibrary.profileTeams.length}</strong>
          <span>team spec</span>
        </div>
      </div>

      <ProfileContractPanel
        contracts={relatedContractsForProfile(contractsIndex, "tac-lead", "tac-hermes")}
        subtitle="The TAC profile team is read through agent runs, Kanban cards, dispatch links, Langfuse trace mirrors, and prompt-profile records."
        title="TAC team data contracts"
      />

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
    </section>
  );
}

function ProfileIndexShowcase({
  contractsIndex,
  profiles,
}: {
  contractsIndex: SupabaseContractsIndex | null;
  profiles: readonly HermesProfile[];
}) {
  return (
    <section className="library-page">
      <div className="library-toolbar">
        <div>
          <span className="library-eyebrow">Profile Templates</span>
          <h2>Hermes Profile Index</h2>
        </div>
        <Badge tone="secondary" className="text-[10px]">
          {profiles.length} imported
        </Badge>
      </div>
      <ProfileContractPanel
        contracts={relatedContractsForProfile(contractsIndex, "profile", "agent")}
        subtitle="Shared contract context for browsing profile templates and mapping them back to runtime evidence."
        title="Profile index contracts"
      />
      <div className="profile-index-grid">
        {profiles.map((profile) => (
          <Link
            className="profile-index-card"
            key={profile.id}
            to={`/profiles/${profile.id}`}
          >
            <ProfileArt profile={profile} compact />
            <div>
              <div className="library-card-topline">
                <span>{profile.team || "template"}</span>
                <span>{profile.model || "model tbd"}</span>
              </div>
              <h3>{profile.displayName}</h3>
              <p>{profile.role || "Reusable Hermes profile template."}</p>
              <div className="library-mini-roster">
                {profile.primaryTools.slice(0, 4).map((tool) => (
                  <span key={tool}>{tool}</span>
                ))}
              </div>
            </div>
          </Link>
        ))}
      </div>
    </section>
  );
}

function TacLeadVariants({
  contractsIndex,
}: {
  contractsIndex: SupabaseContractsIndex | null;
}) {
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

      <ProfileContractPanel
        contracts={relatedContractsForProfile(contractsIndex, "tac-lead", "tac-hermes")}
        subtitle="Contracts used to evaluate TAC lead dispatch, retrieval, advisor, and handoff behavior."
        title="TAC lead contract surface"
      />

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

function ProfileContractPanel({
  contracts,
  subtitle,
  title,
}: {
  contracts: readonly SupabaseContractRow[];
  subtitle: string;
  title: string;
}) {
  if (!contracts.length) return null;

  return (
    <article className="profile-contract-panel">
      <div>
        <span className="library-eyebrow">Supabase Contract Index</span>
        <h3>{title}</h3>
        <p>{subtitle}</p>
      </div>
      <div className="profile-contract-grid">
        {contracts.slice(0, 6).map((contract) => (
          <section key={contract.object_name ?? `${contract.domain}:${contract.owner_agent}`}>
            <div className="library-card-topline">
              <span>{contract.domain ?? "domain tbd"}</span>
              <span>{contract.access_model ?? "access tbd"}</span>
            </div>
            <strong>{contract.object_name ?? "unnamed object"}</strong>
            <p>{contract.notes || contract.read_path || contract.write_path || "No contract notes yet."}</p>
          </section>
        ))}
      </div>
    </article>
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
