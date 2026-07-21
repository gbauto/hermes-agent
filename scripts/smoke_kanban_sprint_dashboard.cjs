#!/usr/bin/env node
"use strict";

/**
 * Real-browser smoke for the Kanban Sprint Manager plugin.
 *
 * Environment:
 *   KANBAN_SMOKE_URL         Dashboard URL (default http://127.0.0.1:9150/kanban)
 *   KANBAN_SMOKE_BOARD       Board slug selected before load (default gbautomation)
 *   KANBAN_SMOKE_SCREENSHOT  Optional output PNG path
 *   PLAYWRIGHT_MODULE        Optional absolute module path when Playwright is not local
 */

const fs = require("node:fs");
const playwright = require(process.env.PLAYWRIGHT_MODULE || "playwright");

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

async function main() {
  const url = process.env.KANBAN_SMOKE_URL || "http://127.0.0.1:9150/kanban";
  const board = process.env.KANBAN_SMOKE_BOARD || "gbautomation";
  const screenshot = process.env.KANBAN_SMOKE_SCREENSHOT || "";
  const browser = await playwright.chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await page.addInitScript((selectedBoard) => {
    window.localStorage.setItem("hermes.kanban.selectedBoard", selectedBoard);
    window.localStorage.setItem("hermes.kanban.commandView", "sprint");
  }, board);

  try {
    const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
    invariant(response && response.ok(), `dashboard navigation failed: ${response && response.status()}`);
    await page.locator(".hermes-kanban-command-nav").waitFor({ timeout: 30_000 });

    invariant(await page.locator(".hermes-kanban-command-tab").count() === 3,
      "expected Sprint, Workstreams, and Board tabs");
    await page.getByText("Execution pulse, decisions, and proof", { exact: true }).waitFor();
    invariant(await page.locator(".hermes-kanban-sprint-metric").count() === 6,
      "expected six sprint scorecard metrics");
    invariant(await page.locator(".hermes-kanban-rock").count() > 0,
      "expected at least one live rock");
    invariant(await page.locator(".hermes-kanban-ids-row").count() > 0,
      "expected prioritized IDS blockers");

    const firstRock = page.locator(".hermes-kanban-rock-title").first();
    await firstRock.click();
    await page.locator(".hermes-kanban-drawer").waitFor();
    invariant(/#task=t_[a-z0-9]+/i.test(page.url()), "task drawer did not write a deep link");
    await page.locator(".hermes-kanban-drawer-close").first().click();

    await page.getByRole("tab", { name: /Workstreams/ }).click();
    await page.getByText("Linked workstreams", { exact: true }).waitFor();
    const firstStream = page.locator(".hermes-kanban-workstream-summary").first();
    invariant(await firstStream.count() === 1, "expected at least one linked workstream");
    await firstStream.click();
    await page.locator(".hermes-kanban-workstream-detail").waitFor();

    await page.getByRole("tab", { name: /Board/ }).click();
    await page.locator(".hermes-kanban-columns").waitFor();
    invariant(await page.locator(".hermes-kanban-column").count() >= 6,
      "native board columns did not render");

    await page.getByRole("tab", { name: /Sprint/ }).click();
    if (screenshot) {
      await page.screenshot({ path: screenshot, fullPage: true });
      invariant(fs.statSync(screenshot).size > 0, "screenshot is empty");
    }
    invariant(pageErrors.length === 0, `browser page errors: ${pageErrors.join(" | ")}`);

    process.stdout.write(JSON.stringify({
      ok: true,
      tabs: 3,
      metrics: 6,
      rocks: await page.locator(".hermes-kanban-rock").count(),
      blockers: await page.locator(".hermes-kanban-ids-row").count(),
      screenshot: screenshot || null,
    }) + "\n");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
