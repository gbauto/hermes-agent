"""Bounded offline regression checks for Kanban worker admission and handoff."""

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _skill(home, relative):
    target = home / "skills" / relative / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\nname: kanban-worker\ndescription: Worker.\n---\n\n# Worker\n")


def test_codex_bad_override_blocks_before_claim(isolated_home, monkeypatch, all_assignees_spawnable):
    (isolated_home / "config.yaml").write_text("provider: openai-codex\n")
    monkeypatch.setattr(kb, "_worker_profile_home", lambda profile: str(isolated_home))
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="bad model", assignee="builder")
        conn.execute("UPDATE tasks SET model_override = ? WHERE id = ?", ("gpt-5.5", tid))
        dry = kb.dispatch_once(conn, dry_run=True)
        assert dry.spawned == []
        assert "unsupported" in dry.readiness_failed[0][1]
        assert kb.get_task(conn, tid).status == "ready"
        live = kb.dispatch_once(conn, spawn_fn=lambda *args, **kwargs: pytest.fail("must not spawn"))
        assert live.spawned == []
        assert kb.get_task(conn, tid).status == "blocked"
        assert kb.get_task(conn, tid).consecutive_failures == 0
        assert not any(e.kind == "claimed" for e in kb.list_events(conn, tid))
        kb.recompute_ready(conn)
        assert kb.get_task(conn, tid).status == "blocked"


@pytest.mark.parametrize(
    ("config", "effective_provider"),
    [
        ("provider: anthropic\nmodel:\n  provider: openai-codex\n  default: gpt-5.5\n", "openai-codex"),
        ("provider: openai-codex\nmodel:\n  provider: anthropic\n  default: gpt-5.5\n", "anthropic"),
    ],
)
def test_model_provider_precedence_matches_cli(isolated_home, config, effective_provider):
    (isolated_home / "config.yaml").write_text(config)
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="nested provider", assignee="builder")
        task = kb.get_task(conn, tid)
        assert task is not None
        if effective_provider == "openai-codex":
            with pytest.raises(kb.WorkerSkillReadinessError) as exc:
                kb._worker_model_preflight(task, str(isolated_home))
            assert exc.value.payload["provider"] == effective_provider
            assert exc.value.payload["effective_model"] == "gpt-5.5"
        else:
            assert kb._worker_model_preflight(task, str(isolated_home))["provider"] == effective_provider


def test_model_default_wins_over_legacy_model_name(isolated_home):
    (isolated_home / "config.yaml").write_text(
        "model:\n  provider: openai-codex\n  default: gpt-5.5\n  model: gpt-6-sol\n"
    )
    with kb.connect() as conn:
        task = kb.get_task(conn, kb.create_task(conn, title="effective default"))
        assert task is not None
        with pytest.raises(kb.WorkerSkillReadinessError) as exc:
            kb._worker_model_preflight(task, str(isolated_home))
        assert exc.value.payload["effective_model"] == "gpt-5.5"


def test_explicit_builtin_missing_is_required(isolated_home, all_assignees_spawnable):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="required builtin", assignee="builder", skills=["kanban-worker"])
        result = kb.dispatch_once(conn, dry_run=True)
        assert result.spawned == []
        assert result.readiness_failed[0][0] == tid
        assert "missing required skill" in result.readiness_failed[0][1]


def test_implicit_builtin_collision_omitted_but_explicit_fails(isolated_home, all_assignees_spawnable):
    _skill(isolated_home, "alpha/kanban-worker")
    _skill(isolated_home, "beta/kanban-worker")
    _skill(isolated_home, "backup/kanban-worker.bak")
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="collision", assignee="builder")
        task = kb.get_task(conn, tid)
        assert task is not None
        argv, payload = kb._worker_skill_preflight(task, profile_arg="builder", hermes_home=str(isolated_home))
        assert "kanban-worker" not in argv
        assert "kanban-worker" in payload["ambiguous_skills"]
        result = kb.dispatch_once(conn, dry_run=True)
        assert [row[0] for row in result.spawned] == [tid]
        assert result.readiness_failed == []
        explicit = kb.create_task(conn, title="explicit collision", assignee="builder", skills=["kanban-worker"])
        result = kb.dispatch_once(conn, dry_run=True)
        assert any(row[0] == explicit and "ambiguous" in row[1] for row in result.readiness_failed)
        qualified = kb.create_task(conn, title="qualified collision", assignee="builder",
                                   skills=["alpha/kanban-worker"])
        selected = kb.get_task(conn, qualified)
        assert selected is not None
        argv, _ = kb._worker_skill_preflight(selected, profile_arg="builder", hermes_home=str(isolated_home))
        assert argv == ["alpha/kanban-worker"]


