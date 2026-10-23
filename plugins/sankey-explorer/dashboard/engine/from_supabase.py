#!/usr/bin/env python3
"""from_supabase.py — feed a Supabase table straight into the 3D Sankey.

A thin, READ-ONLY wrapper: it builds a SELECT, runs it through the
``gbauto-supabase`` CLI (``--json query`` — read-only, DML silently rolls back),
normalizes the returned rows into a ``{"records": [...]}`` array, and hands that
to ``build_sankey.py`` to render a single self-contained HTML chart.

    python from_supabase.py --table host_job_runs \
        --dims job_name,host,status,scheduler \
        --title "Host Job Runs" --unit runs \
        --out artifacts/supabase-sankey/host-job-runs.html

Hard rules honoured (see memory):
  * EVERY Supabase read goes through the gbauto-supabase CLI ONLY — never
    psycopg2 / PostgREST / supabase-py. ``query`` is read-only.
  * No secrets are ever printed; the CLI resolves credentials itself from AWS SM.
  * Field/table names are validated as plain SQL identifiers before they reach
    the query, so the constructed SELECT stays well-formed.

OFFLINE PROVABILITY
  Several Supabase refs are DNS-blocked from the PC (Mini-only). Pass
  ``--fixture <path>`` to read a committed JSON sample instead of calling the
  CLI, so the whole pipeline (fetch -> normalize -> render) is provable offline.

DISTINCT-ON (agent_runs)
  agent_runs over-counts crashed rows (one row per Kanban poll). Use
  ``--distinct-on task_id --order-by "created_at desc"`` to collapse to the
  latest row per task — the script emits
  ``select distinct on (task_id) ... order by task_id, created_at desc``.

Run from the repo root. From a Mini cron, export PATH first:
  export PATH="$HOME/.local/bin:$PATH"   # so gbauto-supabase resolves
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Reuse build_sankey's dim-spec parser so --dims behaves identically here.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_sankey import parse_dims  # noqa: E402

BUILD = Path(__file__).resolve().parent / "build_sankey.py"

# A plain SQL identifier (optionally schema-qualified for --table).
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")

# Keys a --json CLI / fixture payload might wrap result rows under.
_ROW_KEYS = ("rows", "data", "result", "records", "items")


def supabase_bin() -> str:
    return os.environ.get("GBAUTO_SUPABASE_BIN", "gbauto-supabase")


def _ident(name: str, kind: str) -> str:
    """Validate a bare SQL identifier (a dim key / weight / distinct-on field)."""
    if not _IDENT.match(name or ""):
        sys.exit(f"error: {kind} '{name}' is not a plain SQL identifier "
                 "(letters, digits, underscore; must start with a letter/underscore)")
    return name


def _table(name: str) -> str:
    if not _TABLE.match(name or ""):
        sys.exit(f"error: --table '{name}' is not a valid identifier (schema.table allowed)")
    return name


def build_sql(table: str, fields: list[str], *, where: str | None,
              distinct_on: str | None, order_by: str | None, limit: int | None) -> str:
    """Construct a read-only SELECT. Identifiers are pre-validated by the caller.

    --where / --order-by are raw operator-supplied SQL fragments (this is a
    read-only query, so they carry no write risk) and are passed through verbatim.
    """
    cols = ", ".join(fields)
    select = f"select distinct on ({distinct_on}) {cols}" if distinct_on else f"select {cols}"
    sql = f"{select} from {table}"
    if where:
        sql += f" where {where}"
    # DISTINCT ON requires the leftmost ORDER BY expression to be the distinct key.
    order_terms: list[str] = []
    ob = order_by.strip() if order_by else ""
    if distinct_on:
        order_terms.append(distinct_on)
        # only append the user fragment if it doesn't already lead with the key
        if ob and not ob.lower().startswith(distinct_on.lower()):
            order_terms.append(ob)
    elif ob:
        order_terms.append(ob)
    if order_terms:
        sql += " order by " + ", ".join(order_terms)
    if limit:
        sql += f" limit {int(limit)}"
    return sql


def _parse_cli_json(stdout: str):
    """Parse the CLI's --json stdout, tolerating a human line before the blob."""
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for opener, closer in (("{", "}"), ("[", "]")):
            start = text.find(opener)
            end = text.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    continue
    return None


def extract_rows(parsed) -> list[dict]:
    """Pull a list-of-dicts out of whatever shape the CLI / fixture returned."""
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return [r for r in parsed if isinstance(r, dict)]
    if isinstance(parsed, dict):
        for key in _ROW_KEYS:
            value = parsed.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        if "ok" not in parsed and "error" not in parsed:
            return [parsed]
    return []


