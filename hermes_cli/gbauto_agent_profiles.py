"""Live Supabase profile/team catalog for the dashboard /profiles page."""

from __future__ import annotations

from typing import Any, Optional

from hermes_cli.gbauto_supabase_logs import _run_cli, _sql_literal


ALLOWED_TENANTS = {"smoke-client", "gbautomation", "jid5274", "ecom"}


def load_profile_catalog(tenant: Optional[str] = None) -> dict[str, Any]:
    selected = (tenant or "gbautomation").strip()
    if selected not in ALLOWED_TENANTS:
        raise ValueError(f"tenant not allowed: {selected}")

    tenant_sql = _sql_literal(selected)
    teams = _run_cli(
        "select team_key, tenant, team_id, display_name, runtime, purpose, "
        "canonical_agent_team, canonical_profile_team, orchestrator_profile, lead_profile, "
        "specialist_profiles, existing_specialist_profiles, source_path, indexed_at, metadata "
        "from public.agent_profile_teams "
        f"where tenant = {tenant_sql} "
        "order by display_name, team_id"
    )
    profiles = _run_cli(
        "select profile_key, tenant, profile_id, profile_type, runtime, display_name, role, "
        "status, model, provider, source_path, source_kind, deployment_target, deploy_user, "
        "deploy_path, team_id, team_display_name, route_keys, suggested_skills, "
        "prompt_manifest_path, package_path, indexed_at, skill_count, route_count "
        "from public.agent_profile_catalog "
        f"where tenant = {tenant_sql} "
        "order by coalesce(team_id, ''), profile_type, profile_id"
    )
    routes = _run_cli(
        "select profile_route_key, tenant, route_name, route_kind, source_profile_key, "
        "target_type, target_profile_key, target_profile_id, target_team_id, source_path, "
        "metadata, indexed_at "
        "from public.agent_profile_routes "
        f"where tenant = {tenant_sql} "
        "order by source_path, route_name, target_profile_id, target_team_id"
    )
    return {
        "ok": True,
        "source": "supabase:agent_profile_catalog",
        "tenant": selected,
        "teams": teams,
        "profiles": profiles,
        "routes": routes,
    }