def test_declared_handoff_requires_readback_and_claim_rechecks(isolated_home, tmp_path):
    with kb.connect() as conn:
        producer = kb.create_task(conn, title="producer")
        consumer = kb.create_task(conn, title="consumer", parents=[producer])
        receipt = tmp_path / "declared.txt"
        assert kb.complete_task(conn, producer, metadata={"artifacts": [str(receipt)]})
        assert kb.get_task(conn, consumer).status == "todo"
        assert kb.reconcile_task_state(conn, consumer)["state"] == "dependency_evidence_missing"
        receipt.write_text("handoff")
        assert kb.reconcile_task_state(conn, consumer)["state"] == "ready_starvation_repaired"
        receipt.unlink()
        assert kb.claim_task(conn, consumer) is None
        assert kb.get_task(conn, consumer).status == "todo"
        receipt.write_text("handoff")
        assert kb.reconcile_task_state(conn, consumer)["changed"] is True
        assert kb.reconcile_task_state(conn, consumer)["changed"] is False


def test_reconciliation_reports_failed_stale_done_without_receipt_and_no_dispatch(isolated_home):
    with kb.connect() as conn:
        starved = kb.create_task(conn, title="starved")
        conn.execute("UPDATE tasks SET status = 'todo' WHERE id = ?", (starved,))
        assert kb.reconcile_task_state(conn, starved)["state"] == "ready_starvation_repaired"
        failed = kb.create_task(conn, title="failed")
        conn.execute("UPDATE tasks SET status = 'blocked', consecutive_failures = 3 WHERE id = ?", (failed,))
        assert kb.reconcile_task_state(conn, failed)["state"] == "failed_blocked"
        stale = kb.create_task(conn, title="stale")
        conn.execute("UPDATE tasks SET status = 'running', claim_expires = 10 WHERE id = ?", (stale,))
        assert kb.reconcile_task_state(conn, stale, now=11)["state"] == "stale_claim"
        orphan = kb.create_task(conn, title="done without receipt")
        child = kb.create_task(conn, title="dependent", parents=[orphan])
        conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (orphan,))
        assert kb.reconcile_task_state(conn, orphan)["state"] == "done_without_receipt"
        assert kb.reconcile_task_state(conn, child)["state"] == "dependency_evidence_missing"
        assert kb.get_task(conn, child).status == "todo"
        assert kb.get_task(conn, failed).status == "blocked"
        assert kb.get_task(conn, stale).status == "running"


def test_archiving_last_dependent_releases_owned_scratch(isolated_home):
    with kb.connect() as conn:
        parent = kb.create_task(conn, title="producer")
        child = kb.create_task(conn, title="consumer", parents=[parent])
        workspace = kb.resolve_workspace(kb.get_task(conn, parent))
        kb.set_workspace_path(conn, parent, workspace)
        (workspace / "handoff.txt").write_text("data")
        assert kb.complete_task(conn, parent)
        assert workspace.exists()
        assert kb.archive_task(conn, child)
        assert not workspace.exists()


def test_scratch_evidence_retained_for_dependent(isolated_home):
    with kb.connect() as conn:
        parent = kb.create_task(conn, title="producer")
        child = kb.create_task(conn, title="consumer", parents=[parent])
        workspace = kb.resolve_workspace(kb.get_task(conn, parent))
        kb.set_workspace_path(conn, parent, workspace)
        (workspace / "receipt.txt").write_text("persistent handoff")
        assert kb.complete_task(conn, parent)
        assert (workspace / "receipt.txt").read_text() == "persistent handoff"
        assert kb.get_task(conn, child).status == "ready"
        assert any(e.kind == "scratch_evidence_preserved" for e in kb.list_events(conn, parent))
        assert kb.complete_task(conn, child)
        assert not workspace.exists()  # dependent ownership ended; no indefinite scratch retention


def test_multihop_rolled_scratch_survives_intermediate_completion(isolated_home):
    with kb.connect() as conn:
        a = kb.create_task(conn, title="A")
        b = kb.create_task(conn, title="B", parents=[a])
        c = kb.create_task(conn, title="C", parents=[b])
        d = kb.create_task(conn, title="D", parents=[b])
        ancestor = kb.get_task(conn, a)
        assert ancestor is not None
        workspace = kb.resolve_workspace(ancestor)
        kb.set_workspace_path(conn, a, workspace)
        receipt = workspace / "artifact.txt"
        receipt.write_text("original")
        assert kb.complete_task(conn, a, metadata={"artifacts": [str(receipt)]})
        assert kb.complete_task(conn, b)
        assert receipt.read_text() == "original"
        assert kb.get_task(conn, c).status == "ready"
        assert kb.claim_task(conn, c) is not None
        assert kb.complete_task(conn, c)
        assert receipt.read_text() == "original"  # D still owns the rolled-up A receipt.
        assert kb.claim_task(conn, d) is not None
        assert kb.complete_task(conn, d)
        # The completed producer and closeout events still advertise this path.
        assert receipt.read_text() == "original"
        events = [e for e in kb.list_events(conn, d) if e.kind == "completed"]
        assert str(receipt) in str(events[-1].payload)


