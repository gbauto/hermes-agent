"""Behavioral coverage for the Kanban dashboard sprint projection."""

import json

from plugins.kanban.dashboard import plugin_api as sprint_api
from plugins.kanban.dashboard.plugin_api import (
    _attach_sprint_data_overlay,
    _build_sprint_snapshot,
    _extract_reference_paths,
    _load_sprint_data_overlay,
)


def _task(
    task_id: str,
    title: str,
    status: str,
    *,
    now: int,
    priority: int = 0,
    created_delta: int = 60,
    completed_delta: int | None = None,
    body: str = "",
    result: str = "",
    block_kind: str | None = None,
) -> dict:
    return {
        "id": task_id,
        "title": title,
        "body": body,
        "assignee": "tac-builder",
        "status": status,
        "priority": priority,
        "created_at": now - created_delta,
        "started_at": None,
        "completed_at": now - completed_delta if completed_delta is not None else None,
        "tenant": "gbautomation",
        "result": result,
        "last_failure_error": "validation gate needs an operator decision",
        "project_id": None,
        "block_kind": block_kind,
    }


def _database_snapshot(**overrides) -> dict:
    payload = {
        "schema_version": "hermes-kanban-sprint-data.v1",
        "generated_at": "2026-07-28T10:00:00Z",
        "board": "gbautomation",
        "source": {
            "system": "supabase",
            "project": "gbauto",
            "tables": [
                {
                    "name": "sprint_plans",
                    "matched_rows": 1,
                    "latest_at": "2026-07-28T09:59:00Z",
                }
            ],
        },
        "freshness": {
            "status": "fresh",
            "planning_status": "fresh",
        },
        "warnings": [],
        "sprint": {
            "id": "sprint-01",
            "title": "Connected sprint",
            "status": "active",
            "week_of": "2026-07-27",
        },
        "rocks": [],
        "issues": [],
        "scorecard_metrics": [],
        "task_lineage": {},
        "totals": {"rocks": 0},
    }
    payload.update(overrides)
    return payload


def test_reference_extraction_classifies_and_deduplicates_paths():
    refs = _extract_reference_paths(
        "Plan: `second-brain/plans/2026-07-21-live-kanban.md`. "
        "Proof: second-brain/intelligence/operations/receipt-01.md",
        "repeat second-brain/plans/2026-07-21-live-kanban.md",
    )

    assert refs == [
        {
            "path": "second-brain/plans/2026-07-21-live-kanban.md",
            "kind": "plan",
        },
        {
            "path": "second-brain/intelligence/operations/receipt-01.md",
            "kind": "evidence",
        },
    ]


def test_sprint_snapshot_projects_live_flow_rocks_ids_and_workstreams():
    now = 2_000_000_000
    rows = [
        _task(
            "t_parent",
            "Ship the official Sprint Manager board",
            "ready",
            now=now,
            priority=200,
            body="Plan: second-brain/plans/2026-07-21-sprint-board.md",
        ),
        _task(
            "t_done",
            "Build the scorecard",
            "done",
            now=now,
            completed_delta=120,
            result="Proof: second-brain/intelligence/operations/scorecard-receipt.md",
        ),
        _task(
            "t_blocked",
            "Validate the operator gate",
            "blocked",
            now=now,
            priority=150,
            block_kind="approval_required",
        ),
        _task(
            "t_archived",
            "Land the earlier report",
            "archived",
            now=now,
            completed_delta=180,
        ),
        _task(
            "t_old_done",
            "Old completed work",
            "done",
            now=now,
            created_delta=20 * 86400,
            completed_delta=20 * 86400,
        ),
    ]
    links = [
        {"parent_id": "t_parent", "child_id": "t_done"},
        {"parent_id": "t_parent", "child_id": "t_blocked"},
    ]

    snapshot = _build_sprint_snapshot(rows, links, now=now, days=7)

    assert snapshot["source"]["system"] == "sqlite"
    assert snapshot["source"]["rocks_mode"].startswith("inferred_")
    assert snapshot["scorecard"] == {
        "created": 4,
        "completed": 2,
        "flow_delta": -2,
        "open": 2,
        "active": 1,
        "blocked": 1,
        "ready": 1,
        "running": 0,
        "review": 0,
    }
    assert snapshot["rocks"][0]["id"] == "t_parent"
    assert snapshot["rocks"][0]["progress"] == {"done": 1, "total": 2}
    assert snapshot["issues"][0]["block_kind"] == "approval_required"
    assert snapshot["workstreams"][0]["id"] == "t_parent"
    assert snapshot["workstreams"][0]["total"] == 3
    assert snapshot["workstreams"][0]["counts"] == {
        "ready": 1,
        "done": 1,
        "blocked": 1,
    }

    refs = {entry["path"]: entry for entry in snapshot["references"]}
    assert refs["second-brain/plans/2026-07-21-sprint-board.md"]["kind"] == "plan"
    assert refs["second-brain/plans/2026-07-21-sprint-board.md"]["status"] == "ready"
    assert refs["second-brain/intelligence/operations/scorecard-receipt.md"]["kind"] == "evidence"
    assert refs["second-brain/intelligence/operations/scorecard-receipt.md"]["status"] == "done"


