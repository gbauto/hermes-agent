"""Supabase-backed observability reads for the Hermes dashboard logs page.

All database access intentionally goes through the ``gbauto-supabase`` CLI.
The dashboard exposes fixed read endpoints only; callers never provide raw SQL.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


MAX_DAYS = 30
MAX_LIMIT = 500

_CACHE: Dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True)
class QueryBounds:
    days: int = 7
    limit: int = 100


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _bounds(days: Any, limit: Any, *, default_days: int = 7) -> QueryBounds:
    return QueryBounds(
        days=_clamp_int(days, default_days, 1, MAX_DAYS),
        limit=_clamp_int(limit, 100, 1, MAX_LIMIT),
    )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''").replace("%", "%%") + "'"


def _search_clause(columns: list[str], search: Optional[str]) -> str:
    if not search:
        return ""
    needle = search.strip().lower()
    if not needle:
        return ""
    pattern = _sql_literal(f"%{needle}%")
    checks = [f"lower(coalesce({col}::text, '')) like {pattern}" for col in columns]
    return " and (" + " or ".join(checks) + ")"


def _contains_clause(columns: list[str], value: Optional[str]) -> str:
    if not value:
        return ""
    needle = value.strip().lower()
    if not needle:
        return ""
    pattern = _sql_literal(f"%{needle}%")
    checks = [f"lower(coalesce({col}::text, '')) like {pattern}" for col in columns]
    return " and (" + " or ".join(checks) + ")"


def _enum_clause(column: str, value: Optional[str], allowed: set[str]) -> str:
    if not value or value == "all":
        return ""
    normalized = value.strip().lower()
    if normalized not in allowed:
        return ""
    return f" and lower(coalesce({column}::text, '')) = {_sql_literal(normalized)}"


def _source_clause(column: str, value: Optional[str], allowed: set[str]) -> str:
    if not value or value == "all":
        return ""
    normalized = value.strip()
    if normalized not in allowed:
        return ""
    return f" and {column} = {_sql_literal(normalized)}"


def _run_cli(sql: str, *, timeout: int = 45) -> list[dict[str, Any]]:
    binary = shutil.which("gbauto-supabase")
    if not binary:
        raise RuntimeError("gbauto-supabase CLI is not on PATH")

    proc = subprocess.run(
        [binary, "--json", "query", sql],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "gbauto-supabase query failed").strip()
        raise RuntimeError(message[:1200])

    data = json.loads(proc.stdout or "[]")
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(str(data.get("error") or "gbauto-supabase query failed"))
    if isinstance(data, dict) and isinstance(data.get("value"), list):
        data = data["value"]
    if not isinstance(data, list):
        raise RuntimeError("gbauto-supabase returned an unexpected response shape")
    return [row for row in data if isinstance(row, dict)]


def _cached(key: str, ttl_s: int, loader: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < ttl_s:
        return cached[1]
    try:
        payload = loader()
    except Exception as exc:
        payload = {"ok": False, "error": str(exc), "rows": []}
    _CACHE[key] = (now, payload)
    return payload


def get_summary() -> dict[str, Any]:
    def load() -> dict[str, Any]:
        rows = _run_cli(
            """
            select
              now() as generated_at,
              (select count(*) from ops_run_timeline where started_at >= now() - interval '3 days')::int as timeline_rows,
              (select count(*) from ops_recent_failures where started_at >= now() - interval '14 days')::int as recent_failures,
              (select count(*) from cron_runs where started_at >= now() - interval '14 days')::int as cron_runs,
              (select count(*) from cron_run_outputs o left join cron_runs c on c.tick_id = o.tick_id where c.started_at >= now() - interval '14 days' or c.started_at is null)::int as cron_outputs,
              (select count(*) from host_job_runs where created_at >= now() - interval '14 days')::int as host_receipts,
              (select count(*) from agent_log_artifacts where modified_at >= now() - interval '14 days')::int as log_artifacts,
              (select count(*) from langfuse_traces where trace_timestamp >= now() - interval '7 days')::int as traces,
              (select coalesce(round(sum(total_cost)::numeric, 4), 0) from langfuse_traces where trace_timestamp >= now() - interval '7 days') as trace_cost,
              (select coalesce(sum(total_tokens), 0) from langfuse_traces where trace_timestamp >= now() - interval '7 days')::int as trace_tokens,
              (select obs_join_candidates from ops_trace_coverage order by measured_at desc nulls last limit 1)::int as obs_join_candidates,
              (select obs_with_trace from ops_trace_coverage order by measured_at desc nulls last limit 1)::int as obs_with_trace,
              (select obs_langfuse_matches from ops_trace_coverage order by measured_at desc nulls last limit 1)::int as obs_langfuse_matches
            """
        )
        row = rows[0] if rows else {}
        return {
            "ok": True,
            "generated_at": row.get("generated_at"),
            "windows": {"timeline_days": 3, "failure_days": 14, "trace_days": 7},
            "counts": {
                "timeline_rows": row.get("timeline_rows", 0),
                "recent_failures": row.get("recent_failures", 0),
                "cron_runs": row.get("cron_runs", 0),
                "cron_outputs": row.get("cron_outputs", 0),
                "host_receipts": row.get("host_receipts", 0),
                "log_artifacts": row.get("log_artifacts", 0),
                "traces": row.get("traces", 0),
            },
            "trace_totals": {
                "cost": row.get("trace_cost", 0),
                "tokens": row.get("trace_tokens", 0),
            },
            "trace_coverage": {
                "obs_join_candidates": row.get("obs_join_candidates", 0),
                "obs_with_trace": row.get("obs_with_trace", 0),
                "obs_langfuse_matches": row.get("obs_langfuse_matches", 0),
            },
        }

    return _cached("summary", 30, load)


def get_timeline(*, days: Any = 3, limit: Any = 100, status: Optional[str] = None,
                 source: Optional[str] = None, search: Optional[str] = None,
                 repo: Optional[str] = None, workdir: Optional[str] = None) -> dict[str, Any]:
    bounds = _bounds(days, limit, default_days=3)
    key = f"timeline:{bounds.days}:{bounds.limit}:{status}:{source}:{search}:{repo}:{workdir}"

    def load() -> dict[str, Any]:
        sql = f"""
            select run_key, run_type, source_table, source_id, parent_run_key,
                   domain, profile, client_slug, repo_slug, raw_status, status_family,
                   title, started_at, ended_at, duration_ms, trace_id
            from ops_run_timeline
            where started_at >= now() - interval '{bounds.days} days'
            {_enum_clause("status_family", status, {"failed", "succeeded", "unknown"})}
            {_source_clause("source_table", source, {"agent_runs", "browser_control_runs", "cron_runs", "skill_runs", "host_job_runs"})}
            {_contains_clause(["repo_slug"], repo)}
            {_contains_clause(["title"], workdir)}
            {_search_clause(["run_key", "source_id", "profile", "client_slug", "repo_slug", "title", "trace_id"], search)}
            order by started_at desc nulls last
            limit {bounds.limit}
        """
        return {"ok": True, "rows": _run_cli(sql), "days": bounds.days, "limit": bounds.limit}

    return _cached(key, 15, load)


def get_failures(*, days: Any = 14, limit: Any = 100, search: Optional[str] = None,
                 repo: Optional[str] = None, workdir: Optional[str] = None) -> dict[str, Any]:
    bounds = _bounds(days, limit, default_days=14)
    key = f"failures:{bounds.days}:{bounds.limit}:{search}:{repo}:{workdir}"

    def load() -> dict[str, Any]:
        sql = f"""
            select run_key, run_type, source_table, source_id, parent_run_key,
                   domain, profile, client_slug, repo_slug, raw_status, status_family,
                   title, started_at, ended_at, duration_ms, trace_id
            from ops_recent_failures
            where started_at >= now() - interval '{bounds.days} days'
            {_contains_clause(["repo_slug"], repo)}
            {_contains_clause(["title"], workdir)}
            {_search_clause(["run_key", "source_id", "profile", "client_slug", "repo_slug", "title", "trace_id"], search)}
            order by started_at desc nulls last
            limit {bounds.limit}
        """
        return {"ok": True, "rows": _run_cli(sql), "days": bounds.days, "limit": bounds.limit}

    return _cached(key, 15, load)


def get_artifacts(*, days: Any = 14, limit: Any = 100, search: Optional[str] = None,
                  repo: Optional[str] = None, workdir: Optional[str] = None) -> dict[str, Any]:
    bounds = _bounds(days, limit, default_days=14)
    key = f"artifacts:{bounds.days}:{bounds.limit}:{search}:{repo}:{workdir}"

    def load() -> dict[str, Any]:
        sql = f"""
            select source_id, client_slug, repo_slug, agent, category, source_host,
                   path, basename, file_size_bytes, modified_at, content_mode,
                   captured_bytes, metadata, first_seen_at, last_synced_at
            from agent_log_artifacts
            where modified_at >= now() - interval '{bounds.days} days'
            {_contains_clause(["repo_slug"], repo)}
            {_contains_clause(["path"], workdir)}
            {_search_clause(["source_id", "client_slug", "repo_slug", "agent", "category", "source_host", "path", "basename"], search)}
            order by modified_at desc nulls last
            limit {bounds.limit}
        """
        return {"ok": True, "rows": _run_cli(sql), "days": bounds.days, "limit": bounds.limit}

    return _cached(key, 60, load)


def get_cron(*, days: Any = 14, limit: Any = 100, search: Optional[str] = None,
             repo: Optional[str] = None, workdir: Optional[str] = None) -> dict[str, Any]:
    bounds = _bounds(days, limit, default_days=14)
    key = f"cron:{bounds.days}:{bounds.limit}:{search}:{repo}:{workdir}"

    def load() -> dict[str, Any]:
        rollup_sql = f"""
            select cron_name, count(*)::int as runs, coalesce(sum(ok_count), 0)::int as ok_outputs,
                   coalesce(sum(fail_count), 0)::int as failed_outputs,
                   coalesce(round(sum(total_cost_usd)::numeric, 4), 0) as cost_usd,
                   max(started_at) as latest_started_at
            from cron_runs
            where started_at >= now() - interval '{bounds.days} days'
            {_contains_clause(["cron_name", "host"], repo)}
            {_contains_clause(["cron_name", "host"], workdir)}
            {_search_clause(["cron_name", "host"], search)}
            group by cron_name
            order by failed_outputs desc, latest_started_at desc nulls last
            limit {bounds.limit}
        """
        outputs_sql = f"""
            select o.output_id, o.tick_id, c.cron_name, c.started_at as cron_started_at,
                   o.issue_id, o.status, o.branch, o.pr_url, o.cost_usd, o.turns,
                   o.duration_s, left(coalesce(o.stderr_tail, ''), 2000) as stderr_tail,
                   o.agent_summary
            from cron_run_outputs o
            left join cron_runs c on c.tick_id = o.tick_id
            where (c.started_at >= now() - interval '{bounds.days} days' or c.started_at is null)
            {_contains_clause(["c.cron_name", "o.branch", "o.pr_url"], repo)}
            {_contains_clause(["c.cron_name", "o.branch", "o.pr_url", "o.agent_summary"], workdir)}
            {_search_clause(["c.cron_name", "o.issue_id", "o.status", "o.branch", "o.pr_url", "o.agent_summary", "o.stderr_tail"], search)}
            order by c.started_at desc nulls last
            limit {bounds.limit}
        """
        return {
            "ok": True,
            "rollup": _run_cli(rollup_sql),
            "outputs": _run_cli(outputs_sql),
            "days": bounds.days,
            "limit": bounds.limit,
        }

    return _cached(key, 15, load)


def get_host_jobs(*, days: Any = 14, limit: Any = 100, status: Optional[str] = None,
                  search: Optional[str] = None, repo: Optional[str] = None,
                  workdir: Optional[str] = None) -> dict[str, Any]:
    bounds = _bounds(days, limit, default_days=14)
    key = f"host:{bounds.days}:{bounds.limit}:{status}:{search}:{repo}:{workdir}"

    def load() -> dict[str, Any]:
        sql = f"""
            select run_id, created_at, pathway, host, host_class, task_name,
                   skill_name, command_label, working_directory, status,
                   stdout_bytes, stderr_bytes, receipt_path, inserted_at
            from host_job_runs
            where created_at >= now() - interval '{bounds.days} days'
            {_enum_clause("status", status, {"ok", "failed", "error"})}
            {_contains_clause(["working_directory", "receipt_path"], repo)}
            {_contains_clause(["working_directory"], workdir)}
            {_search_clause(["run_id", "pathway", "host", "host_class", "task_name", "skill_name", "command_label", "working_directory", "status", "receipt_path"], search)}
            order by created_at desc
            limit {bounds.limit}
        """
        return {"ok": True, "rows": _run_cli(sql), "days": bounds.days, "limit": bounds.limit}

    return _cached(key, 15, load)


def get_traces(*, days: Any = 7, limit: Any = 100, search: Optional[str] = None,
               repo: Optional[str] = None, workdir: Optional[str] = None) -> dict[str, Any]:
    bounds = _bounds(days, limit, default_days=7)
    key = f"traces:{bounds.days}:{bounds.limit}:{search}:{repo}:{workdir}"

    def load() -> dict[str, Any]:
        traces_sql = f"""
            select trace_id, trace_name, trace_timestamp, tags, runtime, agent,
                   profile, latency_sec, total_cost, input_tokens, output_tokens,
                   total_tokens, observation_count, langfuse_url, last_synced_at
            from langfuse_traces
            where trace_timestamp >= now() - interval '{bounds.days} days'
            {_contains_clause(["tags::text", "trace_name"], repo)}
            {_contains_clause(["tags::text", "trace_name"], workdir)}
            {_search_clause(["trace_id", "trace_name", "runtime", "agent", "profile", "langfuse_url"], search)}
            order by trace_timestamp desc nulls last
            limit {bounds.limit}
        """
        gaps_sql = f"""
            select session_id, task_id, run_id, trace_id, trace_url, board, tenant,
                   profile, repo, branch, first_seen_at, last_seen_at, event_count,
                   obs_total_tokens, obs_cost_total, needs_review, langfuse_trace_id,
                   trace_name, trace_timestamp, has_langfuse_trace, match_key
            from obs_langfuse_trace_join
            where coalesce(has_langfuse_trace, false) = false
            {_contains_clause(["repo"], repo)}
            {_contains_clause(["repo", "branch"], workdir)}
            {_search_clause(["session_id", "task_id", "run_id", "trace_id", "board", "tenant", "profile", "repo", "branch"], search)}
            order by last_seen_at desc nulls last
            limit {min(bounds.limit, 100)}
        """
        coverage_sql = f"""
            select run_day, client_slug, repo_slug, board_slug, profile, run_count,
                   runs_with_trace_id, runs_with_trace_mirror,
                   trace_id_coverage_pct, trace_mirror_coverage_pct
            from v_agent_trace_coverage
            where true
            {_contains_clause(["repo_slug"], repo)}
            order by run_day desc nulls last, run_count desc nulls last
            limit 100
        """
        return {
            "ok": True,
            "rows": _run_cli(traces_sql),
            "gaps": _run_cli(gaps_sql),
            "coverage": _run_cli(coverage_sql),
            "days": bounds.days,
            "limit": bounds.limit,
        }

    return _cached(key, 15, load)
