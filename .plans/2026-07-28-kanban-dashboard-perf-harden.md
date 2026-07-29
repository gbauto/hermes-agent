---
title: Kanban dashboard performance hardening
date: 2026-07-28
description: Defers hidden Chat work, splits dashboard routes, compresses responses, and caches versioned assets so the live All Boards view opens quickly.
status: complete
tags: [performance, dashboard, kanban, hermes]
related:
  - "[[web-perf-harden]]"
---

# Kanban dashboard performance hardening

## Summary

The live All Boards route became usable in 0.68 seconds, down from 3.36
seconds in the same visible Chrome session on the Mac Mini. The production
entry bundle fell from 3.17 MB to 603 KB, hidden Chat/session work no longer
competes with Kanban startup, and text assets plus large JSON responses are
gzip-compressed.

Deployed commit: `15d08b4924f171e873ec0e8a30880ed60017b955`

## Problem

Opening `http://127.0.0.1:9150/kanban?board=__all__` looked stalled even though
direct API probes were normally sub-300 ms. A cold browser trace showed that
the browser transferred 6.04 MB and waited 3.36 seconds for the All Boards
controls. The hidden Chat surface simultaneously fetched nearly 1 MB of
session data and caused the board request to finish in 2.83 seconds.

## Root cause

- `web/src/App.tsx:99` previously imported Chat and every built-in route into
  one 3.17 MB JavaScript entry.
- The persistent Chat host mounted after plugin discovery on every route,
  including Kanban. Chat's terminal setup and session sidebar then opened
  background connections and fetched `/api/sessions` while invisible.
- `hermes_cli/web_server.py:264` had no response compression. The 521 KB
  compact All Boards response and 3.17 MB JavaScript entry crossed the wire
  uncompressed.
- Hashed application assets and plugin bundles lacked durable browser caching.
  Plugin assets were explicitly marked `no-store`.

## Changes

### Frontend

- Converted built-in pages to route-level `React.lazy` chunks.
- Deferred the 524 KB Chat chunk and all Chat side effects until the user
  opens Chat for the first time.
- Preserved Chat persistence after first use. Returning to Kanban hides the
  existing terminal instead of destroying it.
- Versioned plugin JS and CSS URLs from each plugin manifest at
  `web/src/plugins/usePlugins.ts:44`.

### Backend

- Added `GZipMiddleware` for responses over 1 KB at
  `hermes_cli/web_server.py:264`.
- Added one-year immutable caching for Vite hashed assets, versioned plugin
  assets, and hashed CSS.
- Added a one-hour revalidation cache for unversioned public assets such as
  the GB mark.
- Added regression coverage for compressed, immutable plugin bundles.

## Verification

- Focused backend tests: 6 passed.
- Web tests: 31 passed.
- Web TypeScript typecheck: passed.
- Production Vite build: passed.
- Existing full-web lint remains red on eight unrelated pre-existing rules;
  this change introduced no new reported lint finding.
- Live lazy-Chat smoke: Chat loaded its JS/CSS, model info, and session list
  only after clicking Chat; xterm connected; returning to Kanban preserved the
  terminal as inactive and rendered Sprint, Workstreams, Board, and Squash
  Board controls.
- Live headers confirmed `Content-Encoding: gzip` and immutable cache headers
  for hashed JS, CSS, and plugin bundles.

## Lighthouse before and after

Both runs used Lighthouse's throttled performance profile on the same Mac
Mini. The baseline was served from the previous deployed commit on isolated
port 9151 against the same live Hermes home; the optimized build ran on
production port 9150.

| Metric | Before | After | Change |
|---|---:|---:|---:|
| Performance score | 27 | 60 | +33 points |
| First Contentful Paint | 19.7 s | 6.3 s | 68% faster |
| Largest Contentful Paint | 24.9 s | 8.7 s | 65% faster |
| Total Blocking Time | 2,404 ms | 0 ms | eliminated |
| Speed Index | 19.7 s | 6.3 s | 68% faster |
| Cumulative Layout Shift | 0 | 0 | unchanged |
| Total byte weight | 4.81 MB | 1.39 MB | 71% lower |

## Real-browser cold-load trace

| Metric | Before | After | Change |
|---|---:|---:|---:|
| All Boards usable | 3,360 ms | 682 ms | 80% faster |
| All Boards API | 2,831 ms | 338 ms | 88% faster |
| Resource transfer | 6.04 MB | 1.31 MB | 78% lower |
| Main JS entry, decoded | 3.17 MB | 603 KB | 81% lower |
| Hidden session-list request | 984 KB | 0 | removed |

## Expected impact

Kanban now paints its shell at roughly the same time as before but reaches
live board controls and cards much sooner. Repeat visits are cheaper because
hashed and versioned assets remain cached. Live polling, filters, sorting,
workstreams, Sprint, and full task-on-click behavior are unchanged.

## Follow-ups

- Optimize the 303 KB `gb-mark.png` source. It is cached now but still costs
  about 298 KB on a true cold load.
- Deduplicate the small initial `/api/status` and `/api/config` calls.
- Consider splitting the shared design-system badge chunk if another cold
  trace shows its 411 KB decoded cost is material.
- Keep the compact board response and lazy task-detail contract as board
  volume grows.
