import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const fixturePath = path.join(root, "public", "gbauto-supabase", "smoke-client-pi-observability.json");
const appPath = path.join(root, "src", "App.tsx");
const pagePath = path.join(root, "src", "pages", "SmokeClientObservabilityPage.tsx");
const staticDir = path.join(root, "public", "hermes-observability");
const staticIndexPath = path.join(staticDir, "index.html");
const staticAppPath = path.join(staticDir, "app.js");
const staticSwimlanePath = path.join(staticDir, "swimlane.js");
const staticRacePath = path.join(staticDir, "race.js");

function fail(message) {
  console.error(`FAIL: ${message}`);
  process.exitCode = 1;
}

const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
if (fixture.tenant !== "smoke-client") fail("fixture tenant must be smoke-client");
if (!Array.isArray(fixture.sessions) || fixture.sessions.length < 1) fail("fixture must contain at least one session");
if (!Array.isArray(fixture.events) || fixture.events.length < 5) fail("fixture must contain at least five events");

const sessionIds = new Set(fixture.sessions.map((session) => session.session_id));
for (const event of fixture.events) {
  if (event.pool !== "smoke-client") fail(`event ${event.event_id} has non-smoke pool ${event.pool}`);
  if (!event.tags?.includes("tenant:smoke-client")) fail(`event ${event.event_id} lacks tenant tag`);
  if (!event.tags?.includes("runtime:hermes")) fail(`event ${event.event_id} lacks runtime tag`);
  if (!sessionIds.has(event.session_id)) fail(`event ${event.event_id} references unknown session`);
}

const types = new Set(fixture.events.map((event) => event.type));
for (const required of ["session_start", "agent_start", "tool_call", "tool_result", "assistant_message"]) {
  if (!types.has(required)) fail(`fixture lacks ${required}`);
}

const text = [
  fs.readFileSync(appPath, "utf8"),
  fs.readFileSync(pagePath, "utf8"),
  fs.readFileSync(staticIndexPath, "utf8"),
  fs.readFileSync(staticAppPath, "utf8"),
].join("\n");
if (!text.includes('"/hermes-observability"')) fail("App route/nav must include /hermes-observability");
if (!text.includes("Hermes Observability")) fail("page must be renamed to Hermes Observability");
if (!text.includes("/api/gbauto/pi-observability")) fail("static app must fetch through the Hermes observability bridge");
if (!text.includes("view: \"swimlane\"")) fail("static app must default to swimlane view");
if (!text.includes("pool: \"smoke-client\"")) fail("static app must default to smoke-client pool");
for (const asset of [staticSwimlanePath, staticRacePath]) {
  if (!fs.existsSync(asset)) fail(`missing original observability asset ${path.basename(asset)}`);
}
if (text.includes("192.168.4.101") || text.includes("pi_obs_token=dev")) {
  fail("page source must not hard-code local Mini URL or dev token");
}

if (!process.exitCode) {
  console.log("OK: smoke-client Pi observability fixture and route contract are valid");
}
