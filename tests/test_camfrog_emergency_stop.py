"""Unit tests for camfrog_emergency_stop.py (Phase 22).

Verifies:
- Hierarchical scopes: GLOBAL > MACHINE > AGENT > ACCOUNT > FEATURE
- Activating a broader scope implicitly stops all narrower scopes
- Narrower stops never cover broader scopes (fail-closed direction)
- Resource-scoped stops and explicit deactivation semantics
- Audit log permanence (records survive deactivation)
- EmergencyStopGuard raises StopIteration (never SystemExit) and never
  suppresses exceptions from the guarded block
- EmergencyStopHook: check, in-flight reporting, safe worker shutdown
- Thread-safe concurrent activation and checks
- Static safety: no os.kill / subprocess / Windows registry writes in code
"""

from __future__ import annotations

import ast
import json
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _resolve_src(name: str) -> Path:
    for location in ("src/features", "src/camfrog"):
        candidate = ROOT / location / name
        if candidate.is_file():
            return candidate
    raise AssertionError(f"module file not found: {name}")
sys.path.insert(0, str(ROOT))

import camfrog_emergency_stop as es
from camfrog_emergency_stop import (
    EmergencyStopGuard,
    EmergencyStopHook,
    EmergencyStopReason,
    EmergencyStopRegistry,
    EmergencyStopScope,
)


def test_local_emergency_stop_cli_activates_lists_and_deactivates(capsys):
    scope = EmergencyStopScope.FEATURE.value
    resource = "camfrog.status-random"
    assert es.main(
        [
            "activate",
            "--scope",
            scope,
            "--reason",
            "OPERATOR_HALT",
            "--operator",
            "operator-test",
            "--resource",
            resource,
        ]
    ) == 0
    activated = json.loads(capsys.readouterr().out)
    assert activated["activated"] is True
    assert activated["stop"]["affected_resources"] == [resource]
    assert EmergencyStopRegistry().is_stopped(EmergencyStopScope.FEATURE, resource)
    assert not EmergencyStopRegistry().is_stopped(
        EmergencyStopScope.FEATURE, "camfrog.status-changer"
    )

    assert es.main(["status"]) == 0
    assert len(json.loads(capsys.readouterr().out)["active_stops"]) == 1

    assert es.main(["deactivate", "--scope", scope, "--resource", resource]) == 0
    assert json.loads(capsys.readouterr().out)["deactivated"] is True
    assert not EmergencyStopRegistry().is_stopped(EmergencyStopScope.FEATURE, resource)


@pytest.fixture(autouse=True)
def clean_registry(tmp_path, monkeypatch):
    """Isolate the persistent registry for each test."""
    monkeypatch.setenv(
        "ZCHAT_EMERGENCY_STOP_DB", str(tmp_path / "emergency-stops.sqlite3")
    )
    EmergencyStopRegistry()._reset_for_testing()
    yield
    EmergencyStopRegistry()._reset_for_testing()


def _reg() -> EmergencyStopRegistry:
    return EmergencyStopRegistry()


def _observe_and_deactivate_in_child(database_path: str, result_queue) -> None:
    os.environ["ZCHAT_EMERGENCY_STOP_DB"] = database_path
    registry = EmergencyStopRegistry()
    result_queue.put(
        (
            registry.is_stopped(EmergencyStopScope.FEATURE, "feature-1"),
            registry.deactivate(EmergencyStopScope.GLOBAL),
        )
    )


# ---------- Singleton ----------

def test_registry_is_singleton():
    assert EmergencyStopRegistry() is EmergencyStopRegistry()


# ---------- Scope hierarchy ----------

def test_global_stop_covers_every_scope():
    _reg().activate(EmergencyStopScope.GLOBAL, "OPERATOR_HALT", "op-1")
    for scope in EmergencyStopScope:
        assert _reg().is_stopped(scope) is True
        assert _reg().is_stopped(scope, "any-resource") is True


def test_narrower_stop_does_not_cover_broader_scope():
    _reg().activate(EmergencyStopScope.FEATURE, "OPERATOR_HALT", "op-1")
    assert _reg().is_stopped(EmergencyStopScope.FEATURE) is True
    assert _reg().is_stopped(EmergencyStopScope.ACCOUNT) is False
    assert _reg().is_stopped(EmergencyStopScope.AGENT) is False
    assert _reg().is_stopped(EmergencyStopScope.MACHINE) is False
    assert _reg().is_stopped(EmergencyStopScope.GLOBAL) is False


def test_machine_stop_covers_agent_account_feature_only():
    _reg().activate(EmergencyStopScope.MACHINE, "OPERATOR_HALT", "op-1")
    assert _reg().is_stopped(EmergencyStopScope.MACHINE) is True
    assert _reg().is_stopped(EmergencyStopScope.AGENT) is True
    assert _reg().is_stopped(EmergencyStopScope.ACCOUNT) is True
    assert _reg().is_stopped(EmergencyStopScope.FEATURE) is True
    assert _reg().is_stopped(EmergencyStopScope.GLOBAL) is False


