"""Shared provider auth keeps one mutable OAuth owner across named profiles."""

from __future__ import annotations

import asyncio
import base64
import json
import multiprocessing
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import yaml


PROVIDER = "openai-codex"


def _jwt(exp: int) -> str:
    def part(payload: dict) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'exp': exp})}.sig"


def _oauth_entry(
    access: str,
    refresh: str,
    *,
    entry_id: str = "shared-codex",
    source: str = "manual:device_code",
    priority: int = 0,
) -> dict:
    return {
        "id": entry_id,
        "label": entry_id,
        "auth_type": "oauth",
        "priority": priority,
        "source": source,
        "access_token": access,
        "refresh_token": refresh,
    }


def _authorize_shared(root: Path, profiles: list[str]) -> None:
    (root / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "auth": {
                    "shared_provider_consumers": {PROVIDER: profiles},
                }
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def profile_env(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".hermes"
    profile = root / "profiles" / "tac-builder"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    _authorize_shared(root, ["tac-builder"])
    return {"root": root, "profile": profile}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _enable_shared(profile: Path) -> None:
    (profile / "config.yaml").write_text(
        yaml.safe_dump({"auth": {"shared_providers": [PROVIDER]}}),
        encoding="utf-8",
    )


def test_opted_profile_reads_and_writes_only_global_owner(profile_env):
    from hermes_cli.auth import read_credential_pool, write_credential_pool

    _enable_shared(profile_env["profile"])
    _write(
        profile_env["root"] / "auth.json",
        {"version": 1, "credential_pool": {PROVIDER: [_oauth_entry("root-a", "root-r")]}},
    )
    _write(
        profile_env["profile"] / "auth.json",
        {
            "version": 1,
            "credential_pool": {PROVIDER: [_oauth_entry("local-a", "local-r")]},
            "providers": {"other": {"token": "preserve"}},
        },
    )

    assert read_credential_pool(PROVIDER)[0]["access_token"] == "root-a"
    write_credential_pool(PROVIDER, [_oauth_entry("rotated-a", "rotated-r")])

    root = json.loads((profile_env["root"] / "auth.json").read_text())
    local = json.loads((profile_env["profile"] / "auth.json").read_text())
    assert root["credential_pool"][PROVIDER][0]["access_token"] == "rotated-a"
    assert local["credential_pool"][PROVIDER][0]["access_token"] == "local-a"
    assert local["providers"]["other"] == {"token": "preserve"}


def test_non_opted_profile_never_inherits_global_codex(profile_env):
    from hermes_cli.auth import get_provider_auth_state, read_credential_pool

    _write(
        profile_env["root"] / "auth.json",
        {
            "version": 1,
            "providers": {
                PROVIDER: {
                    "tokens": {"access_token": "root-a", "refresh_token": "root-r"}
                }
            },
            "credential_pool": {PROVIDER: [_oauth_entry("root-a", "root-r")]},
        },
    )

    assert read_credential_pool(PROVIDER) == []
    assert PROVIDER not in read_credential_pool(None)
    assert get_provider_auth_state(PROVIDER) is None


def test_client_profile_cannot_self_authorize_shared_owner(profile_env, monkeypatch):
    from agent.credential_pool import load_pool
    from hermes_cli.auth import (
        get_provider_auth_state,
        read_credential_pool,
        use_shared_provider_auth,
    )

    _write(
        profile_env["root"] / "auth.json",
        {"version": 1, "credential_pool": {PROVIDER: [_oauth_entry("root-a", "root-r")]}},
    )
    client = profile_env["root"] / "profiles" / "client-account"
    client.mkdir(parents=True)
    _enable_shared(client)
    _write(
        client / "auth.json",
        {
            "version": 1,
            "providers": {
                PROVIDER: {
                    "tokens": {
                        "access_token": "stale-local-a",
                        "refresh_token": "stale-local-r",
                    }
                }
            },
            "credential_pool": {
                PROVIDER: [_oauth_entry("stale-local-a", "stale-local-r")]
            },
        },
    )
    monkeypatch.setenv("HERMES_HOME", str(client))
    owner_before = (profile_env["root"] / "auth.json").read_bytes()
    config_before = (client / "config.yaml").read_bytes()

    assert read_credential_pool(PROVIDER) == []
    assert PROVIDER not in read_credential_pool(None)
    assert get_provider_auth_state(PROVIDER) is None
    assert load_pool(PROVIDER).entries() == []
    with pytest.raises(ValueError, match="does not authorize profile"):
        use_shared_provider_auth(PROVIDER)

    assert (profile_env["root"] / "auth.json").read_bytes() == owner_before
    assert (client / "config.yaml").read_bytes() == config_before


def test_use_shared_is_idempotent_and_scrubs_only_local_codex(profile_env):
    from hermes_cli.auth import use_shared_provider_auth

    global_payload = {
        "version": 1,
        "credential_pool": {PROVIDER: [_oauth_entry("root-a", "root-r")]},
    }
    _write(profile_env["root"] / "auth.json", global_payload)
    local_payload = {
        "version": 1,
        "active_provider": "other",
        "providers": {PROVIDER: {"tokens": {"access_token": "old"}}, "other": {"x": 1}},
        "credential_pool": {PROVIDER: [_oauth_entry("old-a", "old-r")], "other": [{"id": "o"}]},
        "suppressed_sources": {PROVIDER: ["device_code"], "other": ["env:X"]},
    }
    _write(profile_env["profile"] / "auth.json", local_payload)
    (profile_env["profile"] / "config.yaml").write_text(
        yaml.safe_dump({"model": {"provider": PROVIDER, "default": "gpt-5.4"}}),
        encoding="utf-8",
    )
    owner_before = (profile_env["root"] / "auth.json").read_bytes()

    first = use_shared_provider_auth(PROVIDER)
    second = use_shared_provider_auth(PROVIDER)

    assert first["local_shadow_removed"] is True
    assert second["local_shadow_removed"] is False
    assert (profile_env["root"] / "auth.json").read_bytes() == owner_before
    local = json.loads((profile_env["profile"] / "auth.json").read_text())
    assert local["active_provider"] == "other"
    assert local["providers"] == {"other": {"x": 1}}
    assert local["credential_pool"] == {"other": [{"id": "o"}]}
    assert local["suppressed_sources"] == {"other": ["env:X"]}
    config = yaml.safe_load((profile_env["profile"] / "config.yaml").read_text())
    assert config["auth"]["shared_providers"] == [PROVIDER]
    assert config["model"] == {"provider": PROVIDER, "default": "gpt-5.4"}


def test_use_shared_refuses_missing_global_owner_before_mutation(profile_env):
    from hermes_cli.auth import use_shared_provider_auth

    (profile_env["profile"] / "config.yaml").write_text("model: gpt-5.4\n", encoding="utf-8")
    before = (profile_env["profile"] / "config.yaml").read_bytes()

    with pytest.raises(ValueError, match="no usable"):
        use_shared_provider_auth(PROVIDER)

    assert (profile_env["profile"] / "config.yaml").read_bytes() == before


def test_shared_consumer_cannot_remove_or_logout_owner(profile_env):
    from hermes_cli.auth import logout_command
    from hermes_cli.auth_commands import auth_logout_command, auth_remove_command

    _enable_shared(profile_env["profile"])
    _write(
        profile_env["root"] / "auth.json",
        {"version": 1, "credential_pool": {PROVIDER: [_oauth_entry("root-a", "root-r")]}},
    )
    before = (profile_env["root"] / "auth.json").read_bytes()

    with pytest.raises(SystemExit, match="default Hermes root"):
        auth_remove_command(SimpleNamespace(provider=PROVIDER, target="1"))
    with pytest.raises(SystemExit, match="default Hermes root"):
        auth_logout_command(SimpleNamespace(provider=PROVIDER))
    with pytest.raises(SystemExit):
        logout_command(SimpleNamespace(provider=PROVIDER))

    assert (profile_env["root"] / "auth.json").read_bytes() == before


def test_shared_consumer_cannot_login_import_or_use_dashboard(profile_env, monkeypatch):
    import hermes_cli.auth as auth_mod
    import hermes_cli.web_server as web_server
    from fastapi import HTTPException

    _enable_shared(profile_env["profile"])
    _write(
        profile_env["root"] / "auth.json",
        {"version": 1, "credential_pool": {PROVIDER: [_oauth_entry("root-a", "root-r")]}},
    )
    before = (profile_env["root"] / "auth.json").read_bytes()
    monkeypatch.setattr(
        auth_mod,
        "_import_codex_cli_tokens",
        lambda: pytest.fail("shared consumer must not inspect Codex CLI auth"),
    )
    monkeypatch.setattr(
        auth_mod,
        "_codex_device_code_login",
        lambda: pytest.fail("shared consumer must not start device login"),
    )

    with pytest.raises(SystemExit, match="default Hermes root"):
        auth_mod._login_openai_codex(
            SimpleNamespace(),
            auth_mod.PROVIDER_REGISTRY[PROVIDER],
            force_new_login=True,
        )

    monkeypatch.setattr(web_server, "_require_token", lambda _request: None)
    with pytest.raises(HTTPException) as start_error:
        asyncio.run(web_server.start_oauth_login(PROVIDER, object()))
    assert start_error.value.status_code == 403
    with pytest.raises(HTTPException) as disconnect_error:
        asyncio.run(web_server.disconnect_oauth_provider(PROVIDER, object()))
    assert disconnect_error.value.status_code == 403
    assert (profile_env["root"] / "auth.json").read_bytes() == before


@pytest.mark.parametrize(
    ("extra", "expected_no_shared"),
    [([], False), (["--no-shared"], True)],
)
def test_use_shared_parser_routes_confirmation_flags(
    monkeypatch,
    extra,
    expected_no_shared,
):
    import hermes_cli.main as main_mod

    captured = {}

    def fake_cmd_auth(args):
        captured.update(
            action=args.auth_action,
            provider=args.provider,
            yes=args.yes,
            no_shared=args.no_shared,
        )

    monkeypatch.setattr(main_mod, "cmd_auth", fake_cmd_auth)
    monkeypatch.setattr(
        sys,
        "argv",
        ["hermes", "auth", "use-shared", PROVIDER, "--yes", *extra],
    )

    main_mod.main()

    assert captured == {
        "action": "use-shared",
        "provider": PROVIDER,
        "yes": True,
        "no_shared": expected_no_shared,
    }


@pytest.mark.parametrize(
    "source",
    ["manual:device_code", "manual:dashboard_device_code"],
)
def test_two_pool_instances_serialize_one_refresh(profile_env, monkeypatch, source):
    from agent.credential_pool import load_pool
    import hermes_cli.auth as auth_mod

    _enable_shared(profile_env["profile"])
    expired = _jwt(int(time.time()) - 60)
    fresh = _jwt(int(time.time()) + 3600)
    _write(
        profile_env["root"] / "auth.json",
        {
            "version": 1,
            "credential_pool": {
                PROVIDER: [_oauth_entry(expired, "refresh-old", source=source)]
            },
        },
    )
    first_pool = load_pool(PROVIDER)
    second_pool = load_pool(PROVIDER)
    calls = []

    def refresh(_access, refresh_token):
        calls.append(refresh_token)
        time.sleep(0.1)
        return {
            "access_token": fresh,
            "refresh_token": "refresh-new",
            "last_refresh": "2026-08-14T00:00:00Z",
        }

    monkeypatch.setattr(auth_mod, "refresh_codex_oauth_pure", refresh)
    results = []
    threads = [
        threading.Thread(target=lambda pool=pool: results.append(pool.select()))
        for pool in (first_pool, second_pool)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == ["refresh-old"]
    assert len(results) == 2
    assert all(entry is not None and entry.refresh_token == "refresh-new" for entry in results)
    stored = json.loads((profile_env["root"] / "auth.json").read_text())
    assert stored["credential_pool"][PROVIDER][0]["refresh_token"] == "refresh-new"
    assert not (profile_env["profile"] / "auth.json").exists()


def test_refresh_preserves_owner_pool_changes_made_after_load(profile_env, monkeypatch):
    from agent.credential_pool import load_pool
    import hermes_cli.auth as auth_mod

    _enable_shared(profile_env["profile"])
    expired = _jwt(int(time.time()) - 60)
    fresh = _jwt(int(time.time()) + 3600)
    auth_path = profile_env["root"] / "auth.json"
    _write(
        auth_path,
        {
            "version": 1,
            "credential_pool": {
                PROVIDER: [
                    _oauth_entry(expired, "a-old", entry_id="a", priority=0),
                    _oauth_entry(fresh, "b", entry_id="b", priority=1),
                ]
            },
        },
    )
    pool = load_pool(PROVIDER)
    _write(
        auth_path,
        {
            "version": 1,
            "credential_pool": {
                PROVIDER: [
                    _oauth_entry(expired, "a-old", entry_id="a", priority=0),
                    _oauth_entry(fresh, "c", entry_id="c", priority=1),
                ]
            },
        },
    )
    monkeypatch.setattr(
        auth_mod,
        "refresh_codex_oauth_pure",
        lambda *_args, **_kwargs: {
            "access_token": fresh,
            "refresh_token": "a-new",
            "last_refresh": "2026-08-14T00:00:00Z",
        },
    )

    refreshed = pool._refresh_entry(pool.entries()[0], force=True)

    assert refreshed is not None and refreshed.refresh_token == "a-new"
    stored = json.loads(auth_path.read_text())["credential_pool"][PROVIDER]
    assert [entry["id"] for entry in stored] == ["a", "c"]
    assert stored[0]["refresh_token"] == "a-new"


def test_refresh_does_not_resurrect_entry_removed_after_load(profile_env, monkeypatch):
    from agent.credential_pool import load_pool
    import hermes_cli.auth as auth_mod

    _enable_shared(profile_env["profile"])
    expired = _jwt(int(time.time()) - 60)
    fresh = _jwt(int(time.time()) + 3600)
    auth_path = profile_env["root"] / "auth.json"
    _write(
        auth_path,
        {
            "version": 1,
            "credential_pool": {
                PROVIDER: [
                    _oauth_entry(expired, "a-old", entry_id="a", priority=0),
                    _oauth_entry(fresh, "b", entry_id="b", priority=1),
                ]
            },
        },
    )
    pool = load_pool(PROVIDER)
    removed = pool.entries()[0]
    _write(
        auth_path,
        {
            "version": 1,
            "credential_pool": {
                PROVIDER: [_oauth_entry(fresh, "b", entry_id="b")]
            },
        },
    )
    monkeypatch.setattr(
        auth_mod,
        "refresh_codex_oauth_pure",
        lambda *_args, **_kwargs: pytest.fail("removed entry must not refresh"),
    )

    assert pool._refresh_entry(removed, force=True) is None
    stored = json.loads(auth_path.read_text())["credential_pool"][PROVIDER]
    assert [entry["id"] for entry in stored] == ["b"]


def test_stale_pool_instances_preserve_both_rotations(profile_env, monkeypatch):
    from agent.credential_pool import load_pool
    import hermes_cli.auth as auth_mod

    _enable_shared(profile_env["profile"])
    expired = _jwt(int(time.time()) - 60)
    fresh = _jwt(int(time.time()) + 3600)
    auth_path = profile_env["root"] / "auth.json"
    _write(
        auth_path,
        {
            "version": 1,
            "credential_pool": {
                PROVIDER: [
                    _oauth_entry(expired, "a-old", entry_id="a", priority=0),
                    _oauth_entry(expired, "b-old", entry_id="b", priority=1),
                ]
            },
        },
    )
    first = load_pool(PROVIDER)
    second = load_pool(PROVIDER)

    def refresh(_access, refresh_token, **_kwargs):
        return {
            "access_token": fresh,
            "refresh_token": refresh_token.replace("-old", "-new"),
            "last_refresh": "2026-08-14T00:00:00Z",
        }

    monkeypatch.setattr(auth_mod, "refresh_codex_oauth_pure", refresh)
    assert first._refresh_entry(first.entries()[0], force=True) is not None
    assert second._refresh_entry(second.entries()[1], force=True) is not None

    stored = json.loads(auth_path.read_text())["credential_pool"][PROVIDER]
    assert {entry["id"]: entry["refresh_token"] for entry in stored} == {
        "a": "a-new",
        "b": "b-new",
    }


def test_revoked_consumer_cannot_refresh_or_copy_loaded_owner_token(
    profile_env,
    monkeypatch,
):
    from agent.credential_pool import load_pool
    import hermes_cli.auth as auth_mod

    _enable_shared(profile_env["profile"])
    expired = _jwt(int(time.time()) - 60)
    _write(
        profile_env["root"] / "auth.json",
        {"version": 1, "credential_pool": {PROVIDER: [_oauth_entry(expired, "old")]}},
    )
    pool = load_pool(PROVIDER)
    _authorize_shared(profile_env["root"], [])
    monkeypatch.setattr(
        auth_mod,
        "refresh_codex_oauth_pure",
        lambda *_args, **_kwargs: pytest.fail("revoked consumer must not refresh"),
    )

    assert pool._refresh_entry(pool.entries()[0], force=True) is None
    assert not (profile_env["profile"] / "auth.json").exists()


def _resolve_singleton_in_child(profile: str, barrier, queue) -> None:
    try:
        import os
        from hermes_cli.auth import resolve_codex_runtime_credentials

        os.environ["HERMES_HOME"] = profile
        barrier.wait(timeout=10)
        resolved = resolve_codex_runtime_credentials()
        queue.put(("ok", resolved["api_key"]))
    except Exception as exc:  # pragma: no cover - returned to parent assertion
        queue.put(("error", f"{type(exc).__name__}: {exc}"))


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="cross-process advisory-lock proof requires fork",
)
def test_two_profiles_serialize_singleton_refresh_across_processes(
    profile_env,
    monkeypatch,
):
    import hermes_cli.auth as auth_mod

    root = profile_env["root"]
    profiles = [root / "profiles" / name for name in ("tac-builder", "tac-validator")]
    for profile in profiles:
        profile.mkdir(parents=True, exist_ok=True)
        _enable_shared(profile)
    _authorize_shared(root, [profile.name for profile in profiles])
    expired = _jwt(int(time.time()) - 60)
    fresh = _jwt(int(time.time()) + 3600)
    _write(
        root / "auth.json",
        {
            "version": 1,
            "providers": {
                PROVIDER: {
                    "tokens": {
                        "access_token": expired,
                        "refresh_token": "singleton-old",
                    }
                }
            },
        },
    )

    ctx = multiprocessing.get_context("fork")
    calls = ctx.Value("i", 0)

    def refresh(_access, _refresh_token, timeout_seconds=20):
        del timeout_seconds
        with calls.get_lock():
            calls.value += 1
        time.sleep(0.15)
        return {
            "access_token": fresh,
            "refresh_token": "singleton-new",
            "last_refresh": "2026-08-14T00:00:00Z",
        }

    monkeypatch.setattr(auth_mod, "refresh_codex_oauth_pure", refresh)
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    children = [
        ctx.Process(
            target=_resolve_singleton_in_child,
            args=(str(profile), barrier, queue),
        )
        for profile in profiles
    ]
    for child in children:
        child.start()
    results = [queue.get(timeout=15) for _ in children]
    for child in children:
        child.join(timeout=15)
        assert child.exitcode == 0

    assert calls.value == 1
    assert results == [("ok", fresh), ("ok", fresh)]
    stored = json.loads((root / "auth.json").read_text())
    assert stored["providers"][PROVIDER]["tokens"]["refresh_token"] == "singleton-new"
    assert all(not (profile / "auth.json").exists() for profile in profiles)


def test_reconcile_shared_auth_reports_and_repairs_metadata(profile_env, capsys):
    from hermes_cli.auth_commands import auth_reconcile_shared_command

    root = profile_env["root"]
    profile = profile_env["profile"]
    (profile / "config.yaml").write_text(
        yaml.safe_dump({"model": {"provider": PROVIDER, "default": "gpt-5.6-terra"}}),
        encoding="utf-8",
    )
    _authorize_shared(root, [])

    auth_reconcile_shared_command(SimpleNamespace(provider=PROVIDER, repair=False))
    dry = capsys.readouterr().out
    assert "dry-run" in dry
    assert "tac-builder" in dry
    assert "No changes made" in dry

    auth_reconcile_shared_command(SimpleNamespace(provider=PROVIDER, repair=True))
    repaired = capsys.readouterr().out
    assert "Repaired metadata" in repaired
    profile_cfg = yaml.safe_load((profile / "config.yaml").read_text())
    root_cfg = yaml.safe_load((root / "config.yaml").read_text())
    assert profile_cfg["auth"]["shared_providers"] == [PROVIDER]
    assert root_cfg["auth"]["shared_provider_consumers"][PROVIDER] == ["tac-builder"]
