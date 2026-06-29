import { useEffect, useLayoutEffect, useState, useMemo } from "react";
import type { CSSProperties } from "react";
import {
  BookOpen,
  Package,
  Search,
  Wrench,
  X,
  Cpu,
  Globe,
  Shield,
  Eye,
  Paintbrush,
  Brain,
  Blocks,
  Code,
  Zap,
  Filter,
  LayoutGrid,
  Sparkles,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  SkillInfo,
  SupabaseLogRow,
  SupabaseLogsSummaryResponse,
  ToolsetInfo,
} from "@/lib/api";
import { auraSkills } from "@/generated/auraSkills";
import { gbautoLibrary } from "@/generated/gbautoLibrary";
import { skillIndividualArt } from "@/generated/skillIndividualArt";
import { useToast } from "@/hooks/useToast";
import { Toast } from "@/components/Toast";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { ListItem } from "@nous-research/ui/ui/components/list-item";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Switch } from "@nous-research/ui/ui/components/switch";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { PluginSlot } from "@/plugins";
import { useTenant } from "@/lib/tenant";
import {
  contractsForObjects,
  findSkillRegistryRow,
  relatedContractsForSkill,
  useSupabaseContractsIndex,
} from "@/lib/gbautoSupabaseContracts";
import type {
  SupabaseContractRow,
  SupabaseContractsIndex,
} from "@/lib/gbautoSupabaseContracts";

/* ------------------------------------------------------------------ */
/*  Types & helpers                                                    */
/* ------------------------------------------------------------------ */

const CATEGORY_LABELS: Record<string, string> = {
  mlops: "MLOps",
  "mlops/cloud": "MLOps / Cloud",
  "mlops/evaluation": "MLOps / Evaluation",
  "mlops/inference": "MLOps / Inference",
  "mlops/models": "MLOps / Models",
  "mlops/training": "MLOps / Training",
  "mlops/vector-databases": "MLOps / Vector DBs",
  mcp: "MCP",
  "red-teaming": "Red Teaming",
  ocr: "OCR",
  p5js: "p5.js",
  ai: "AI",
  ux: "UX",
  ui: "UI",
};

const SKILL_ART_VERSION = "skill-groups-20260623";

const SKILL_ART_KEYS = new Set([
  "ai",
  "design-system",
  "general",
  "marketing",
  "mcp",
  "media",
  "mlops",
  "mlops-cloud",
  "mlops-evaluation",
  "mlops-inference",
  "mlops-models",
  "mlops-training",
  "mlops-vector-databases",
  "motion",
  "ocr",
  "p5js",
  "red-teaming",
  "responsive",
  "review",
  "threejs",
  "ui",
  "ui-skill",
  "ux",
  "visual-effect",
]);

const TEAM_ART_GROUPS: Record<string, string> = {
  "apollo-v2-build": "ai",
  "aws-cloud-ops": "mlops-cloud",
  "ceo-board": "ai",
  database: "mlops-vector-databases",
  design: "design-system",
  development: "ui-skill",
  devsecops: "red-teaming",
  "eagle-ui-redesign": "ui",
  "ecom-intelligence": "marketing",
  "logomotion-v5": "motion",
  operations: "mlops",
  planning: "ai",
  "qa-qc": "review",
  "trader-bot-scaffold": "ai",
};