def test_no_stop_means_not_stopped():
    for scope in EmergencyStopScope:
        assert _reg().is_stopped(scope) is False


# ---------- Resource-scoped stops ----------

def test_resource_scoped_stop_covers_only_listed_key():
    _reg().activate(
        EmergencyStopScope.ACCOUNT,
        "OPERATOR_HALT",
        "op-1",
        affected_resources=["acct-A"],
    )
    assert _reg().is_stopped(EmergencyStopScope.ACCOUNT, "acct-A") is True
    assert _reg().is_stopped(EmergencyStopScope.ACCOUNT, "acct-B") is False
    # A key-less check against a resource-scoped stop still matches (covers scope).
    assert _reg().is_stopped(EmergencyStopScope.ACCOUNT) is True
    # Narrower scopes inherit the stop regardless of key.
    assert _reg().is_stopped(EmergencyStopScope.FEATURE, "anything") is True


def test_scope_wide_stop_covers_any_key():
    _reg().activate(EmergencyStopScope.ACCOUNT, "OPERATOR_HALT", "op-1")
    assert _reg().is_stopped(EmergencyStopScope.ACCOUNT, "acct-A") is True
    assert _reg().is_stopped(EmergencyStopScope.ACCOUNT, "acct-B") is True


# ---------- Deactivation and audit permanence ----------

def test_deactivate_removes_stop_and_reports():
    _reg().activate(EmergencyStopScope.FEATURE, "OPERATOR_HALT", "op-1")
    assert _reg().deactivate(EmergencyStopScope.FEATURE) is True
    assert _reg().is_stopped(EmergencyStopScope.FEATURE) is False
    assert _reg().get_active_stops() == []
    # Second deactivation finds nothing.
    assert _reg().deactivate(EmergencyStopScope.FEATURE) is False


def test_deactivate_with_resource_key_is_selective():
    _reg().activate(
        EmergencyStopScope.ACCOUNT, "HALT-A", "op-1", affected_resources=["acct-A"]
    )
    _reg().activate(
        EmergencyStopScope.ACCOUNT, "HALT-B", "op-1", affected_resources=["acct-B"]
    )
    assert _reg().deactivate(EmergencyStopScope.ACCOUNT, "acct-A") is True
    assert _reg().is_stopped(EmergencyStopScope.ACCOUNT, "acct-A") is False
    assert _reg().is_stopped(EmergencyStopScope.ACCOUNT, "acct-B") is True


def test_deactivate_does_not_touch_broader_scopes():
    _reg().activate(EmergencyStopScope.GLOBAL, "OPERATOR_HALT", "op-1")
    assert _reg().deactivate(EmergencyStopScope.FEATURE) is False
    assert _reg().is_stopped(EmergencyStopScope.FEATURE) is True


def test_audit_log_survives_deactivation():
    reason = _reg().activate(EmergencyStopScope.AGENT, "OPERATOR_HALT", "op-1")
    assert _reg().deactivate(EmergencyStopScope.AGENT) is True
    log = _reg().get_audit_log()
    assert len(log) == 1
    entry = log[0]
    assert entry["stop_id"] == reason.stop_id
    assert entry["scope"] == "AGENT"
    assert entry["reason_code"] == "OPERATOR_HALT"
    assert entry["operator_id"] == "op-1"
    assert entry["active"] is False
    assert entry["deactivated_at"] is not None


def test_stop_and_deactivation_are_shared_across_processes():
    database_path = str(EmergencyStopRegistry._storage_path())
    reason = _reg().activate(EmergencyStopScope.GLOBAL, "OPERATOR_HALT", "op-1")
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    child = context.Process(
        target=_observe_and_deactivate_in_child,
        args=(database_path, result_queue),
    )
    child.start()
    child.join(timeout=15)
    if child.is_alive():
        child.terminate()
        child.join(timeout=5)
        pytest.fail("child process did not finish the shared emergency-stop operation")

    assert child.exitcode == 0
    assert result_queue.get(timeout=2) == (True, True)
    assert _reg().is_stopped(EmergencyStopScope.FEATURE, "feature-1") is False
    audit = _reg().get_audit_log()
    assert len(audit) == 1
    assert audit[0]["stop_id"] == reason.stop_id
    assert audit[0]["active"] is False


def test_stops_do_not_auto_expire():
    reason = _reg().activate(EmergencyStopScope.GLOBAL, "OPERATOR_HALT", "op-1")
    time.sleep(0.05)
    assert reason.active is True
    assert _reg().is_stopped(EmergencyStopScope.FEATURE) is True


def test_stop_ids_are_unique():
    r1 = _reg().activate(EmergencyStopScope.FEATURE, "A", "op-1")
    r2 = _reg().activate(EmergencyStopScope.FEATURE, "B", "op-1")
    assert r1.stop_id != r2.stop_id
    assert len(_reg().get_active_stops()) == 2


# ---------- Guard ----------

def test_guard_passes_when_clear():
    with EmergencyStopGuard(EmergencyStopScope.FEATURE, "feat-1"):
        pass


