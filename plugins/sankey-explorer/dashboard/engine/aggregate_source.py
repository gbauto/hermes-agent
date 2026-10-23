#!/usr/bin/env python3
"""aggregate_source.py — the SHARED, PII-SAFE aggregate core (ADR-2 P0).

This is the single seam reused by BOTH surfaces described in
``docs/decisions/2026-06-29-dashboard-data-on-amplify.md``:

  * the internal 9119 ``sankey-explorer`` dashboard plugin (live on Mini), and
  * the Mini-side build job that emits static per-table aggregate JSON for the
    public Amplify page.

Given ``(table, dims, weight?, where?)`` it produces **AGGREGATE-ONLY** records:

    GROUP BY dims  ->  {**dims, "n": count, "<weight>": sum(weight)}

It NEVER selects or emits raw rows:

  * LIVE path — the GROUP BY is pushed down into the SQL sent through the
    ``gbauto-supabase`` CLI (read-only ``query``), so individual rows never leave
    Supabase. PII-safe by construction.
  * OFFLINE path — a committed FIXTURE of sample rows is aggregated in-process
    and only the aggregate rows are ever returned. (Fixtures are non-PII samples
    of allowed tables, committed so the pipeline is provable on the DNS-blocked
    PC.)

A PII-SAFE TABLE CATALOG (``table_catalog`` / ``allowed_tables``) enumerates the
tables that may be charted, their candidate categorical dims, and numeric weight
columns. An explicit EXCLUDE-LIST removes ``client-core`` and any PII-bearing
table/column so the surface stays PII-safe by construction.

Hard rules honoured (see MEMORY):
  * EVERY Supabase read goes through the gbauto-supabase CLI ONLY (read-only).
  * No secrets are ever printed.
  * Identifiers are validated as plain SQL identifiers before reaching the query.
  * Aggregate-only output; never raw rows; PII tables/columns excluded.

Self-check:
    python aggregate_source.py            # runs the built-in unit test
    python aggregate_source.py --catalog  # print the PII-safe catalog as JSON
    python aggregate_source.py --table host_job_runs \
        --dims job_name,host,status --fixture ../fixtures/host_job_runs.sample.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Reuse the validated CLI read path + identifier guards from from_supabase.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from from_supabase import (  # noqa: E402
    _ident,
    _table,
    extract_rows,
    fetch_live,
    load_fixture,
    supabase_bin,  # noqa: F401  (re-exported for callers/tests)
)

# --------------------------------------------------------------------------- #
# PII-SAFE TABLE CATALOG
# --------------------------------------------------------------------------- #
# Seeded from references/data-sources.md. Each entry is a table that MAY be
# charted, with the categorical dims allowed as Sankey columns and the numeric
# weight columns allowed for the Measure toggle. Anything not listed here cannot
# be aggregated through this module.

# Explicit EXCLUDE-LIST: tables that must NEVER be exposed (PII / raw content).
# `client-core` is never synced or charted; raw message/transcript bodies and
# any per-person tables are PII hazards. Substring match (case-insensitive) so a
# whole family (e.g. anything namespaced *client_core*) is caught.
EXCLUDE_TABLE_PATTERNS: tuple[str, ...] = (
    "client-core",
    "client_core",
    "clientcore",
    "_pii",
    "pii_",
    "messages",        # raw message bodies
    "message_bodies",
    "transcripts",     # raw transcript text
    "chat_messages",
    "smoke_chat",      # chat content (anon read elsewhere, not for aggregation here)
    "contacts",
    "leads",
    "people",
    "users",
    "recipients",
    "emails",
)

# Columns that must NEVER be used as a dim or weight even on an allowed table.
# Matched on underscore-separated TOKENS (so "job_name" is fine but "first_name"
# / a bare "name" column is not), plus a few forbidden compound full-names.
# Free-text / raw-content / per-person tokens only — categorical job/status
# columns are unaffected.
EXCLUDE_COLUMN_TOKENS: frozenset[str] = frozenset({
    "email", "phone", "address", "ssn", "dob", "password", "secret",
    "body", "content", "text", "message", "transcript", "prompt",
    "completion", "snippet", "payload", "raw", "subject", "recipient",
    "recipients", "apikey",
})
EXCLUDE_COLUMN_FULLNAMES: frozenset[str] = frozenset({
    "name", "first_name", "last_name", "full_name", "display_name",
    "contact_name", "client_name", "person_name", "user_name", "username",
    "sender", "api_key", "access_token", "auth_token",
})

# table -> {dims, weights, distinct_on, order_by, unit, title}
_CATALOG: dict[str, dict] = {
    "prd_artifacts": {
        "dims": ["client", "status", "kind", "prd_type", "priority",
                 "tac_status", "agent_team"],
        "weights": [],
        "unit": "PRDs",
        "title": "PRD Artifacts",
    },
    "host_job_runs": {
        "dims": ["job_name", "host", "status", "scheduler"],
        "weights": ["duration_ms"],
        "unit": "runs",
        "title": "Host Job Runs",
    },
    "agent_runs": {
        "dims": ["agent", "status", "team", "exit_reason"],
        "weights": [],
        # Kanban mirror inserts one row per poll -> de-dupe to latest per task.
        "distinct_on": "task_id",
        "order_by": "created_at desc",
        "unit": "runs",
        "title": "Agent Runs",
    },
    "app_error_events": {
        "dims": ["source", "severity", "service"],
        "weights": [],
        "unit": "errors",
        "title": "App Error Events",
    },
    "tag_observations": {
        "dims": ["namespace", "tag", "source_repo"],
        "weights": [],
        "unit": "observations",
        "title": "Tag Observations",
    },
    "agent_feedback_events": {
        # channel/sentiment/slug are aggregate-safe (no free text emitted).
        "dims": ["channel", "sentiment", "plan_slug"],
        "weights": [],
        "unit": "feedback",
        "title": "Agent Feedback Events",
    },
    # Per-domain schemas (shop-intelligence vs mall-scanner — do NOT conflate).
    "ecom_observations": {
        "dims": ["brand", "lane", "gate", "production_green"],
        "weights": [],
        "unit": "observations",
        "title": "Ecom Observations",
    },
    "mall_observations": {
        "dims": ["store", "category", "status"],
        "weights": [],
        "unit": "observations",
        "title": "Mall Observations",
    },
}


def _is_excluded_table(table: str) -> bool:
    low = table.lower()
    return any(pat in low for pat in EXCLUDE_TABLE_PATTERNS)


def _is_excluded_column(col: str) -> bool:
    low = col.lower()
    if low in EXCLUDE_COLUMN_FULLNAMES:
        return True
    tokens = set(low.replace("-", "_").split("_"))
    return bool(tokens & EXCLUDE_COLUMN_TOKENS)


def table_catalog() -> dict[str, dict]:
    """Return the PII-safe catalog: {table: {dims, weights, ...}}.

    Tables matching the EXCLUDE-LIST are removed, and any dim/weight that matches
    an excluded column pattern is stripped, so the catalog is PII-safe by
    construction even if a future edit adds a hazardous column.
    """
    safe: dict[str, dict] = {}
    for table, meta in _CATALOG.items():
        if _is_excluded_table(table):
            continue
        dims = [d for d in meta.get("dims", []) if not _is_excluded_column(d)]
        weights = [w for w in meta.get("weights", []) if not _is_excluded_column(w)]
        entry = dict(meta)
        entry["dims"] = dims
        entry["weights"] = weights
        safe[table] = entry
    return safe


def allowed_tables() -> list[str]:
    return sorted(table_catalog().keys())


def excluded_tables() -> list[str]:
    """Catalog entries that are present in source but filtered out (audit aid)."""
    return sorted(t for t in _CATALOG if _is_excluded_table(t))


# --------------------------------------------------------------------------- #
# AGGREGATION
# --------------------------------------------------------------------------- #
def _norm(v) -> str:
    return "(none)" if v is None or v == "" else str(v)


def _to_num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def build_aggregate_sql(table: str, dims: list[str], *, weight: str | None,
                        where: str | None, distinct_on: str | None,
                        order_by: str | None) -> str:
    """Construct a read-only GROUP BY SELECT — aggregate-only, never raw rows.

    Without distinct_on:
        select d1, d2, count(*) as n[, sum(w) as <w>] from t [where ..]
        group by d1, d2
    With distinct_on (e.g. agent_runs.task_id) we first collapse to the latest
    row per key in a subquery, THEN aggregate the deduped set.
    """
    dim_cols = ", ".join(dims)
    measures = "count(*) as n"
    if weight:
        measures += f", coalesce(sum({weight}), 0) as {weight}"

    if distinct_on:
        inner_cols = ", ".join(dict.fromkeys([*dims, *( [weight] if weight else [] ), distinct_on]))
        ob_terms = [distinct_on]
        ob = (order_by or "").strip()
        if ob and not ob.lower().startswith(distinct_on.lower()):
            ob_terms.append(ob)
        inner = (f"select distinct on ({distinct_on}) {inner_cols} from {table}"
                 + (f" where {where}" if where else "")
                 + " order by " + ", ".join(ob_terms))
        return (f"select {dim_cols}, {measures} from ({inner}) as deduped "
                f"group by {dim_cols}")

    sql = f"select {dim_cols}, {measures} from {table}"
    if where:
        sql += f" where {where}"
    sql += f" group by {dim_cols}"
    return sql


def aggregate_rows(rows: list[dict], dims: list[str], weight: str | None) -> list[dict]:
    """Aggregate a list of raw rows in-process -> aggregate-only records.

    Used for the OFFLINE fixture path. Only the grouped {dims, n, weight} tuples
    are returned — the individual input rows are never echoed back.
    """
    counts: dict[tuple, int] = defaultdict(int)
    sums: dict[tuple, float] = defaultdict(float)
    for r in rows:
        key = tuple(_norm(r.get(d)) for d in dims)
        counts[key] += 1
        if weight:
            sums[key] += _to_num(r.get(weight))
    out: list[dict] = []
    for key, n in counts.items():
        rec = {d: key[i] for i, d in enumerate(dims)}
        rec["n"] = n
        if weight:
            rec[weight] = sums[key]
        out.append(rec)
    out.sort(key=lambda d: d["n"], reverse=True)
    return out


def aggregate(table: str, dims: list[str], *, weight: str | None = None,
              where: str | None = None, fixture: Path | str | None = None,
              project: str = "gbauto") -> dict:
    """Produce AGGREGATE-ONLY records for (table, dims, weight[, where]).

    Returns ``{"table", "dims", "weight", "unit", "count", "records"}`` where
    every record is ``{**dims, "n": <count>[, "<weight>": <sum>]}``.

    Validates the request against the PII-safe catalog and identifier rules.
    On the live path the GROUP BY runs in SQL (raw rows never leave Supabase);
    offline it aggregates the committed fixture in-process.
    """
    table = _table(table)
    if _is_excluded_table(table):
        sys.exit(f"error: table '{table}' is on the PII exclude-list and cannot be charted")
    catalog = table_catalog()
    if table not in catalog:
        sys.exit(f"error: table '{table}' is not in the PII-safe catalog "
                 f"(allowed: {', '.join(catalog)})")

    meta = catalog[table]
    if not dims or len(dims) < 1:
        sys.exit("error: need at least 1 dim to aggregate")
    dim_keys: list[str] = []
    for d in dims:
        d = _ident(d, "dim")
        if _is_excluded_column(d):
            sys.exit(f"error: dim '{d}' matches a PII column pattern and is forbidden")
        if d not in meta["dims"]:
            sys.exit(f"error: dim '{d}' not allowed for {table} "
                     f"(allowed dims: {', '.join(meta['dims'])})")
        dim_keys.append(d)

    w = None
    if weight:
        w = _ident(weight, "weight")
        if _is_excluded_column(w):
            sys.exit(f"error: weight '{w}' matches a PII column pattern and is forbidden")
        if w in dim_keys:
            sys.exit(f"error: weight '{w}' is also a dim; pick a numeric column not in dims")
        if w not in meta.get("weights", []):
            sys.exit(f"error: weight '{w}' not allowed for {table} "
                     f"(allowed weights: {', '.join(meta.get('weights', [])) or 'none'})")

    distinct_on = meta.get("distinct_on")
    order_by = meta.get("order_by")

    if fixture is not None:
        raw = load_fixture(Path(fixture))
        records = aggregate_rows(raw, dim_keys, w)
        src = f"fixture {fixture}"
    else:
        sql = build_aggregate_sql(table, dim_keys, weight=w, where=where,
                                  distinct_on=distinct_on, order_by=order_by)
        agg = fetch_live(sql, project)
        # Coerce CLI-returned n/weight into numbers; rows are already aggregate.
        records = []
        for r in agg:
            rec = {d: _norm(r.get(d)) for d in dim_keys}
            rec["n"] = int(_to_num(r.get("n")))
            if w:
                rec[w] = _to_num(r.get(w))
            records.append(rec)
        records.sort(key=lambda d: d["n"], reverse=True)
        src = f"{table} (live via {supabase_bin()})"

    return {
        "table": table,
        "dims": dim_keys,
        "weight": w,
        "unit": meta.get("unit", "rows"),
        "title": meta.get("title", table),
        "count": len(records),
        "source": src,
        "records": records,
    }


# --------------------------------------------------------------------------- #
# SELF-CHECK / CLI
# --------------------------------------------------------------------------- #
def _self_test() -> int:
    here = Path(__file__).resolve().parent
    fixtures = here.parent / "fixtures"
    fx = fixtures / "host_job_runs.sample.json"

    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        status = "ok  " if cond else "FAIL"
        print(f"  [{status}] {msg}")
        if not cond:
            failures.append(msg)

    print("aggregate_source self-check")
    print("- catalog")
    cat = table_catalog()
    check(len(cat) > 0, f"catalog non-empty ({len(cat)} tables)")
    check(all(not _is_excluded_table(t) for t in cat),
          "catalog contains no excluded tables")
    # PII guard: no client-core table survives
    check(not any("client" in t and "core" in t for t in cat),
          "catalog excludes client-core")
    check("host_job_runs" in cat and "prd_artifacts" in cat,
          "expected tables present (host_job_runs, prd_artifacts)")
    # synthetic PII table is filtered out
    _CATALOG["client_core_contacts"] = {"dims": ["client", "email"], "weights": []}
    try:
        check("client_core_contacts" not in table_catalog(),
              "synthetic client_core table is excluded")
    finally:
        del _CATALOG["client_core_contacts"]

    print("- aggregate (offline fixture: host_job_runs)")
    res = aggregate("host_job_runs", ["job_name", "host", "status"], fixture=fx)
    check(res["count"] > 0, f"aggregate produced {res['count']} records (n>0)")
    check(all("n" in r and r["n"] > 0 for r in res["records"]),
          "every record has n>0")
    check(all(set(r.keys()) == {"job_name", "host", "status", "n"}
              for r in res["records"]),
          "records are aggregate-only (dims + n, no raw columns)")
    total_n = sum(r["n"] for r in res["records"])
    check(total_n == len(load_fixture(fx)),
          f"sum(n)={total_n} equals raw row count (no rows lost/duplicated)")
    # spot-check a known group: ecom-hourly-lane/mac-mini/success appears twice
    hit = [r for r in res["records"]
           if r["job_name"] == "ecom-hourly-lane" and r["status"] == "success"]
    check(len(hit) == 1 and hit[0]["n"] == 2,
          "ecom-hourly-lane/success aggregated to n=2")

    print("- guard rails")
    # excluded table rejected (SystemExit)
    try:
        aggregate("client_core_contacts", ["client"], fixture=fx)
        check(False, "excluded table should raise")
    except SystemExit:
        check(True, "excluded table is rejected")
    # disallowed dim rejected
    try:
        aggregate("host_job_runs", ["secret_token"], fixture=fx)
        check(False, "disallowed dim should raise")
    except SystemExit:
        check(True, "disallowed/PII dim is rejected")

    print()
    if failures:
        print(f"SELF-CHECK FAILED ({len(failures)} failures)")
        return 1
    print("SELF-CHECK PASSED")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", action="store_true",
                    help="print the PII-safe table catalog as JSON and exit")
    ap.add_argument("--table", help="table to aggregate (must be in the catalog)")
    ap.add_argument("--dims", help="comma-separated dims to GROUP BY")
    ap.add_argument("--weight", help="numeric weight column to sum (optional)")
    ap.add_argument("--where", help="raw SQL predicate (read-only; live path only)")
    ap.add_argument("--fixture", type=Path,
                    help="aggregate this committed JSON sample instead of querying")
    ap.add_argument("--project", default="gbauto")
    ap.add_argument("--out", type=Path, help="write the aggregate JSON here")
    args = ap.parse_args()

    if args.catalog:
        print(json.dumps(table_catalog(), indent=2))
        return

    if not args.table:
        # No table -> run the self-check / unit test.
        raise SystemExit(_self_test())

    dims = [d.strip() for d in (args.dims or "").split(",") if d.strip()]
    res = aggregate(args.table, dims, weight=args.weight, where=args.where,
                    fixture=args.fixture, project=args.project)
    blob = json.dumps(res, separators=(",", ":"))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(blob, encoding="utf-8")
        print(f"wrote {args.out} ({res['count']} aggregate records, unit={res['unit']})")
    else:
        print(blob)


if __name__ == "__main__":
    main()
