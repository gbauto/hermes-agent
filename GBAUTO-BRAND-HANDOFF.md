# GBAutomation Brand — Hermes Dashboard Handoff

**For:** the Mac Mini Hermes agent
**From:** Claude Code (Windows desktop — `~/Desktop/hermes-agent`)
**Date:** 2026-05-21
**Branch:** `feat/gbautomation-brand-theme` on `gbauto/hermes-agent`
**Base:** `NousResearch/hermes-agent@a0c031299` (upstream `main`)

---

## Goal

Apply the GBAutomation brand to the Hermes dashboard so the Mac Mini's
dashboard matches the public site (gbautomation.xyz):

1. A new **light cream theme** ("GBAutomation") — cream `#f3f1e7` canvas,
   near-black `#191919` text, terracotta `#d97757` accent, Inter body +
   Newsreader serif headings. Mirrors `gb-automation-landing/src/index.css`.
2. The **GB logo** in the sidebar header, right of the "Hermes Agent"
   wordmark — a black alpha-masked monogram with a slow shine sweep
   (once a minute), linking to `https://www.gbautomation.xyz`.
3. "GBAutomation" pinned to the **top of the theme dropdown**.

This was built and approved on the Windows dev instance. It is **not yet
live on the Mac Mini** — that is your job.

---

## What changed (6 files)

| File | Change |
|---|---|
| `web/src/themes/presets.ts` | New `gbautomationTheme` preset; added to `BUILTIN_THEMES` **first**. |
| `web/src/themes/context.tsx` | Default theme fallback `"default"` → `"gbautomation"` (first-paint default). |
| `web/src/index.css` | `.gb-logo` mask-fill logo + `gb-logo-shine` keyframes (60s cadence, `prefers-reduced-motion` aware). |
| `web/src/App.tsx` | `<a class="gb-logo">` added to the sidebar header, links to gbautomation.xyz (`target="_blank"`). |
| `hermes_cli/web_server.py` | `gbautomation` added **first** in `_BUILTIN_DASHBOARD_THEMES` (drives dropdown order). |
| `web/public/gb-mark.png` | **New** — 25 KB GB monogram, used as the logo's alpha mask. |

The theme system is additive — no existing theme was modified. Rollback is
just switching the theme back in the UI.

---

## Deploy steps

### 0. Determine how hermes-agent is installed on the Mac Mini

Before anything, find out whether the Mac Mini runs hermes-agent from a
**git clone** (editable install) or a **packaged install** (pip/uv from a
release). This decides everything below.

```bash
which hermes && hermes --version
python -c "import hermes_cli, pathlib; print(pathlib.Path(hermes_cli.__file__).parent)"
```

- If `hermes_cli` resolves inside a **git working tree** → editable clone,
  use the branch directly (step 1a).
- If it resolves into `site-packages` → packaged install; you must either
  reinstall from the branch, or patch `web_dist` in place (step 1b).

### 1a. Editable clone — pull the branch

```bash
cd <hermes-agent repo on the Mac Mini>
git remote add gbauto https://github.com/gbauto/hermes-agent.git   # if absent
git fetch gbauto
git checkout feat/gbautomation-brand-theme
```

If the Mac Mini's clone is on a different upstream commit than
`a0c031299`, prefer cherry-picking the 6-file change or merging — the
edits are isolated and conflicts are unlikely outside `web_server.py`.

### 1b. Packaged install — reinstall from the branch

```bash
uv tool install "git+https://github.com/gbauto/hermes-agent.git@feat/gbautomation-brand-theme"
# or: pip install --force-reinstall "git+https://github.com/gbauto/hermes-agent.git@feat/gbautomation-brand-theme"
```

A packaged install ships a pre-built `web_dist`; confirm the rebuild
(step 2) actually ran, or the brand will not appear.

### 2. Rebuild the web SPA

The dashboard serves a pre-built bundle from `hermes_cli/web_dist/`. The
brand changes are in `web/src` — they only take effect after a rebuild.

```bash
cd web
npm install          # first time only
npm run build        # tsc -b && vite build → outputs to ../hermes_cli/web_dist
```

`npm run build` copies `web/public/` (including `gb-mark.png`) into the
bundle. **Confirm `gb-mark.png` exists in `web/public/` before building** —
without it the logo renders as an empty box.

### 3. Activate the theme

The backend reads the active theme from `~/.hermes/config.yaml`
(`dashboard.theme`). Set it:

```bash
# Option A — edit config directly:
#   dashboard:
#     theme: gbautomation
#
# Option B — via the API once the dashboard is up:
TOKEN=$(curl -s http://127.0.0.1:9119/ | grep -oE '__HERMES_SESSION_TOKEN__="[^"]+"' | cut -d'"' -f2)
curl -X PUT http://127.0.0.1:9119/api/dashboard/theme \
  -H "X-Hermes-Session-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"gbautomation"}'
```

### 4. Restart the dashboard

Restart however the Mac Mini manages the `hermes dashboard` process
(launchd job, wrapper script, or a plain relaunch). A Python process does
not hot-reload `web_server.py` — the restart is required for the dropdown
ordering change.

### 5. Verify

- Dashboard canvas is **cream**, text near-black, headings serif.
- **GB logo** sits right of "Hermes Agent" in the sidebar header; it
  sweeps a shine ~once a minute; clicking it opens gbautomation.xyz.
- Theme dropdown (bottom-left) lists **"GBAutomation" first**.
- `curl -s http://127.0.0.1:9119/api/dashboard/themes` → `active` is
  `gbautomation` and `themes[0].name` is `gbautomation`.

---

## Caveats

- **Light theme on a dark-designed app.** Every shadcn token is derived
  from the palette via `color-mix()`, so the inversion mostly works, but
  spot-check dialogs/popovers/charts for any low-contrast areas. The look
  was approved on the Windows dev instance.
- **Backdrop noise.** The Backdrop's `color-dodge` grain blows out on a
  light canvas — `noiseOpacity` is set to `0.1` to compensate. If the
  canvas looks hazy, drop it to `0`.
- **Logo legibility.** The logo chip-fill is fixed black `#191919`; it is
  designed for the cream theme. Under a dark Hermes theme the monogram
  goes low-contrast — expected, since the logo pairs with the brand theme.
- The frontend default (`context.tsx`) is now `gbautomation`; this is a
  GBAutomation fork, so that is intentional.

## Rollback

Switch the theme back to "Hermes Teal" in the dashboard theme switcher
(persists to `~/.hermes/config.yaml`), or check out `main`. The logo is
unconditional in the header; to remove it, revert the `App.tsx` hunk.

---

## Source of truth

- Brand tokens: `gb-automation-landing/src/index.css` (`:root` block).
- Logo + shine technique: `gbautomation` repo →
  `resources/skills/consulting-admin/assets/gb-logo-shine-demo.html`
  (commits `f8a29df5`, `29b539e9`).