def test_guard_raises_stop_iteration_not_system_exit():
    _reg().activate(EmergencyStopScope.GLOBAL, "OPERATOR_HALT", "op-1")
    with pytest.raises(StopIteration):
        with EmergencyStopGuard(EmergencyStopScope.FEATURE, "feat-1"):
            pass  # pragma: no cover - must not execute
    # SystemExit must never be used for stop signalling.
    try:
        with EmergencyStopGuard(EmergencyStopScope.FEATURE):
            pass  # pragma: no cover
    except SystemExit:  # pragma: no cover
        pytest.fail("guard must not raise SystemExit")
    except StopIteration:
        pass


def test_guard_does_not_suppress_guarded_exceptions():
    with pytest.raises(ValueError, match="boom"):
        with EmergencyStopGuard(EmergencyStopScope.FEATURE):
            raise ValueError("boom")


def test_guard_message_identifies_scope_and_resource():
    _reg().activate(EmergencyStopScope.ACCOUNT, "OPERATOR_HALT", "op-1")
    with pytest.raises(StopIteration, match="ACCOUNT"):
        with EmergencyStopGuard(EmergencyStopScope.ACCOUNT, "acct-9"):
            pass  # pragma: no cover


# ---------- Hook mixin ----------

class _HookHarness(EmergencyStopHook):
    def __init__(self):
        super().__init__()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None


def test_hook_defaults_to_global_scope():
    hook = _HookHarness()
    assert hook.emergency_scope == EmergencyStopScope.GLOBAL
    assert hook.emergency_resource_key is None


def test_hook_check_raises_when_stopped():
    hook = _HookHarness()
    hook.check_emergency_stop()  # clear: no raise
    _reg().activate(EmergencyStopScope.GLOBAL, "OPERATOR_HALT", "op-1")
    with pytest.raises(StopIteration):
        hook.check_emergency_stop()


def test_hook_inflight_register_complete_report():
    hook = _HookHarness()
    assert hook.report_inflight_operations() == {}
    hook.register_inflight("op-1", "status write pid=123")
    hook.register_inflight("op-2", "room join")
    assert hook.report_inflight_operations() == {
        "op-1": "status write pid=123",
        "op-2": "room join",
    }
    hook.complete_inflight("op-1")
    assert hook.report_inflight_operations() == {"op-2": "room join"}
    hook.complete_inflight("missing-op")  # never raises


def test_hook_stop_workers_safely_joins_without_killing():
    hook = _HookHarness()
    finished: list[str] = []

    def worker():
        hook._stop_event.wait(timeout=5.0)
        finished.append("done")

    hook._worker_thread = threading.Thread(target=worker, daemon=True)
    hook._worker_thread.start()
    hook.stop_workers_safely(join_timeout=5.0)
    assert hook._stop_event.is_set()
    assert finished == ["done"]
    assert hook._worker_thread.is_alive() is False


def test_hook_stop_workers_safely_without_worker_is_noop():
    _HookHarness().stop_workers_safely(join_timeout=0.1)


def test_hook_make_guard_uses_instance_defaults():
    hook = _HookHarness()
    hook.emergency_scope = EmergencyStopScope.AGENT
    hook.emergency_resource_key = "agent-7"
    guard = hook.make_guard()
    assert guard.scope == EmergencyStopScope.AGENT
    assert guard.resource_key == "agent-7"
    assert guard.registry is hook.stop_registry


# ---------- Thread safety ----------

def test_concurrent_activate_and_check_is_consistent():
    errors: list[BaseException] = []

    def worker(i: int):
        try:
            reg = EmergencyStopRegistry()
            for _ in range(25):
                reg.activate(EmergencyStopScope.FEATURE, f"R-{i}", f"op-{i}")
                assert reg.is_stopped(EmergencyStopScope.FEATURE) is True
        except BaseException as exc:  # noqa: BLE001 - collected for assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    assert errors == []
    assert len(_reg().get_active_stops()) == 8 * 25


# ---------- Static safety: no destructive primitives in code ----------

def _code_nodes():
    source = _resolve_src("camfrog_emergency_stop.py").read_text(encoding="utf-8")
    return ast.parse(source)


def test_no_process_kill_or_subprocess_usage():
    tree = _code_nodes()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in {"subprocess", "winreg"}, (
                    f"banned import: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in {"subprocess", "winreg"}, (
                f"banned import: {node.module}"
            )
        elif isinstance(node, ast.Attribute):
            assert node.attr not in {"kill", "TerminateProcess", "SetValueEx"}, (
                f"banned call: {node.attr}"
            )
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in {"kill", "system", "popen", "Popen"}, (
                    f"banned call: {func.id}"
                )


def test_reason_record_shape():
    reason = EmergencyStopReason(
        scope=EmergencyStopScope.MACHINE,
        reason_code="OPERATOR_HALT",
        operator_id="op-9",
        affected_resources=["m-1"],
    )
    assert reason.active is True
    assert reason.deactivated_at is None
    assert isinstance(reason.stop_id, str) and reason.stop_id
    assert isinstance(reason.timestamp, float)
    d = reason.as_dict()
    assert d["scope"] == "MACHINE"
    assert d["affected_resources"] == ["m-1"]
