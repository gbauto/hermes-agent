import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(webRoot, "..", "..", "gbautomation");
const teamsRoot = path.join(repoRoot, "ai-library", "teams");
const tacProfilesRoot = path.join(
  repoRoot,
  "second-brain",
  "systems",
  "hermes-profiles",
  "tac",
  "profiles",
);
const profileTemplatesRoot = path.join(
  repoRoot,
  "resources",
  "skills",
  "hermes-profile-templates",
);
const outputPath = path.join(webRoot, "src", "generated", "gbautoLibrary.ts");

const PROFILE_MTG_CARD_NAMES = {
  "tac-architect": "Arcum Dagsson",
  "tac-artifact-generator": "Saheeli, Sublime Artificer",
  "tac-builder": "Pia and Kiran Nalaar",
  "tac-director": "Grand Arbiter Augustin IV",
  "tac-lead": "Arcanis the Omnipotent",
  "tac-ops": "Solemn Simulacrum",
  "tac-researcher": "Archivist",
  "tac-self-improve": "Evolution Sage",
  "tac-validator": "Eight-and-a-Half-Tails",
  "chief-of-staff": "Captain Sisay",
  "client-orchestrator": "Teysa, Orzhov Scion",
  "content-creator": "Jhoira of the Ghitu",
  "database-manager": "Darksteel Forge",
  dbforge: "Gilded Lotus",
  ecom: "Merchant Raiders",
  "gbauto-intel": "Tamiyo, Field Researcher",
  gbauto: "Weatherlight",
  gbautomation: "Foundry Inspector",
  "ops-director": "Odric, Master Tactician",
  "research-analyst": "Azami, Lady of Scrolls",
  "scorecard-analyst": "Mentor of the Meek",
  "sprint-manager": "Aurelia, Exemplar of Justice",
};