def test_sprint_snapshot_bounds_reference_and_issue_payloads():
    now = 2_000_000_000
    rows = [
        _task(
            f"t_{index}",
            f"Blocked card {index}",
            "blocked",
            now=now,
            priority=index,
            body=f"artifacts/receipts/proof-{index}.json",
        )
        for index in range(20)
    ]

    snapshot = _build_sprint_snapshot(rows, [], now=now, days=7)

    assert len(snapshot["issues"]) == 12
    assert snapshot["issues"][0]["id"] == "t_19"
    assert len(snapshot["references"]) == 20
    assert all("body" not in issue for issue in snapshot["issues"])


def test_sprint_data_overlay_loads_valid_board_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(sprint_api.kanban_db, "board_dir", lambda _board: tmp_path)
    path = tmp_path / "sprint-data.json"
    path.write_text(
        json.dumps(_database_snapshot(unknown_future_field="ignored")),
        encoding="utf-8",
    )
    now = sprint_api._iso_epoch("2026-07-28T12:00:00Z")

    result = _load_sprint_data_overlay("gbautomation", now=now)

    assert result["status"] == "fresh"
    assert result["available"] is True
    assert result["snapshot"]["sprint"]["id"] == "sprint-01"
    assert "unknown_future_field" not in result["snapshot"]

    stale_now = now + sprint_api._SPRINT_DATA_MAX_AGE_SECONDS + 1
    stale = _load_sprint_data_overlay("gbautomation", now=stale_now)
    assert stale["status"] == "stale"
    assert stale["snapshot"]["sprint"]["id"] == "sprint-01"


def test_sprint_data_overlay_reports_missing_wrong_schema_and_wrong_board(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(sprint_api.kanban_db, "board_dir", lambda _board: tmp_path)
    path = tmp_path / "sprint-data.json"

    assert _load_sprint_data_overlay("gbautomation")["status"] == "missing"

    for payload in (
        _database_snapshot(schema_version="future.v2"),
        _database_snapshot(board="another-board"),
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = _load_sprint_data_overlay("gbautomation")
        assert result["status"] == "invalid"
        assert result["snapshot"] is None


def test_sprint_data_overlay_rejects_malformed_and_oversized_files(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(sprint_api.kanban_db, "board_dir", lambda _board: tmp_path)
    path = tmp_path / "sprint-data.json"

    path.write_text("{not-json", encoding="utf-8")
    assert _load_sprint_data_overlay("gbautomation")["status"] == "invalid"

    path.write_bytes(b"x" * (sprint_api._SPRINT_DATA_MAX_BYTES + 1))
    result = _load_sprint_data_overlay("gbautomation")
    assert result["status"] == "invalid"
    assert "safety limit" in result["message"]


def test_invalid_database_overlay_preserves_native_sprint_projection(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(sprint_api.kanban_db, "board_dir", lambda _board: tmp_path)
    (tmp_path / "sprint-data.json").write_text("{bad", encoding="utf-8")
    now = 2_000_000_000
    local = _build_sprint_snapshot(
        [_task("t_ready", "Board fallback", "ready", now=now)],
        [],
        now=now,
        days=7,
    )

    combined = _attach_sprint_data_overlay(
        local,
        board="gbautomation",
        now=now,
    )

    assert combined["source"]["system"] == "sqlite"
    assert combined["rocks"][0]["id"] == "t_ready"
    assert combined["data_spine"]["status"] == "invalid"
    assert combined["data_spine"]["snapshot"] is None
