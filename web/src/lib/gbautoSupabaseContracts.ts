import { useEffect, useMemo, useState } from "react";

export interface SupabaseContractRow {
  access_model?: string | null;
  created_at?: string | null;
  domain?: string | null;
  lifecycle?: string | null;
  notes?: string | null;
  object_name?: string | null;
  object_type?: string | null;
  owner_agent?: string | null;
  read_path?: string | null;
  retention_policy?: string | null;
  schema_version?: number | null;
  updated_at?: string | null;
  write_path?: string | null;
}

export interface SupabaseSkillRegistryRow {
  description?: string | null;
  display_name?: string | null;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
  owner?: string | null;
  scope?: string | null;
  skill_name?: string | null;
  source_path?: string | null;
  status?: string | null;
  updated_at?: string | null;
}

export interface SupabaseTableCatalogRow {
  table_name?: string | null;
  table_type?: string | null;
}

export interface SupabaseContractsIndex {
  contracts: SupabaseContractRow[];
  generated_at?: string;
  project?: string;
  skills_registry: SupabaseSkillRegistryRow[];
  source?: string;
  tables: SupabaseTableCatalogRow[];
}

type WrappedRows<T> = T[] | { value?: T[] } | null | undefined;

const EMPTY_INDEX: SupabaseContractsIndex = {
  contracts: [],
  skills_registry: [],
  tables: [],
};

const SKILL_OBJECT_NAMES = [
  "ops_skills_registry",
  "skill_runs",
  "skill_outputs",
  "ops_skill_activity",
  "obs_smoke_skill_runs",
  "obs_smoke_skill_metrics_daily",
  "tac_component_retrievals",
];

const PROFILE_OBJECT_NAMES = [
  "agent_runs",
  "obs_agent_run_join",
  "obs_agent_cost_daily",
  "obs_recent_sessions",
  "langfuse_traces",
  "kanban_tasks",
  "prd_kanban_dispatch_links",
  "prompt_profile_applies",
  "ops_trace_coverage",
];

function normalizeRows<T>(value: WrappedRows<T>): T[] {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.value)) return value.value;
  return [];
}

function normalizeText(value: string | null | undefined) {
  return (value ?? "").toLowerCase();
}

function includesToken(value: string | null | undefined, token: string) {
  if (!token) return false;
  return normalizeText(value).includes(token.toLowerCase());
}

function uniqueContracts(rows: SupabaseContractRow[]) {
  const seen = new Set<string>();
  return rows.filter((row) => {
    const key = row.object_name ?? JSON.stringify(row);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function useSupabaseContractsIndex() {
  const [index, setIndex] = useState<SupabaseContractsIndex | null>(null);

  useEffect(() => {
    void fetch("/gbauto-supabase/contracts.json")
      .then((response) => response.json())
      .then((data: {
        contracts?: WrappedRows<SupabaseContractRow>;
        generated_at?: string;
        project?: string;
        skills_registry?: WrappedRows<SupabaseSkillRegistryRow>;
        source?: string;
        tables?: WrappedRows<SupabaseTableCatalogRow>;
      }) =>
        setIndex({
          contracts: normalizeRows(data.contracts),
          generated_at: data.generated_at,
          project: data.project,
          skills_registry: normalizeRows(data.skills_registry),
          source: data.source,
          tables: normalizeRows(data.tables),
        }),
      )
      .catch(() => setIndex(EMPTY_INDEX));
  }, []);

  return index;
}

export function useContractMap(index: SupabaseContractsIndex | null) {
  return useMemo(() => {
    const map = new Map<string, SupabaseContractRow>();
    for (const contract of index?.contracts ?? []) {
      if (contract.object_name) map.set(contract.object_name, contract);
    }
    return map;
  }, [index]);
}

export function contractStats(index: SupabaseContractsIndex | null) {
  const contracts = index?.contracts ?? [];
  const domains = new Set(contracts.map((row) => row.domain).filter(Boolean));
  const owners = new Set(contracts.map((row) => row.owner_agent).filter(Boolean));
  const anonReadable = contracts.filter((row) => row.access_model === "anon_read").length;
  return {
    anonReadable,
    contracts: contracts.length,
    domains: domains.size,
    owners: owners.size,
    registrySkills: index?.skills_registry.length ?? 0,
    tableObjects: index?.tables.length ?? 0,
  };
}

export function contractsForObjects(
  index: SupabaseContractsIndex | null,
  objectNames: readonly string[],
) {
  const wanted = new Set(objectNames);
  return (index?.contracts ?? []).filter((row) => row.object_name && wanted.has(row.object_name));
}

export function relatedContractsForSkill(
  index: SupabaseContractsIndex | null,
  skillName: string,
) {
  const normalized = skillName.toLowerCase();
  const direct = contractsForObjects(index, SKILL_OBJECT_NAMES);
  const textMatches = (index?.contracts ?? []).filter((row) =>
    [
      row.object_name,
      row.domain,
      row.owner_agent,
      row.read_path,
      row.write_path,
      row.notes,
    ].some((value) => includesToken(value, normalized)),
  );
  return uniqueContracts([...textMatches, ...direct]).slice(0, 8);
}

export function relatedContractsForProfile(
  index: SupabaseContractsIndex | null,
  profileId: string,
  teamId?: string,
) {
  const normalizedProfile = profileId.toLowerCase();
  const normalizedTeam = (teamId ?? "").toLowerCase();
  const direct = contractsForObjects(index, PROFILE_OBJECT_NAMES);
  const textMatches = (index?.contracts ?? []).filter((row) =>
    [
      row.object_name,
      row.domain,
      row.owner_agent,
      row.read_path,
      row.write_path,
      row.notes,
    ].some(
      (value) =>
        includesToken(value, normalizedProfile) ||
        Boolean(normalizedTeam && includesToken(value, normalizedTeam)),
    ),
  );
  return uniqueContracts([...textMatches, ...direct]).slice(0, 8);
}

export function findSkillRegistryRow(
  index: SupabaseContractsIndex | null,
  skillName: string,
) {
  const normalized = skillName.toLowerCase();
  return (index?.skills_registry ?? []).find((row) =>
    [row.skill_name, row.display_name, row.source_path].some((value) =>
      includesToken(value, normalized),
    ),
  );
}