const COLOR_NAMES = {
  B: "Black",
  G: "Green",
  R: "Red",
  U: "Blue",
  W: "White",
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readText(filePath) {
  return fs.readFileSync(filePath, "utf8");
}

function repoRelative(filePath) {
  return path.relative(repoRoot, filePath).replaceAll(path.sep, "/");
}

function stripQuotes(value) {
  const trimmed = String(value ?? "").trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function slugify(value) {
  return stripQuotes(value)
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function titleFromSlug(value) {
  return String(value)
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function parseFrontmatter(text) {
  if (!text.startsWith("---")) return { data: {}, body: text };
  const end = text.indexOf("\n---", 3);
  if (end === -1) return { data: {}, body: text };
  const yaml = text.slice(3, end).trim();
  const body = text.slice(end + 4).trim();
  const data = {};
  const lines = yaml.split(/\r?\n/);
  let activeKey = null;

  for (const rawLine of lines) {
    const line = rawLine.replace(/\r$/, "");
    if (!line.trim() || line.trimStart().startsWith("#")) continue;

    const listMatch = line.match(/^\s*-\s+(.+)$/);
    if (listMatch && activeKey) {
      data[activeKey] ||= [];
      data[activeKey].push(stripQuotes(listMatch[1]));
      continue;
    }

    const kv = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
    if (!kv) continue;
    const [, key, rawValue] = kv;
    activeKey = key;
    const value = rawValue.trim();
    if (!value) {
      data[key] = [];
    } else if (value.startsWith("[") && value.endsWith("]")) {
      data[key] = value
        .slice(1, -1)
        .split(",")
        .map(stripQuotes)
        .filter(Boolean);
    } else if (value.includes(",") && key === "tools") {
      data[key] = value.split(",").map(stripQuotes).filter(Boolean);
    } else if (value === "true" || value === "True") {
      data[key] = true;
    } else if (value === "false" || value === "False") {
      data[key] = false;
    } else {
      data[key] = stripQuotes(value);
    }
  }

  return { data, body };
}

function firstParagraph(body) {
  const paragraph = body
    .split(/\n\s*\n/)
    .map((block) => block.replace(/^#+\s+/gm, "").trim())
    .find((block) => block && !block.startsWith("|") && !block.startsWith("```"));
  return paragraph ? paragraph.replace(/\s+/g, " ").slice(0, 260) : "";
}

function sectionAfterHeading(body, heading) {
  const escaped = heading.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = body.match(new RegExp(`## ${escaped}\\s+([\\s\\S]*?)(?:\\n## |$)`, "i"));
  if (!match) return "";
  return match[1]
    .replace(/```[\s\S]*?```/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 360);
}

function parseRoster(filePath) {
  const text = readText(filePath);
  const lines = text.split(/\r?\n/);
  const result = {
    team: slugify(path.basename(path.dirname(filePath))),
    displayName: titleFromSlug(path.basename(path.dirname(filePath))),
    members: [],
  };
  let currentSection = null;
  let currentMember = null;

  for (const rawLine of lines) {
    const line = rawLine.replace(/\r$/, "");
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    const top = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
    if (top) {
      const [, key, value] = top;
      if (key === "team") result.team = slugify(value);
      if (key === "display_name") result.displayName = stripQuotes(value);
      if (["leaders", "seniors", "juniors"].includes(key)) currentSection = key;
      continue;
    }

    const memberStart = line.match(/^\s*-\s+name:\s*(.+)$/);
    if (memberStart && currentSection) {
      currentMember = {
        name: stripQuotes(memberStart[1]),
        rosterRole: currentSection,
      };
      result.members.push(currentMember);
      continue;
    }

    const memberField = line.match(/^\s+([A-Za-z0-9_-]+):\s*(.+)$/);
    if (memberField && currentMember) {
      currentMember[memberField[1]] = stripQuotes(memberField[2]);
    }
  }
  return result;
}

function mtgFromData(data) {
  const artUrl = data.banner || data.mtg_art_crop || data.art_crop || "";
  const imageUrl = data.mtg_image || data.mtg_card_image || "";
  if (!data.mtg_card && !artUrl && !imageUrl) return null;
  return {
    card: data.mtg_card || "",
    color: data.mtg_color || "",
    edition: data.mtg_edition || "",
    setCode: data.mtg_set_code || "",
    artUrl,
    imageUrl,
  };
}

function colorName(colors) {
  if (!Array.isArray(colors) || colors.length === 0) return "Colorless";
  return colors.map((color) => COLOR_NAMES[color] || color).join("/");
}

function imageUris(card) {
  return card.image_uris || card.card_faces?.[0]?.image_uris || {};
}

async function fetchScryfallCard(cardName) {
  const url = `https://api.scryfall.com/cards/named?fuzzy=${encodeURIComponent(cardName)}`;
  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
      "User-Agent": "gbautomation-hermes-agent/0.1",
    },
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  const card = await response.json();
  const images = imageUris(card);
  return {
    card: card.name || cardName,
    color: colorName(card.colors || card.color_identity),
    edition: card.set_name || "",
    setCode: card.set || "",
    artUrl: images.art_crop || images.normal || "",
    imageUrl: images.normal || images.large || images.png || "",
    scryfallUri: card.scryfall_uri || "",
  };
}

async function buildManualMtgAssignments() {
  const assignments = {};
  const seenCards = new Set();
  for (const [profileId, cardName] of Object.entries(PROFILE_MTG_CARD_NAMES)) {
    if (seenCards.has(cardName)) continue;
    seenCards.add(cardName);
    try {
      assignments[profileId] = await fetchScryfallCard(cardName);
    } catch (error) {
      console.warn(`Could not fetch MTG card "${cardName}" for ${profileId}: ${error.message}`);
    }
    await sleep(110);
  }
  return assignments;
}

function parseAgent(filePath, team, teamDisplayName, rosterMember) {
  const text = readText(filePath);
  const { data, body } = parseFrontmatter(text);
  const name = data.name || slugify(path.basename(filePath, ".md"));
  const displayName =
    rosterMember?.name ||
    name
      .replace(`${team}-`, "")
      .split("-")
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
  const tools = Array.isArray(data.tools)
    ? data.tools
    : String(data.tools || "")
        .split(",")
        .map(stripQuotes)
        .filter(Boolean);
  const expertise =
    sectionAfterHeading(body, "Expertise") ||
    sectionAfterHeading(body, "Role on the team") ||
    firstParagraph(body);
  return {
    id: slugify(name),
    name,
    displayName,
    team,
    teamDisplayName,
    role: data.role || rosterMember?.rosterRole || "agent",
    rosterRole: rosterMember?.rosterRole || data.role || "agent",
    model: data.model || rosterMember?.model || "",
    provider: data.cli_provider || data.provider || "",
    description: data.description || expertise,
    expertise,
    tools,
    tags: Array.isArray(data.tac_tags) ? data.tac_tags : [],
    avatarEmoji: data.avatar_emoji || "",
    sourcePath: repoRelative(filePath),
    mtg: mtgFromData(data),
    artSeed: slugify(`${team}-${displayName}`),
  };
}

function parseTeamCatalog() {
  const catalogPath = path.join(teamsRoot, "_catalog.md");
  const text = readText(catalogPath);
  const teams = new Map();
  for (const line of text.split(/\r?\n/)) {
    const cells = line
      .trim()
      .split("|")
      .map((cell) => cell.trim())
      .filter(Boolean);
    if (cells.length < 4 || cells[0] === "Team" || cells[0].startsWith("---")) continue;
    const displayName = cells[0].replace(/\*\*/g, "");
    const teamPath = cells[3].replace(/`/g, "");
    if (!teamPath.startsWith("ai-library/teams/")) continue;
    const id = slugify(teamPath.split("/").filter(Boolean).at(-1));
    teams.set(id, {
      id,
      displayName,
      catalogAgentCount: Number(cells[1].replace(/\D/g, "")) || 0,
      leader: cells[2].replace(/\*\*/g, ""),
      sourcePath: teamPath,
    });
  }
  return teams;
}

function buildAiLibrary() {
  const catalog = parseTeamCatalog();
  const teams = [];
  const agents = [];

  for (const entry of fs.readdirSync(teamsRoot, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const teamDir = path.join(teamsRoot, entry.name);
    const rosterPath = path.join(teamDir, "_roster.yaml");
    if (!fs.existsSync(rosterPath)) continue;
    const roster = parseRoster(rosterPath);
    const teamMeta = catalog.get(roster.team) || {
      id: roster.team,
      displayName: roster.displayName,
      leader: roster.members[0]?.name || "",
      sourcePath: repoRelative(teamDir),
    };
    const teamAgents = [];
    for (const member of roster.members) {
      if (!member.agent_file) continue;
      const agentPath = path.join(teamDir, member.agent_file);
      if (!fs.existsSync(agentPath)) continue;
      const agent = parseAgent(agentPath, roster.team, roster.displayName, member);
      agents.push(agent);
      teamAgents.push(agent.id);
    }
    teams.push({
      id: roster.team,
      displayName: roster.displayName || teamMeta.displayName,
      leader: teamMeta.leader || roster.members[0]?.name || "",
      sourcePath: repoRelative(rosterPath),
      agentCount: teamAgents.length,
      catalogAgentCount: teamMeta.catalogAgentCount || teamAgents.length,
      roleCounts: {
        leaders: roster.members.filter((member) => member.rosterRole === "leaders").length,
        seniors: roster.members.filter((member) => member.rosterRole === "seniors").length,
        juniors: roster.members.filter((member) => member.rosterRole === "juniors").length,
      },
      agents: teamAgents,
      kind: "ai-library",
      description: `${roster.displayName} agent team from the canonical AI Library index.`,
      artSeed: slugify(roster.displayName || roster.team),
    });
  }

  return { teams, agents };
}

function parseTopLevelYaml(filePath) {
  const text = readText(filePath);
  const lines = text.split(/\r?\n/);
  const data = {};
  let activeArray = null;

  for (const rawLine of lines) {
    const line = rawLine.replace(/\r$/, "");
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    const top = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
    if (top) {
      const [, key, rawValue] = top;
      const value = rawValue.trim();
      if (!value) {
        activeArray = key;
        data[key] ||= [];
      } else {
        activeArray = null;
        data[key] = stripQuotes(value);
      }
      continue;
    }

    const listItem = line.match(/^\s*-\s+(.+)$/);
    if (listItem && activeArray) {
      data[activeArray].push(stripQuotes(listItem[1]));
    }
  }

  return { data, text };
}

function parseOperatingModes(text) {
  const modes = [];
  const lines = text.split(/\r?\n/);
  let inModes = false;
  let current = null;
  for (const rawLine of lines) {
    const line = rawLine.replace(/\r$/, "");
    if (/^operating_modes:\s*$/.test(line)) {
      inModes = true;
      continue;
    }
    if (inModes && /^[A-Za-z0-9_-]+:/.test(line)) break;
    if (!inModes) continue;

    const start = line.match(/^\s*-\s+id:\s*(.+)$/);
    if (start) {
      current = { id: stripQuotes(start[1]), label: "", purpose: "" };
      modes.push(current);
      continue;
    }
    if (!current) continue;
    const field = line.match(/^\s+([A-Za-z0-9_-]+):\s*(.+)$/);
    if (field && ["label", "purpose"].includes(field[1])) {
      current[field[1]] = stripQuotes(field[2]);
    }
  }
  return modes;
}

function parseHermesProfile(filePath, team = "", manualMtgAssignments = {}) {
  const { data, text } = parseTopLevelYaml(filePath);
  const id = slugify(data.id || path.basename(filePath, ".yaml"));
  return {
    id,
    displayName: data.display_name || titleFromSlug(path.basename(filePath, ".yaml")),
    team,
    role: data.role || "",
    status: data.status || "",
    runtime: data.runtime || "hermes",
    model: data.model || "",
    provider: data.provider || "",
    primaryTools: Array.isArray(data.primary_tools) ? data.primary_tools : [],
    canonicalSources: Array.isArray(data.canonical_sources) ? data.canonical_sources : [],
    operatingModes: parseOperatingModes(text),
    sourcePath: repoRelative(filePath),
    mtg: mtgFromData(data) || manualMtgAssignments[id] || null,
    artSeed: slugify(data.display_name || data.id || path.basename(filePath, ".yaml")),
  };
}

function buildProfiles(manualMtgAssignments) {
  const profiles = [];
  if (fs.existsSync(tacProfilesRoot)) {
    for (const fileName of fs.readdirSync(tacProfilesRoot)) {
      if (!fileName.endsWith(".yaml")) continue;
      profiles.push(parseHermesProfile(path.join(tacProfilesRoot, fileName), "tac-hermes", manualMtgAssignments));
    }
  }

  const templatesRoot = path.join(profileTemplatesRoot, "profiles");
  if (fs.existsSync(templatesRoot)) {
    for (const fileName of fs.readdirSync(templatesRoot)) {
      if (!fileName.endsWith(".yaml")) continue;
      const profile = parseHermesProfile(path.join(templatesRoot, fileName), "profile-template", manualMtgAssignments);
      if (!profiles.some((existing) => existing.id === profile.id)) profiles.push(profile);
    }
  }
  return profiles;
}

function buildProfileTeams(profiles) {
  const tacTeamPath = path.join(profileTemplatesRoot, "profile-teams", "tac-hermes.yaml");
  const profileTeams = [];
  if (fs.existsSync(tacTeamPath)) {
    const data = parseTopLevelYaml(tacTeamPath).data;
    profileTeams.push({
      id: "tac-hermes",
      displayName: data.display_name || "TAC Hermes Build Council",
      runtime: data.runtime || "hermes",
      purpose:
        "Route coding, PRD, validation, and agent-team work through TAC-primed Hermes profiles.",
      sourcePath: repoRelative(tacTeamPath),
      profiles: profiles
        .filter((profile) => profile.team === "tac-hermes")
        .map((profile) => profile.id),
      artSeed: "tac-hermes",
    });
  }
  return profileTeams;
}

const manualMtgAssignments = await buildManualMtgAssignments();
const { teams, agents } = buildAiLibrary();
const profiles = buildProfiles(manualMtgAssignments);
const profileTeams = buildProfileTeams(profiles);
const tacLead = profiles.find((profile) => profile.id === "tac-lead");
const tacLeadVariants = [
  {
    id: "tac-lead-north-star",
    title: "North Star Advisor",
    modeId: "agentic_engineering_advisor",
    summary: "Staff-level TAC strategy, zero-touch engineering guidance, and architecture tradeoffs.",
    emphasis: "Use when the question is still strategic and the route is not clear yet.",
  },
  {
    id: "tac-lead-component-retrieval",
    title: "Component Retrieval",
    modeId: "tac_component_retrieval",
    summary: "Find real TAC components, skills, prompts, hooks, agents, and repo patterns before design.",
    emphasis: "Use when a recommendation needs evidence from actual reusable assets.",
  },
  {
    id: "tac-lead-dispatch",
    title: "Team Dispatch",
    modeId: "tac_team_dispatch",
    summary: "Convert approved plans into Hermes Kanban work for tac-director and the TAC build team.",
    emphasis: "Use after the PRD/spec is clear and operator approval exists.",
  },
].map((variant) => ({
  ...variant,
  profileId: "tac-lead",
  sourcePath: tacLead?.sourcePath || "",
}));

const payload = {
  generatedAt: new Date().toISOString(),
  summary: {
    teams: teams.length,
    agents: agents.length,
    hermesProfiles: profiles.length,
    profileTeams: profileTeams.length,
  },
  teams,
  agents,
  profiles,
  profileTeams,
  tacLeadVariants,
};

function jsStringify(value) {
  return JSON.stringify(value, null, 2).replace(/[\u007f-\uffff]/g, (char) => {
    return `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`;
  });
}

const output = `/* Generated by scripts/build_gbauto_library_index.mjs. Do not edit by hand. */
export const gbautoLibrary = ${jsStringify(payload)} as const;

export type GbautoLibrary = typeof gbautoLibrary;
export type GbautoLibraryAgent = GbautoLibrary["agents"][number];
export type GbautoLibraryTeam = GbautoLibrary["teams"][number];
export type GbautoHermesProfile = GbautoLibrary["profiles"][number];
export type GbautoProfileTeam = GbautoLibrary["profileTeams"][number];
export type GbautoTacLeadVariant = GbautoLibrary["tacLeadVariants"][number];
`;

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, output, "utf8");
console.log(
  `Wrote ${path.relative(process.cwd(), outputPath)} (${teams.length} teams, ${agents.length} agents, ${profiles.length} profiles)`,
);