function prettyCategory(
  raw: string | null | undefined,
  generalLabel: string,
): string {
  if (!raw) return generalLabel;
  if (CATEGORY_LABELS[raw]) return CATEGORY_LABELS[raw];
  return raw
    .split(/[-_/]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

const TOOLSET_ICONS: Record<
  string,
  React.ComponentType<{ className?: string }>
> = {
  computer: Cpu,
  web: Globe,
  security: Shield,
  vision: Eye,
  design: Paintbrush,
  ai: Brain,
  integration: Blocks,
  code: Code,
  automation: Zap,
};

function toolsetIcon(
  name: string,
): React.ComponentType<{ className?: string }> {
  const lower = name.toLowerCase();
  for (const [key, icon] of Object.entries(TOOLSET_ICONS)) {
    if (lower.includes(key)) return icon;
  }
  return Wrench;
}

function skillArtKey(raw: string | null | undefined): string {
  const normalized =
    raw
      ?.trim()
      .toLowerCase()
      .replace(/&/g, "and")
      .replace(/\./g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "general";
  return SKILL_ART_KEYS.has(normalized) ? normalized : "general";
}

function skillGroupArtUrl(raw: string | null | undefined): string {
  return `/skill-art/${skillArtKey(raw)}.jpg?v=${SKILL_ART_VERSION}`;
}

type IndividualArtEntry = {
  artId: string;
  publicPath: string;
  sourceFile: string;
  sourceGroup: string;
};

type SkillsDisplayMode = "current" | "image-gallery" | "detail-gallery";

type SkillsGalleryItem = {
  id: string;
  title: string;
  description: string;
  category?: string;
  href?: string;
  imageUrl?: string;
  meta?: string;
  metrics?: { label: string; value: string }[];
  tags?: string[];
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [toolsets, setToolsets] = useState<ToolsetInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [view, setView] = useState<
    "skills" | "toolsets" | "library" | "prompt-cards" | "aura-skills"
  >("skills");
  const [displayMode, setDisplayMode] =
    useState<SkillsDisplayMode>("current");
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [togglingSkills, setTogglingSkills] = useState<Set<string>>(new Set());
  const [supabaseSummary, setSupabaseSummary] =
    useState<SupabaseLogsSummaryResponse | null>(null);
  const [skillRunRows, setSkillRunRows] = useState<SupabaseLogRow[]>([]);
  const [skillFailureRows, setSkillFailureRows] = useState<SupabaseLogRow[]>(
    [],
  );
  const [analyticsLoading, setAnalyticsLoading] = useState(true);
  const [analyticsError, setAnalyticsError] = useState<string | null>(null);
  const { toast, showToast } = useToast();
  const { t } = useI18n();
  const { setAfterTitle, setEnd } = usePageHeader();
  const contractsIndex = useSupabaseContractsIndex();
  const tenant = useTenant();

  useEffect(() => {
    Promise.all([api.getSkills(), api.getToolsets()])
      .then(([s, tsets]) => {
        setSkills(s);
        setToolsets(tsets);
      })
      .catch(() => showToast(t.common.loading, "error"))
      .finally(() => setLoading(false));
  }, [showToast, t.common.loading]);

  useEffect(() => {
    let cancelled = false;
    setAnalyticsLoading(true);
    setAnalyticsError(null);
    Promise.all([
      api.getSupabaseLogsSummary({ client: tenant }),
      api.getSupabaseLogsTimeline({
        client: tenant,
        days: 14,
        limit: 250,
        source: "skill_runs",
      }),
      api.getSupabaseLogsFailures({
        client: tenant,
        days: 14,
        limit: 100,
        search: "skill",
      }),
    ])
      .then(([summary, runs, failures]) => {
        if (cancelled) return;
        setSupabaseSummary(summary);
        setSkillRunRows(runs.rows ?? []);
        setSkillFailureRows(failures.rows ?? []);
      })
      .catch((err) => {
        if (!cancelled) setAnalyticsError(String(err));
      })
      .finally(() => {
        if (!cancelled) setAnalyticsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tenant]);

  /* ---- Toggle skill ---- */
  const handleToggleSkill = async (skill: SkillInfo) => {
    setTogglingSkills((prev) => new Set(prev).add(skill.name));
    try {
      await api.toggleSkill(skill.name, !skill.enabled);
      setSkills((prev) =>
        prev.map((s) =>
          s.name === skill.name ? { ...s, enabled: !s.enabled } : s,
        ),
      );
      showToast(
        `${skill.name} ${skill.enabled ? t.common.disabled : t.common.enabled}`,
        "success",
      );
    } catch {
      showToast(`${t.common.failedToToggle} ${skill.name}`, "error");
    } finally {
      setTogglingSkills((prev) => {
        const next = new Set(prev);
        next.delete(skill.name);
        return next;
      });
    }
  };

  /* ---- Derived data ---- */
  const lowerSearch = search.toLowerCase();
  const isSearching = search.trim().length > 0 && view === "skills";

  const searchMatchedSkills = useMemo(() => {
    if (!isSearching) return [];
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(lowerSearch) ||
        s.description.toLowerCase().includes(lowerSearch) ||
        (s.category ?? "").toLowerCase().includes(lowerSearch),
    );
  }, [skills, isSearching, lowerSearch]);

  const activeSkills = useMemo(() => {
    if (isSearching) return [];
    if (!activeCategory)
      return [...skills].sort((a, b) => a.name.localeCompare(b.name));
    return skills
      .filter((s) =>
        activeCategory === "__none__"
          ? !s.category
          : s.category === activeCategory,
      )
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [skills, activeCategory, isSearching]);

  const allCategories = useMemo(() => {
    const cats = new Map<string, number>();
    for (const s of skills) {
      const key = s.category || "__none__";
      cats.set(key, (cats.get(key) || 0) + 1);
    }
    return [...cats.entries()]
      .sort((a, b) => {
        if (a[0] === "__none__") return -1;
        if (b[0] === "__none__") return 1;
        return a[0].localeCompare(b[0]);
      })
      .map(([key, count]) => ({
        key,
        name: prettyCategory(key === "__none__" ? null : key, t.common.general),
        count,
      }));
  }, [skills, t]);

  const enabledCount = skills.filter((s) => s.enabled).length;

  const libraryAgents = useMemo(() => {
    return gbautoLibrary.agents.filter((agent) => {
      if (!search) return true;
      const haystack = [
        agent.displayName,
        agent.name,
        agent.teamDisplayName,
        agent.description,
        agent.expertise,
        agent.model,
        agent.role,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(lowerSearch);
    });
  }, [lowerSearch, search]);

  const libraryTeams = useMemo(() => {
    return gbautoLibrary.teams.filter((team) => {
      if (!search) return true;
      const teamAgents = gbautoLibrary.agents
        .filter((agent) => agent.team === team.id)
        .map((agent) => `${agent.displayName} ${agent.description}`)
        .join(" ");
      return `${team.displayName} ${team.leader} ${team.description} ${teamAgents}`
        .toLowerCase()
        .includes(lowerSearch);
    });
  }, [lowerSearch, search]);

  const filteredAuraSkills = useMemo(() => {
    return auraSkills.skills.filter((skill) => {
      if (!search) return true;
      const haystack = [
        skill.title,
        skill.description,
        skill.category,
        skill.authorName,
        skill.repoOwner,
        skill.repoName,
        skill.contentPreview,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(lowerSearch);
    });
  }, [lowerSearch, search]);

  useLayoutEffect(() => {
    if (loading) {
      setAfterTitle(null);
      setEnd(null);
      return;
    }
    setAfterTitle(
      <span className="whitespace-nowrap text-xs text-muted-foreground">
        {t.skills.enabledOf
          .replace("{enabled}", String(enabledCount))
          .replace("{total}", String(skills.length))}
      </span>,
    );
    setEnd(
      <div className="relative w-full min-w-0 sm:max-w-xs">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
        <Input
          className="h-8 rounded-none pl-8 pr-7 text-xs"
          placeholder={t.common.search}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {search && (
          <Button
            ghost
            size="xs"
            className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            onClick={() => setSearch("")}
            aria-label={t.common.clear}
          >
            <X />
          </Button>
        )}
      </div>,
    );
    return () => {
      setAfterTitle(null);
      setEnd(null);
    };
  }, [enabledCount, loading, search, setAfterTitle, setEnd, skills.length, t]);

  const filteredToolsets = useMemo(() => {
    return toolsets.filter(
      (ts) =>
        !search ||
        ts.name.toLowerCase().includes(lowerSearch) ||
        ts.label.toLowerCase().includes(lowerSearch) ||
        ts.description.toLowerCase().includes(lowerSearch),
    );
  }, [toolsets, search, lowerSearch]);

  const galleryItems = useMemo<SkillsGalleryItem[]>(() => {
    if (view === "skills") {
      const source = isSearching ? searchMatchedSkills : activeSkills;
      return source.map((skill) => {
        const registryRow = findSkillRegistryRow(contractsIndex, skill.name);
        const relatedContracts = relatedContractsForSkill(
          contractsIndex,
          skill.name,
        );
        return {
          id: skill.name,
          title: skill.name,
          description: skill.description || t.skills.noDescription,
          category: prettyCategory(skill.category, t.common.general),
          imageUrl: skillGroupArtUrl(skill.category),
          meta: skill.enabled ? t.common.active : t.common.inactive,
          metrics: [
            { label: "Enabled", value: skill.enabled ? "Yes" : "No" },
            ...(skill.canopy ? [{ label: "Canopy", value: "Yes" }] : []),
            ...(registryRow?.status
              ? [{ label: "Registry", value: registryRow.status }]
              : []),
          ],
          tags: [
            skill.canopy ? "Canopy" : null,
            registryRow?.source_path,
            ...relatedContracts
              .slice(0, 2)
              .map((contract) => contract.object_name),
          ].filter(Boolean) as string[],
        };
      });
    }

    if (view === "toolsets") {
      return filteredToolsets.map((toolset) => ({
        id: toolset.name,
        title: toolset.label.replace(/^[\p{Emoji}\s]+/u, "").trim() || toolset.name,
        description: toolset.description,
        category: "Toolset",
        imageUrl: skillGroupArtUrl(toolset.name),
        meta: toolset.enabled ? t.common.active : t.common.inactive,
        metrics: [
          { label: "Configured", value: toolset.configured ? "Yes" : "No" },
          { label: "Tools", value: String(toolset.tools.length) },
        ],
        tags: toolset.tools.slice(0, 5),
      }));
    }

    if (view === "library") {
      return libraryTeams.map((team) => ({
        id: team.id,
        title: team.displayName,
        description: team.description,
        category: team.kind,
        imageUrl: libraryTeamArtUrl(team),
        meta: `Lead: ${team.leader}`,
        metrics: [
          { label: "Agents", value: String(team.agentCount) },
          { label: "Seniors", value: String(team.roleCounts.seniors) },
          { label: "Juniors", value: String(team.roleCounts.juniors) },
        ],
        tags: [team.sourcePath],
      }));
    }

    if (view === "prompt-cards") {
      return libraryAgents.map((agent) => {
        const art = individualAgentArt(agent);
        return {
          id: agent.id,
          title: agent.displayName,
          description: agent.description,
          category: agent.teamDisplayName,
          imageUrl: versionedArtPath(art.publicPath),
          meta: agent.model || "model tbd",
          metrics: [
            { label: "Role", value: agent.rosterRole.replace(/s$/, "") },
            { label: "Provider", value: agent.provider || "tbd" },
          ],
          tags: [agent.expertise, agent.sourcePath, `Art: ${art.artId}`].filter(
            Boolean,
          ) as string[],
        };
      });
    }

    if (view === "aura-skills") {
      const auraSource: readonly AuraSkill[] = filteredAuraSkills;
      return auraSource.map((skill) => ({
        id: skill.id,
        title: skill.title,
        description: skill.description,
        category: prettyCategory(skill.category, "UI"),
        href: skill.sourceUrl || "https://www.aura.build/skills",
        imageUrl: skill.avatarUrl,
        meta: skill.authorName,
        metrics: [
          { label: "Views", value: formatCompact(skill.views) },
          { label: "Uses", value: formatCompact(skill.uses) },
        ],
        tags: [
          skill.repoOwner && skill.repoName
            ? `${skill.repoOwner}/${skill.repoName}`
            : "Aura source",
          skill.featured ? "Featured" : null,
        ].filter(Boolean) as string[],
      }));
    }

    return [];
  }, [
    activeSkills,
    contractsIndex,
    filteredAuraSkills,
    filteredToolsets,
    isSearching,
    libraryAgents,
    libraryTeams,
    searchMatchedSkills,
    t.common.active,
    t.common.general,
    t.common.inactive,
    t.skills.noDescription,
    view,
  ]);

  const galleryEmptyLabel = useMemo(() => {
    if (view === "toolsets") return t.skills.noToolsetsMatch;
    if (view === "aura-skills") return "No Aura skills match the current filter.";
    if (view === "skills") {
      return skills.length === 0 ? t.skills.noSkills : t.skills.noSkillsMatch;
    }
    return "No records match the current filter.";
  }, [
    skills.length,
    t.skills.noSkills,
    t.skills.noSkillsMatch,
    t.skills.noToolsetsMatch,
    view,
  ]);

  /* ---- Loading ---- */
  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <PluginSlot name="skills:top" />
      <Toast toast={toast} />

      <div className="flex flex-col sm:flex-row sm:items-start gap-4">
        <aside aria-label={t.skills.title} className="sm:w-56 sm:shrink-0">
          <div className="sm:sticky sm:top-0">
            <div className="flex flex-col rounded-none border border-border bg-muted/20">
              <div className="hidden sm:flex items-center gap-2 px-3 py-2 border-b border-border">
                <Filter className="h-3 w-3 text-muted-foreground" />
                <span className="font-mondwest text-[0.65rem] tracking-[0.12em] uppercase text-muted-foreground">
                  {t.skills.filters}
                </span>
              </div>

              <div className="flex sm:flex-col gap-1 overflow-x-auto sm:overflow-x-visible scrollbar-none p-2">
                <PanelItem
                  icon={Package}
                  label={`${t.skills.all} (${skills.length})`}
                  active={view === "skills" && !isSearching}
                  onClick={() => {
                    setView("skills");
                    setActiveCategory(null);
                    setSearch("");
                  }}
                />
                <PanelItem
                  icon={Wrench}
                  label={`${t.skills.toolsets} (${toolsets.length})`}
                  active={view === "toolsets"}
                  onClick={() => {
                    setView("toolsets");
                    setSearch("");
                  }}
                />
                <PanelItem
                  icon={BookOpen}
                  label={`AI Library (${gbautoLibrary.summary.teams})`}
                  active={view === "library"}
                  onClick={() => {
                    setView("library");
                    setActiveCategory(null);
                  }}
                />
                <PanelItem
                  icon={Sparkles}
                  label={`Prompt Cards (${gbautoLibrary.summary.agents})`}
                  active={view === "prompt-cards"}
                  onClick={() => {
                    setView("prompt-cards");
                    setActiveCategory(null);
                  }}
                />
                <PanelItem
                  icon={Paintbrush}
                  label={`Aura Skills (${auraSkills.summary.total})`}
                  active={view === "aura-skills"}
                  onClick={() => {
                    setView("aura-skills");
                    setActiveCategory(null);
                  }}
                />
              </div>

              {view === "skills" &&
                !isSearching &&
                allCategories.length > 0 && (
                  <div className="hidden sm:flex flex-col border-t border-border">
                    <div className="px-3 pt-2 pb-1 font-mondwest text-[0.6rem] tracking-[0.12em] uppercase text-muted-foreground/70">
                      {t.skills.categories}
                    </div>
                    <div className="flex flex-col p-2 pt-1 gap-px max-h-[calc(100vh-340px)] overflow-y-auto">
                      {allCategories.map(({ key, name, count }) => {
                        const isActive = activeCategory === key;

                        return (
                          <ListItem
                            key={key}
                            active={isActive}
                            onClick={() =>
                              setActiveCategory(isActive ? null : key)
                            }
                            className="rounded-none px-2 py-1 text-[11px]"
                          >
                            <span className="flex-1 truncate">{name}</span>
                            <span
                              className={`text-[10px] tabular-nums ${
                                isActive
                                  ? "text-foreground/60"
                                  : "text-muted-foreground/50"
                              }`}
                            >
                              {count}
                            </span>
                          </ListItem>
                        );
                      })}
                    </div>
                  </div>
                )}
            </div>
          </div>
        </aside>

        <div className="flex-1 min-w-0">
          <SkillsAnalyticsPane
            analyticsError={analyticsError}
            analyticsLoading={analyticsLoading}
            displayMode={displayMode}
            index={contractsIndex}
            onDisplayModeChange={setDisplayMode}
            skillFailureRows={skillFailureRows}
            skillRunRows={skillRunRows}
            skills={skills}
            summary={supabaseSummary}
            tenant={tenant}
            view={view}
          />
          {displayMode !== "current" ? (
            <SkillsTemplateGallery
              emptyLabel={galleryEmptyLabel}
              items={galleryItems}
              mode={displayMode}
            />
          ) : isSearching ? (
            <Card className="rounded-none">
              <CardHeader className="py-3 px-4">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <Search className="h-4 w-4" />
                    {t.skills.title}
                  </CardTitle>
                  <Badge tone="secondary" className="text-[10px]">
                    {t.skills.resultCount
                      .replace("{count}", String(searchMatchedSkills.length))
                      .replace(
                        "{s}",
                        searchMatchedSkills.length !== 1 ? "s" : "",
                      )}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="px-4 pb-4">
                {searchMatchedSkills.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-8">
                    {t.skills.noSkillsMatch}
                  </p>
                ) : (
                  <div className="grid gap-1">
                    {searchMatchedSkills.map((skill) => (
                      <SkillRow
                        key={skill.name}
                        contractsIndex={contractsIndex}
                        skill={skill}
                        toggling={togglingSkills.has(skill.name)}
                        onToggle={() => handleToggleSkill(skill)}
                        noDescriptionLabel={t.skills.noDescription}
                      />
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          ) : view === "skills" ? (
            /* Skills list */
            <Card className="rounded-none">
              <CardHeader className="py-3 px-4">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <Package className="h-4 w-4" />
                    {activeCategory
                      ? prettyCategory(
                          activeCategory === "__none__" ? null : activeCategory,
                          t.common.general,
                        )
                      : t.skills.all}
                  </CardTitle>
                  <Badge tone="secondary" className="text-[10px]">
                    {t.skills.skillCount
                      .replace("{count}", String(activeSkills.length))
                      .replace("{s}", activeSkills.length !== 1 ? "s" : "")}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="px-4 pb-4">
                {activeSkills.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-8">
                    {skills.length === 0
                      ? t.skills.noSkills
                      : t.skills.noSkillsMatch}
                  </p>
                ) : (
                  <div className="grid gap-1">
                    {activeSkills.map((skill) => (
                      <SkillRow
                        key={skill.name}
                        contractsIndex={contractsIndex}
                        skill={skill}
                        toggling={togglingSkills.has(skill.name)}
                        onToggle={() => handleToggleSkill(skill)}
                        noDescriptionLabel={t.skills.noDescription}
                      />
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          ) : view === "toolsets" ? (
            /* Toolsets grid */
            <>
              {filteredToolsets.length === 0 ? (
                <Card className="rounded-none">
                  <CardContent className="py-8 text-center text-sm text-muted-foreground">
                    {t.skills.noToolsetsMatch}
                  </CardContent>
                </Card>
              ) : (
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {filteredToolsets.map((ts) => {
                    const TsIcon = toolsetIcon(ts.name);
                    const labelText =
                      ts.label.replace(/^[\p{Emoji}\s]+/u, "").trim() ||
                      ts.name;

                    return (
                      <Card key={ts.name} className="relative rounded-none">
                        <CardContent className="py-4">
                          <div className="flex items-start gap-3">
                            <TsIcon className="h-5 w-5 text-muted-foreground shrink-0 mt-0.5" />
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 mb-1">
                                <span className="font-medium text-sm">
                                  {labelText}
                                </span>
                                <Badge
                                  tone={ts.enabled ? "success" : "outline"}
                                  className="text-[10px]"
                                >
                                  {ts.enabled
                                    ? t.common.active
                                    : t.common.inactive}
                                </Badge>
                              </div>
                              <p className="text-xs text-muted-foreground mb-2">
                                {ts.description}
                              </p>
                              {ts.enabled && !ts.configured && (
                                <p className="text-[10px] text-amber-300/80 mb-2">
                                  {t.skills.setupNeeded}
                                </p>
                              )}
                              {ts.tools.length > 0 && (
                                <div className="flex flex-wrap gap-1">
                                  {ts.tools.map((tool) => (
                                    <Badge
                                      key={tool}
                                      tone="secondary"
                                      className="text-[10px] font-mono"
                                    >
                                      {tool}
                                    </Badge>
                                  ))}
                                </div>
                              )}
                              {ts.tools.length === 0 && (
                                <span className="text-[10px] text-muted-foreground/60">
                                  {ts.enabled
                                    ? t.skills.toolsetLabel.replace(
                                        "{name}",
                                        ts.name,
                                      )
                                    : t.skills.disabledForCli}
                                </span>
                              )}
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    );
                  })}
                </div>
              )}
            </>
          ) : view === "library" ? (
            <AiLibraryTeamsView agents={libraryAgents} contractsIndex={contractsIndex} teams={libraryTeams} />
          ) : view === "aura-skills" ? (
            <AuraSkillsView contractsIndex={contractsIndex} skills={filteredAuraSkills} />
          ) : (
            <PromptCardsView agents={libraryAgents} contractsIndex={contractsIndex} />
          )}
        </div>
      </div>
      <PluginSlot name="skills:bottom" />
    </div>
  );
}

function SkillsAnalyticsPane({
  analyticsError,
  analyticsLoading,
  displayMode,
  index,
  onDisplayModeChange,
  skillFailureRows,
  skillRunRows,
  skills,
  summary,
  tenant,
  view,
}: {
  analyticsError: string | null;
  analyticsLoading: boolean;
  displayMode: SkillsDisplayMode;
  index: SupabaseContractsIndex | null;
  onDisplayModeChange: (mode: SkillsDisplayMode) => void;
  skillFailureRows: readonly SupabaseLogRow[];
  skillRunRows: readonly SupabaseLogRow[];
  skills: readonly SkillInfo[];
  summary: SupabaseLogsSummaryResponse | null;
  tenant: string;
  view: "skills" | "toolsets" | "library" | "prompt-cards" | "aura-skills";
}) {
  const registryRows = index?.skills_registry ?? [];
  const activeRegistryRows = registryRows.filter(
    (row) => row.status === "active",
  ).length;
  const enabledSkills = skills.filter((skill) => skill.enabled).length;
  const counts = summary?.counts ?? {};
  const traceTotals = summary?.trace_totals ?? {};
  const coverage = summary?.trace_coverage ?? {};
  const joinCandidates = numberFromUnknown(coverage.obs_join_candidates);
  const withTrace = numberFromUnknown(coverage.obs_with_trace);
  const traceCoverage =
    joinCandidates > 0 ? Math.round((withTrace / joinCandidates) * 100) : null;
  const latestSkillRun = latestDateFromRows(skillRunRows, "started_at");
  const skillRunFailures = skillRunRows.filter(
    (row) => String(row.status_family ?? "").toLowerCase() === "failed",
  ).length;
  const skillFailureTotal = skillRunFailures || skillFailureRows.length;
  const skillSuccessTotal = Math.max(skillRunRows.length - skillFailureTotal, 0);
  const registryRatio =
    registryRows.length > 0
      ? Math.round((activeRegistryRows / registryRows.length) * 100)
      : null;
  const enabledRatio =
    skills.length > 0 ? Math.round((enabledSkills / skills.length) * 100) : null;
  const successRatio =
    skillRunRows.length > 0
      ? Math.round((skillSuccessTotal / skillRunRows.length) * 100)
      : null;
  const liveState = analyticsLoading
    ? "Loading Supabase"
    : analyticsError
      ? "Supabase unavailable"
      : "Live Supabase";

  return (
    <section className="skills-analytics-pane">
      <div className="skills-analytics-main">
        <div className="skills-analytics-title">
          <span className="library-eyebrow">{liveState}</span>
          <h2>Skills Analytics</h2>
          <p>
            Client scope: {tenant}. Recent skill runs, trace coverage, registry state, and operational
            contracts from the Supabase-backed Hermes observability tables.
          </p>
        </div>

        <div className="skills-analytics-rows">
          <AnalyticsRow
            detail={`${formatCompact(skillRunRows.length)} runs in 14d; ${formatCompact(skillFailureTotal)} failed`}
            label="Run health"
            progress={successRatio}
            value={`${formatCompact(skillSuccessTotal)} succeeded`}
          />
          <AnalyticsRow
            detail={`${formatCompact(activeRegistryRows)} active registry rows; ${formatCompact(registryRows.length)} total`}
            label="Registry state"
            progress={registryRatio}
            value={
              registryRatio === null
                ? "No registry rows"
                : `${registryRatio}% active`
            }
          />
          <AnalyticsRow
            detail={`${formatCompact(enabledSkills)} enabled locally; ${formatCompact(skills.length)} installed`}
            label="Local enablement"
            progress={enabledRatio}
            value={
              enabledRatio === null ? "No local skills" : `${enabledRatio}% enabled`
            }
          />
          <AnalyticsRow
            detail={`${formatCompact(numberFromUnknown(counts.traces))} traces; ${formatCompact(numberFromUnknown(traceTotals.tokens))} tokens; $${numberFromUnknown(traceTotals.cost).toFixed(2)} cost`}
            label="Langfuse telemetry"
            progress={traceCoverage}
            value={
              traceCoverage === null
                ? "Coverage pending"
                : `${withTrace}/${joinCandidates} joined`
            }
          />
          <AnalyticsRow
            detail={summary?.generated_at ? `Supabase summary ${formatShortDate(summary.generated_at)}` : "Waiting on Supabase summary"}
            label="Freshness"
            value={
              latestSkillRun
                ? `Latest skill run ${formatShortDate(latestSkillRun)}`
                : "No recent skill run"
            }
          />
        </div>

        {analyticsError ? (
          <div className="skills-analytics-error">
            {analyticsError.replace(/^Error:\s*/, "")}
          </div>
        ) : null}
      </div>

      <div className="skills-analytics-side">
        <SkillsDisplayModeSwitcher
          mode={displayMode}
          onChange={onDisplayModeChange}
        />
        <ContractObjectStrip
          contracts={contractsForObjects(index, [
            "ops_skills_registry",
            "skill_runs",
            "skill_outputs",
            "ops_skill_activity",
            "obs_smoke_skill_runs",
            "obs_smoke_skill_metrics_daily",
          ])}
        />
        <div className="skills-analytics-footnote">
          <span>{view.replace("-", " ")}</span>
          <span>{summary?.generated_at ? formatShortDate(summary.generated_at) : "pending"}</span>
        </div>
      </div>
    </section>
  );
}

function AnalyticsRow({
  detail,
  label,
  progress,
  value,
}: {
  detail: string;
  label: string;
  progress?: number | null;
  value: string;
}) {
  const boundedProgress =
    typeof progress === "number" && Number.isFinite(progress)
      ? Math.max(0, Math.min(100, progress))
      : null;

  return (
    <article className="skills-analytics-row">
      <div className="skills-analytics-row-copy">
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
      {boundedProgress !== null ? (
        <div
          aria-label={`${label}: ${boundedProgress}%`}
          className="skills-analytics-bar"
          role="meter"
          aria-valuemax={100}
          aria-valuemin={0}
          aria-valuenow={boundedProgress}
        >
          <i style={{ width: `${boundedProgress}%` }} />
        </div>
      ) : null}
    </article>
  );
}

function SkillsDisplayModeSwitcher({
  mode,
  onChange,
}: {
  mode: SkillsDisplayMode;
  onChange: (mode: SkillsDisplayMode) => void;
}) {
  const options: { label: string; mode: SkillsDisplayMode }[] = [
    { label: "Current", mode: "current" },
    { label: "Image gallery", mode: "image-gallery" },
    { label: "Detail gallery", mode: "detail-gallery" },
  ];

  return (
    <div className="skills-template-toolbar" aria-label="Skills view type">
      <div>
        <span className="library-eyebrow">View Type</span>
        <p>Approved GBauto catalog templates</p>
      </div>
      <div className="skills-template-switcher" role="group">
        {options.map((option) => (
          <button
            aria-pressed={mode === option.mode}
            className={cn(
              "skills-template-switch",
              mode === option.mode && "is-active",
            )}
            key={option.mode}
            onClick={() => onChange(option.mode)}
            type="button"
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function SkillsTemplateGallery({
  emptyLabel,
  items,
  mode,
}: {
  emptyLabel: string;
  items: readonly SkillsGalleryItem[];
  mode: Exclude<SkillsDisplayMode, "current">;
}) {
  if (!items.length) {
    return (
      <Card className="rounded-none">
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          {emptyLabel}
        </CardContent>
      </Card>
    );
  }

  if (mode === "image-gallery") {
    return (
      <div className="skills-template-grid skills-template-grid--image">
        {items.map((item) => (
          <article className="skills-template-image-card" key={item.id}>
            <div className="skills-template-image-wrap">
              {item.imageUrl ? (
                <img alt="" decoding="async" loading="lazy" src={item.imageUrl} />
              ) : (
                <LayoutGrid className="h-7 w-7" />
              )}
            </div>
            <div className="skills-template-card-body">
              <div className="library-card-topline">
                <span>{item.category || "Catalog"}</span>
                <span>{item.meta || "record"}</span>
              </div>
              <h3>{item.title}</h3>
              <p>{item.description}</p>
              <GalleryMetricRow metrics={item.metrics} />
              <GalleryTagRow tags={item.tags} />
              {item.href ? (
                <a
                  className="skills-template-card-link"
                  href={item.href}
                  rel="noreferrer"
                  target="_blank"
                >
                  Open source
                </a>
              ) : null}
            </div>
          </article>
        ))}
      </div>
    );
  }

  return (
    <div className="skills-template-grid skills-template-grid--detail">
      {items.map((item) => (
        <article className="skills-template-detail-card" key={item.id}>
          <div className="library-card-topline">
            <span>{item.category || "Catalog"}</span>
            <span>{item.meta || "record"}</span>
          </div>
          <h3>{item.title}</h3>
          <p>{item.description}</p>
          <GalleryMetricRow metrics={item.metrics} />
          <GalleryTagRow tags={item.tags} />
          {item.href ? (
            <a
              className="skills-template-card-link"
              href={item.href}
              rel="noreferrer"
              target="_blank"
            >
              Open source
            </a>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function GalleryMetricRow({
  metrics,
}: {
  metrics?: SkillsGalleryItem["metrics"];
}) {
  if (!metrics?.length) return null;
  return (
    <div className="skills-template-metric-row">
      {metrics.slice(0, 3).map((metric) => (
        <span key={`${metric.label}:${metric.value}`}>
          <b>{metric.value}</b>
          {metric.label}
        </span>
      ))}
    </div>
  );
}

function GalleryTagRow({ tags }: { tags?: SkillsGalleryItem["tags"] }) {
  const visibleTags = tags?.filter(Boolean).slice(0, 4) ?? [];
  if (!visibleTags.length) return null;
  return (
    <div className="skills-template-tag-row">
      {visibleTags.map((tag) => (
        <span key={tag}>{tag}</span>
      ))}
    </div>
  );
}

function SkillRow({
  contractsIndex,
  skill,
  toggling,
  onToggle,
  noDescriptionLabel,
}: SkillRowProps) {
  const registryRow = findSkillRegistryRow(contractsIndex, skill.name);
  const relatedContracts = relatedContractsForSkill(contractsIndex, skill.name);
  return (
    <div className="group flex items-start gap-3 px-3 py-2.5 transition-colors hover:bg-muted/40">
      <div className="pt-0.5 shrink-0">
        <Switch
          checked={skill.enabled}
          onCheckedChange={onToggle}
          disabled={toggling}
        />
      </div>
      <img
        alt=""
        aria-hidden="true"
        className="skill-row-art"
        decoding="async"
        loading="lazy"
        src={skillGroupArtUrl(skill.category)}
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span
            className={`font-mono-ui text-sm ${
              skill.enabled ? "text-foreground" : "text-muted-foreground"
            }`}
          >
            {skill.name}
          </span>
          <Badge tone="secondary" className="text-[9px]">
            {prettyCategory(skill.category, "General")}
          </Badge>
          {skill.canopy && (
            <span title="Part of the Canopy prompt system">
              <Badge tone="outline" className="text-[9px]">
                🌿 Canopy
              </Badge>
            </span>
          )}
        </div>
        <p className="text-xs text-muted-foreground leading-relaxed line-clamp-2">
          {skill.description || noDescriptionLabel}
        </p>
        {registryRow || relatedContracts.length ? (
          <div className="skill-contract-row">
            {registryRow?.source_path ? <span>{registryRow.source_path}</span> : null}
            {registryRow?.status ? <span>{registryRow.status}</span> : null}
            {relatedContracts.slice(0, 3).map((contract) => (
              <span key={contract.object_name ?? contract.domain ?? "contract"}>
                {contract.object_name}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function PanelItem({ active, icon: Icon, label, onClick }: PanelItemProps) {
  return (
    <ListItem
      active={active}
      onClick={onClick}
      className={cn(
        "rounded-none whitespace-nowrap px-2.5 py-1.5",
        "font-mondwest text-[0.7rem] tracking-[0.08em] uppercase",
        active && "bg-foreground/90 text-background hover:text-background",
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="flex-1 truncate">{label}</span>
    </ListItem>
  );
}

interface PanelItemProps {
  active: boolean;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  onClick: () => void;
}

interface SkillRowProps {
  contractsIndex: SupabaseContractsIndex | null;
  noDescriptionLabel: string;
  onToggle: () => void;
  skill: SkillInfo;
  toggling: boolean;
}

type LibraryAgent = (typeof gbautoLibrary.agents)[number];
type LibraryTeam = (typeof gbautoLibrary.teams)[number];
type AuraSkill = (typeof auraSkills.skills)[number];

function versionedArtPath(publicPath: string): string {
  return `${publicPath}?v=${skillIndividualArt.version}`;
}

function individualAgentArt(agent: LibraryAgent): IndividualArtEntry {
  const entry =
    skillIndividualArt.agents[
      agent.id as keyof typeof skillIndividualArt.agents
    ] as IndividualArtEntry | undefined;
  if (entry) return entry;
  const key = skillArtKey(TEAM_ART_GROUPS[agent.team] ?? agent.teamDisplayName);
  return {
    artId: `group-${key}`,
    publicPath: `/skill-art/${key}.jpg`,
    sourceFile: `${key}.jpg`,
    sourceGroup: "group",
  };
}

function ContractObjectStrip({
  contracts,
}: {
  contracts: readonly SupabaseContractRow[];
}) {
  if (!contracts.length) return null;

  return (
    <div className="contract-object-strip">
      {contracts.slice(0, 6).map((contract) => (
        <article key={contract.object_name ?? `${contract.domain}:${contract.owner_agent}`}>
          <div className="library-card-topline">
            <span>{contract.domain ?? "domain tbd"}</span>
            <span>{contract.access_model ?? "access tbd"}</span>
          </div>
          <strong>{contract.object_name ?? "unnamed object"}</strong>
          <p>{contract.notes || contract.read_path || contract.write_path || "No contract notes yet."}</p>
        </article>
      ))}
    </div>
  );
}

function libraryTeamArtUrl(team: LibraryTeam): string {
  return skillGroupArtUrl(TEAM_ART_GROUPS[team.id] ?? team.artSeed);
}

function AiLibraryTeamsView({
  agents,
  contractsIndex,
  teams,
}: {
  agents: readonly LibraryAgent[];
  contractsIndex: SupabaseContractsIndex | null;
  teams: readonly LibraryTeam[];
}) {
  const agentsByTeam = useMemo(() => {
    const map = new Map<string, LibraryAgent[]>();
    for (const agent of agents) {
      const list = map.get(agent.team) ?? [];
      list.push(agent);
      map.set(agent.team, list);
    }
    return map;
  }, [agents]);

  return (
    <section className="library-page">
      <div className="library-hero">
        <div>
          <span className="library-eyebrow">Canonical AI Library</span>
          <h2>Skill Teams</h2>
          <p>
            Imported from <code>ai-library/teams/_catalog.md</code> and every
            roster under <code>ai-library/teams/*/_roster.yaml</code>.
          </p>
        </div>
        <div className="library-hero-stats">
          <strong>{gbautoLibrary.summary.teams}</strong>
          <span>teams</span>
          <strong>{gbautoLibrary.summary.agents}</strong>
          <span>agents</span>
        </div>
      </div>
      <ContractObjectStrip
        contracts={contractsForObjects(contractsIndex, [
          "agent_runs",
          "kanban_tasks",
          "prd_kanban_dispatch_links",
          "langfuse_traces",
          "skill_runs",
        ])}
      />

      <div className="library-team-grid">
        {teams.map((team) => {
          const teamAgents = agentsByTeam.get(team.id) ?? [];
          return (
            <article className="library-team-card" key={team.id}>
              <LibraryArt
                imageUrl={libraryTeamArtUrl(team)}
                label={team.displayName}
                seed={team.artSeed}
              />
              <div className="library-team-card-body">
                <div className="library-card-topline">
                  <span>{team.kind}</span>
                  <span>{team.agentCount} agents</span>
                </div>
                <h3>{team.displayName}</h3>
                <p>{team.description}</p>
                <div className="library-team-meta">
                  <span>Lead: {team.leader}</span>
                  <span>{team.roleCounts.seniors} seniors</span>
                  <span>{team.roleCounts.juniors} juniors</span>
                </div>
                <div className="library-mini-roster">
                  {teamAgents.slice(0, 5).map((agent) => (
                    <span key={agent.id}>{agent.displayName}</span>
                  ))}
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function PromptCardsView({
  agents,
  contractsIndex,
}: {
  agents: readonly LibraryAgent[];
  contractsIndex: SupabaseContractsIndex | null;
}) {
  return (
    <section className="library-page">
      <div className="library-toolbar">
        <div>
          <span className="library-eyebrow">Prompt Surface</span>
          <h2>Agent Prompt Cards</h2>
        </div>
        <Badge tone="secondary" className="text-[10px]">
          {agents.length} rendered
        </Badge>
      </div>
      <ContractObjectStrip
        contracts={contractsForObjects(contractsIndex, [
          "agent_runs",
          "prompt_profile_applies",
          "langfuse_traces",
          "skill_runs",
        ])}
      />
      <div className="library-prompt-grid">
        {agents.map((agent) => {
          const art = individualAgentArt(agent);
          return (
            <article className="library-prompt-card" key={agent.id}>
              <LibraryArt
                artId={art.artId}
                imageUrl={versionedArtPath(art.publicPath)}
                label={agent.displayName}
                seed={agent.artSeed}
              />
              <div className="library-prompt-body">
                <div className="library-card-topline">
                  <span>{agent.teamDisplayName}</span>
                  <span>{agent.model || "model tbd"}</span>
                </div>
                <h3>{agent.displayName}</h3>
                <p>{agent.description}</p>
                <div className="library-tag-row">
                  <span>{agent.rosterRole.replace(/s$/, "")}</span>
                  {agent.provider && <span>{agent.provider}</span>}
                  {mtgCardName(agent.mtg) ? (
                    <span>{mtgCardName(agent.mtg)}</span>
                  ) : (
                    <span>{art.sourceGroup}</span>
                  )}
                  <span>Art: {art.artId}</span>
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function AuraSkillsView({
  contractsIndex,
  skills,
}: {
  contractsIndex: SupabaseContractsIndex | null;
  skills: readonly AuraSkill[];
}) {
  const [category, setCategory] = useState<string | null>(null);
  const categories = useMemo(() => {
    const counts = new Map<string, number>();
    for (const skill of auraSkills.skills) {
      counts.set(skill.category, (counts.get(skill.category) ?? 0) + 1);
    }
    return [...counts.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([key, count]) => ({ key, count }));
  }, []);
  const visibleSkills = useMemo(() => {
    if (!category) return skills;
    return skills.filter((skill) => skill.category === category);
  }, [category, skills]);

  return (
    <section className="library-page aura-skills-page">
      <div className="library-hero aura-skills-hero">
        <div>
          <span className="library-eyebrow">Aura Skill UI Design System</span>
          <h2>Popular Aura Skills</h2>
          <p>
            Public Aura.build skill records indexed from the live skills feed
            for TAC Designer, Hermes Skills, and UI-agents design references.
          </p>
        </div>
        <div className="library-hero-stats aura-skills-stats">
          <strong>{auraSkills.summary.total}</strong>
          <span>skills</span>
          <strong>{formatCompact(auraSkills.summary.totalViews)}</strong>
          <span>views</span>
          <strong>{formatCompact(auraSkills.summary.totalUses)}</strong>
          <span>uses</span>
        </div>
      </div>
      <ContractObjectStrip
        contracts={contractsForObjects(contractsIndex, [
          "ops_skills_registry",
          "skill_runs",
          "obs_smoke_skill_runs",
          "obs_smoke_skill_metrics_daily",
        ])}
      />

      <div className="aura-skills-filter-row" aria-label="Aura skill filters">
        <button
          className={cn("aura-filter-pill", !category && "is-active")}
          onClick={() => setCategory(null)}
          type="button"
        >
          All
        </button>
        {categories.map((item) => (
          <button
            className={cn(
              "aura-filter-pill",
              category === item.key && "is-active",
            )}
            key={item.key}
            onClick={() => setCategory(category === item.key ? null : item.key)}
            type="button"
          >
            {prettyCategory(item.key, "UI")} <span>{item.count}</span>
          </button>
        ))}
      </div>

      {visibleSkills.length === 0 ? (
        <Card className="rounded-none">
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No Aura skills match the current filter.
          </CardContent>
        </Card>
      ) : (
        <div className="aura-skills-grid">
          {visibleSkills.map((skill) => (
              <article className="aura-skill-card" key={skill.id}>
                <div className="library-card-topline">
                  <span>{prettyCategory(skill.category, "UI")}</span>
                  <span>{skill.authorName}</span>
                </div>
                <h3>{skill.title}</h3>
                <p>{skill.description}</p>

                <div className="aura-skill-metrics">
                  <span>
                    <strong>{formatCompact(skill.views)}</strong>
                    views
                  </span>
                  <span>
                    <strong>{formatCompact(skill.uses)}</strong>
                    uses
                  </span>
                </div>

                <div className="library-tag-row">
                  {skill.repoOwner && skill.repoName ? (
                    <span>
                      {skill.repoOwner}/{skill.repoName}
                    </span>
                  ) : (
                    <span>Aura source</span>
                  )}
                  {skill.featured && <span>Featured</span>}
                </div>

                <p className="aura-skill-preview">{skill.contentPreview}</p>

                <a
                  className="aura-skill-link"
                  href={skill.sourceUrl || "https://www.aura.build/skills"}
                  rel="noreferrer"
                  target="_blank"
                >
                  Open source
                </a>
              </article>
          ))}
        </div>
      )}
    </section>
  );
}

function LibraryArt({
  artId,
  imageUrl,
  label,
  seed,
}: {
  artId?: string;
  imageUrl?: string;
  label: string;
  seed: string;
}) {
  const hue = hashSeed(seed) % 360;
  if (imageUrl) {
    return (
      <img
        alt={`${label} MTG art`}
        className="library-art"
        data-mtg-art-id={artId}
        decoding="async"
        loading="lazy"
        src={imageUrl}
        title={artId ? `MTG art ID: ${artId}` : undefined}
      />
    );
  }
  return (
    <div
      aria-label={`${label} generated art panel`}
      className="library-art library-art-fallback"
      role="img"
      style={{ "--library-hue": `${hue}deg` } as CSSProperties}
    >
      <LayoutGrid className="h-7 w-7" />
      <span>{label}</span>
    </div>
  );
}

function hashSeed(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return hash;
}

function mtgCardName(value: unknown) {
  if (!value || typeof value !== "object") return "";
  return (value as { card?: string }).card || "";
}

function formatCompact(value: number) {
  return new Intl.NumberFormat("en", {
    maximumFractionDigits: 1,
    notation: "compact",
  }).format(value);
}

function numberFromUnknown(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function latestDateFromRows(
  rows: readonly SupabaseLogRow[],
  key: string,
): string | null {
  let latest = 0;
  let latestRaw: string | null = null;
  for (const row of rows) {
    const value = row[key];
    if (typeof value !== "string") continue;
    const time = Date.parse(value);
    if (!Number.isFinite(time) || time <= latest) continue;
    latest = time;
    latestRaw = value;
  }
  return latestRaw;
}

function formatShortDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
  }).format(date);
}