def test_archiving_final_dependent_preserves_advertised_scratch(isolated_home):
    with kb.connect() as conn:
        producer = kb.create_task(conn, title="producer")
        consumer = kb.create_task(conn, title="consumer", parents=[producer])
        task = kb.get_task(conn, producer)
        assert task is not None
        workspace = kb.resolve_workspace(task)
        kb.set_workspace_path(conn, producer, workspace)
        artifact = workspace / "report.txt"
        artifact.write_text("durable receipt")
        assert kb.complete_task(conn, producer, metadata={"artifacts": [{"path": str(artifact)}]})
        assert kb.archive_task(conn, consumer)
        assert artifact.read_text() == "durable receipt"
        assert str(artifact) in str([e.payload for e in kb.list_events(conn, producer) if e.kind == "completed"])


def test_malformed_completion_event_blocks_promotion_and_claim(isolated_home):
    with kb.connect() as conn:
        a = kb.create_task(conn, title="A")
        b = kb.create_task(conn, title="B", parents=[a])
        assert kb.complete_task(conn, a)
        conn.execute("UPDATE task_events SET payload = ? WHERE task_id = ? AND kind = 'completed'", ("{invalid", a))
        assert kb.claim_task(conn, b) is None
        assert kb.get_task(conn, b).status == "todo"
        assert "malformed" in str(kb.reconcile_task_state(conn, b)["errors"])
        assert kb.recompute_ready(conn) == 0
        conn.execute("UPDATE task_events SET payload = ? WHERE task_id = ? AND kind = 'completed'", ('{"artifacts": []}', a))
        assert kb.recompute_ready(conn) == 1


def test_corrupt_ancestor_receipt_blocks_multihop_promotion(isolated_home):
    with kb.connect() as conn:
        a = kb.create_task(conn, title="A")
        b = kb.create_task(conn, title="B", parents=[a])
        c = kb.create_task(conn, title="C", parents=[b])
        assert kb.complete_task(conn, a)
        assert kb.complete_task(conn, b)
        conn.execute("UPDATE task_events SET payload = ? WHERE task_id = ? AND kind = 'completed'", ("null", a))
        assert kb.claim_task(conn, c) is None
        assert kb.get_task(conn, c).status == "todo"
        assert "malformed" in str(kb.reconcile_task_state(conn, c)["errors"])


def test_published_url_object_does_not_read_incidental_relative_path(isolated_home):
    with kb.connect() as conn:
        a = kb.create_task(conn, title="publisher")
        b = kb.create_task(conn, title="consumer", parents=[a])
        assert kb.complete_task(conn, a, metadata={"artifacts": [
            {"url": "https://example.invalid/report", "path": "not-a-local-file"},
            {"public_url": "https://example.invalid/receipt", "file_path": "also-not-local"},
            "https://example.invalid/other",
        ]})
        assert kb.get_task(conn, b).status == "ready"
        assert kb.claim_task(conn, b) is not None


def test_codex_profile_default_bad_model_blocks_without_override(isolated_home, monkeypatch, all_assignees_spawnable):
    (isolated_home / "config.yaml").write_text("provider: openai-codex\nmodel: gpt-5.5\n")
    monkeypatch.setattr(kb, "_worker_profile_home", lambda profile: str(isolated_home))
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="bad profile default", assignee="builder")
        assert kb.get_task(conn, tid).model_override in (None, "")
        dry = kb.dispatch_once(conn, dry_run=True)
        assert dry.spawned == []
        assert "unsupported" in dry.readiness_failed[0][1]
        live = kb.dispatch_once(conn, spawn_fn=lambda *args, **kwargs: pytest.fail("must not spawn"))
        assert live.spawned == []
        assert kb.get_task(conn, tid).status == "blocked"
        assert not any(event.kind == "claimed" for event in kb.list_events(conn, tid))


def test_codex_valid_override_takes_precedence_over_bad_profile_default(isolated_home, monkeypatch, all_assignees_spawnable):
    (isolated_home / "config.yaml").write_text("provider: openai-codex\nmodel: gpt-5.5\n")
    monkeypatch.setattr(kb, "_worker_profile_home", lambda profile: str(isolated_home))
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="supported override", assignee="builder")
        conn.execute("UPDATE tasks SET model_override = ? WHERE id = ?", ("gpt-6-sol", tid))
        result = kb.dispatch_once(conn, dry_run=True)
        assert [row[0] for row in result.spawned] == [tid]
        assert result.readiness_failed == []