def fetch_live(sql: str, project: str) -> list[dict]:
    """Run a read-only SELECT through ``gbauto-supabase --json query``."""
    cmd = [supabase_bin(), "--project", project, "--json", "query", sql]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=180, creationflags=creationflags,
        )
    except FileNotFoundError:
        sys.exit(f"error: {supabase_bin()} not found on PATH. All Supabase access "
                 "must go through the gbauto-supabase CLI (export PATH=$HOME/.local/bin "
                 "on Mini cron) or set GBAUTO_SUPABASE_BIN.")
    except subprocess.TimeoutExpired:
        sys.exit("error: gbauto-supabase query timed out after 180s")

    parsed = _parse_cli_json(proc.stdout)
    err = (proc.stderr or "").strip()
    if isinstance(parsed, dict) and (parsed.get("ok") is False or parsed.get("error")):
        sys.exit(f"error: query failed: {parsed.get('error') or parsed.get('message')}")
    if proc.returncode != 0:
        # NOTE: do not echo argv (no secrets are in it, but keep output clean).
        sys.exit(f"error: gbauto-supabase exited {proc.returncode}: "
                 f"{err or proc.stdout.strip() or 'no output'}")
    return extract_rows(parsed)


def load_fixture(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"error: --fixture {path} not found")
    return extract_rows(json.loads(path.read_text(encoding="utf-8")))


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", required=True, help="Supabase table/view (schema.table allowed)")
    ap.add_argument("--dims", required=True, help="key:Label,key:Label,... (selectable columns)")
    ap.add_argument("--out", required=True, type=Path, help="output HTML path")
    ap.add_argument("--weight", help="numeric field to size flows by (optional; not a dim)")
    ap.add_argument("--weight-label", help="display label for the weight measure")
    ap.add_argument("--where", help="raw SQL predicate (read-only; e.g. \"status='failed'\")")
    ap.add_argument("--distinct-on", help="DISTINCT ON (field) — e.g. task_id for agent_runs")
    ap.add_argument("--order-by", help="raw ORDER BY fragment (e.g. \"created_at desc\")")
    ap.add_argument("--limit", type=int, default=20000, help="row cap (default 20000)")
    ap.add_argument("--default", help="three dim keys for left,middle,right columns")
    ap.add_argument("--title", default="Supabase Sankey")
    ap.add_argument("--unit", default="rows")
    ap.add_argument("--project", default="gbauto", help="gbauto-supabase project (default: gbauto)")
    ap.add_argument("--fixture", type=Path,
                    help="read rows from this JSON file instead of calling the CLI (offline)")
    ap.add_argument("--data", type=Path,
                    help="path for the intermediate records JSON (default: <out>.data.json)")
    ap.add_argument("--inline-three", action="store_true",
                    help="passthrough to build_sankey: inline three.js for offline viewing")
    args = ap.parse_args()

    table = _table(args.table)
    dims = parse_dims(args.dims)
    if len(dims) < 2:
        sys.exit(f"error: need at least 2 dims (got {len(dims)})")
    dim_keys = [_ident(k, "dim") for k, _ in dims]

    weight = _ident(args.weight, "weight") if args.weight else None
    if weight and weight in dim_keys:
        sys.exit(f"error: --weight '{weight}' is also a dim; pick a numeric field not in --dims")
    distinct_on = _ident(args.distinct_on, "distinct-on") if args.distinct_on else None

    select_fields = list(dim_keys)
    if weight:
        select_fields.append(weight)
    # DISTINCT ON needs its key present in the projection for the row to survive.
    if distinct_on and distinct_on not in select_fields:
        select_fields.append(distinct_on)

    sql = build_sql(table, select_fields, where=args.where, distinct_on=distinct_on,
                    order_by=args.order_by, limit=args.limit)

    if args.fixture:
        rows = load_fixture(args.fixture)
        src = f"fixture {args.fixture}"
    else:
        rows = fetch_live(sql, args.project)
        src = f"{args.table} (live via gbauto-supabase)"
    if not rows:
        sys.exit(f"error: no rows from {src}\n  SQL: {sql}")

    data_path = args.data or args.out.with_suffix(".data.json")
    data_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"count": len(rows), "records": rows}
    data_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"fetched {len(rows)} rows from {src} -> {data_path}")
    print(f"  SQL: {sql}")

    cmd = [
        sys.executable, str(BUILD),
        "--records", str(data_path), "--out", str(args.out),
        "--dims", args.dims, "--title", args.title, "--unit", args.unit,
    ]
    if weight:
        cmd += ["--weight", weight]
        if args.weight_label:
            cmd += ["--weight-label", args.weight_label]
    if args.default:
        cmd += ["--default", args.default]
    if args.inline_three:
        cmd += ["--inline-three"]
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
