"""Dedicated regression tests for multi-bundle evidence reconciliation.

Builds synthetic evidence bundles (validated by the evidence validator)
and asserts the reconciler accepts correct sessions and rejects or flags
incorrect ones. No network, browser, or Windows access required.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.evidence_reconciliation import reconcile_bundles
from tools.evidence_validator import validate_bundle

# ---------------------------------------------------------------------------
# Bundle fixture helpers
# ---------------------------------------------------------------------------

BASE_TIME = datetime.now(UTC)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bundle(
    root: Path,
    role: str,
    *,
    nickname: str = "Seaza",
    account_b_ref: str = "acct-b-01",
    run_id: str = "RUN-0001",
    acceptance_session_id: str = "SESSION-0001",
    command_id: str = "CMD-0001",
    source_commit_sha: str = "abc123def456",
    expected_text_sha256: str | None = None,
    observed_at: datetime | None = None,
    state: str = "WEB_PROFILE_MATCH",
    screenshot_files: list[str] | None = None,
    external_effect_verified: bool = False,
    last_observed_text_sha256: str | None = "sha-text-0001",
    tamper: str | None = None,
) -> Path:
    """Write a complete, validator-compatible evidence bundle directory.

    Returns the bundle directory path.
    """
    out = root / role
    out.mkdir(parents=True, exist_ok=True)

    observed_at = observed_at or datetime.now(UTC)

    summary = {
        "run_id": run_id,
        "acceptance_session_id": acceptance_session_id,
        "account_a_nickname": nickname,
        "command_id": command_id,
        "account_b_reference": account_b_ref,
        "source_commit_sha": source_commit_sha,
        "expected_text_sha256": (
            expected_text_sha256
            if expected_text_sha256 is not None
            else last_observed_text_sha256
        ),
        "generated_at": observed_at.isoformat(),
        "stage_chain": {
            "COMMAND_ACCEPTED": "EXTERNAL_NOT_OBSERVED_BY_THIS_TOOL",
            "WORKER_EXECUTED": "EXTERNAL_NOT_OBSERVED_BY_THIS_TOOL",
            "WEB_PROFILE_OBSERVED": state,
            "EXTERNAL_EFFECT_VERIFIED": external_effect_verified,
        },
        "sample_log": f"{run_id}.jsonl",
        "screenshot_files": screenshot_files or [],
        "last_observed_text_sha256": last_observed_text_sha256,
    }

    samples = [
        {
            "run_id": run_id,
            "sample_index": 1,
            "observed_at": observed_at.isoformat(),
            "nickname": nickname,
            "source": "authenticated_web_profile",
            "command_id": command_id,
            "account_b_reference": account_b_ref,
            "desktop_status_verified": False,
            "state": state,
            "screenshot_files": [],
            "top_states": {},
            "first_observed_text_sha256": last_observed_text_sha256,
            "last_observed_text_sha256": last_observed_text_sha256,
            "screenshot_sha256": "",
            "screenshot_file": "",
            "error_type": None,
        }
    ]

    # screenshot file (dummy bytes)
    if screenshot_files:
        for scr in screenshot_files:
            (out / scr).write_bytes(b"\x89PNG dummy screenshot")

    # jsonl sample log
    jsonl_name = summary["sample_log"]
    (out / jsonl_name).write_text(
        "".join(json.dumps(s) + "\n" for s in samples), encoding="utf-8"
    )

    # summary.json
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )

    # manifest.json (covers every non-manifest file)
    files = [
        p.name
        for p in out.iterdir()
        if p.is_file() and p.name != "manifest.json"
    ]
    manifest = {
        "run_id": run_id,
        "generated_at": observed_at.isoformat(),
        "files": [
            {
                "file": name,
                "sha256": _sha256((out / name).read_bytes()),
                "size_bytes": (out / name).stat().st_size,
            }
            for name in sorted(files)
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    # Apply tamper after writing
    if tamper == "bad_hash":
        mp = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        mp["files"][0]["sha256"] = "0" * 64
        (out / "manifest.json").write_text(
            json.dumps(mp, indent=2) + "\n", encoding="utf-8"
        )
    elif tamper == "naive_ts":
        sp = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        sp["generated_at"] = "2026-10-25T09:00:00"
        (out / "summary.json").write_text(
            json.dumps(sp, indent=2) + "\n", encoding="utf-8"
        )

    return out


def _valid_bundle_set(tmp_path: Path) -> dict[str, Path]:
    """Create a full valid bundle set (baseline, marker_a, marker_b, stop_check, windows)."""
    now = datetime.now(UTC)
    bundles = {
        "baseline": _write_bundle(
            tmp_path, "baseline",
            run_id="RUN-BASELINE-0001",
            observed_at=now,
            state="WEB_PROFILE_OBSERVED",
            last_observed_text_sha256="sha-baseline",
        ),
        "marker_a": _write_bundle(
            tmp_path, "marker_a",
            run_id="RUN-MARKER-A-0001",
            observed_at=now + timedelta(seconds=2),
            state="WEB_PROFILE_MATCH",
            last_observed_text_sha256="sha-marker-a",
        ),
        "marker_b": _write_bundle(
            tmp_path, "marker_b",
            run_id="RUN-MARKER-B-0001",
            observed_at=now + timedelta(seconds=4),
            state="WEB_PROFILE_MATCH",
            last_observed_text_sha256="sha-marker-b",
        ),
        "stop_check": _write_bundle(
            tmp_path, "stop_check",
            run_id="RUN-STOP-CHECK-0001",
            observed_at=now + timedelta(seconds=6),
            state="WEB_PROFILE_MATCH",
            last_observed_text_sha256="sha-marker-b",
        ),
        "windows": _write_bundle(
            tmp_path, "windows",
            run_id="RUN-WINDOWS-0001",
            observed_at=now + timedelta(seconds=8),
            state="WEB_PROFILE_MATCH",
            last_observed_text_sha256="sha-marker-b",
        ),
    }
    return bundles


def _rewrite_summary_field(bundle: Path, field: str, value) -> None:
    summary_path = bundle / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary[field] = value
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary_bytes = summary_path.read_bytes()
    for entry in manifest["files"]:
        if entry["file"] == "summary.json":
            entry["sha256"] = hashlib.sha256(summary_bytes).hexdigest()
            entry["size_bytes"] = len(summary_bytes)
            break
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Positive tests
# ---------------------------------------------------------------------------


def test_complete_valid_reconciliation_passes(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    verdict = reconcile_bundles(
        bundles,
        expected_nickname="Seaza",
        expected_account_b_reference="acct-b-01",
        expected_source_commit_sha="abc123def456",
    )
    assert verdict["status"] == "TRANSITION_CANDIDATE"
    assert verdict["external_effect_verified"] is False
    assert verdict["review_required"] is True
    assert verdict["windows_evidence"]["present"] is True
    assert verdict["windows_evidence"]["valid"] is True


def test_valid_baseline_bundle(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    # Remove everything except baseline -> should FAIL (missing required bundles)
    verdict = reconcile_bundles({"baseline": bundles["baseline"]})
    assert verdict["status"] == "FAIL"


def test_matching_command_ids(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    verdict = reconcile_bundles(
        bundles,
        expected_nickname="Seaza",
        expected_account_b_reference="acct-b-01",
    )
    assert verdict["status"] == "TRANSITION_CANDIDATE"


def test_correct_utc_ordering(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    verdict = reconcile_bundles(bundles)
    ordering_check = next(
        c for c in verdict["checks"] if c["check"] == "utc_ordering_chronological"
    )
    assert ordering_check["passed"] is True


def test_valid_manifest_hashes(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    # All bundles should pass individual validation
    for role, bdir in bundles.items():
        v = validate_bundle(bdir)
        assert v["status"] == "PASS", f"{role}: {v['errors']}"


def test_correct_account_identities(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    verdict = reconcile_bundles(
        bundles,
        expected_nickname="Seaza",
        expected_account_b_reference="acct-b-01",
    )
    id_check = next(
        c for c in verdict["checks"] if c["check"] == "consistent_account_a_identity"
    )
    assert id_check["passed"] is True
    ab_check = next(
        c for c in verdict["checks"] if c["check"] == "account_b_reference_consistent"
    )
    assert ab_check["passed"] is True


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------


def test_missing_windows_evidence_returns_inconclusive(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    del bundles["windows"]
    verdict = reconcile_bundles(
        bundles,
        expected_nickname="Seaza",
        expected_account_b_reference="acct-b-01",
        expected_source_commit_sha="abc123def456",
    )
    # Without Windows evidence, cannot be TRANSITION_CANDIDATE
    assert verdict["status"] != "TRANSITION_CANDIDATE"
    assert verdict["windows_evidence"]["present"] is False


def test_missing_marker_a(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    del bundles["marker_a"]
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"


def test_missing_marker_b(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    del bundles["marker_b"]
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"


def test_same_marker_repeated(tmp_path: Path):
    now = datetime.now(UTC)
    bundles = {
        "baseline": _write_bundle(tmp_path, "baseline", observed_at=now, state="WEB_PROFILE_OBSERVED", last_observed_text_sha256="sha-same"),
        "marker_a": _write_bundle(tmp_path, "marker_a", observed_at=now + timedelta(seconds=2), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-same"),
        "marker_b": _write_bundle(tmp_path, "marker_b", observed_at=now + timedelta(seconds=4), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-same"),
        "stop_check": _write_bundle(tmp_path, "stop_check", observed_at=now + timedelta(seconds=6), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-same"),
        "windows": _write_bundle(tmp_path, "windows", observed_at=now + timedelta(seconds=4), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-same"),
    }
    verdict = reconcile_bundles(bundles)
    # Same SHA for marker_a and marker_b -> no distinct transition
    assert verdict["status"] != "TRANSITION_CANDIDATE"
    trans_check = next(
        c for c in verdict["checks"] if c["check"] == "transition_evidence_validated"
    )
    assert trans_check["passed"] is False


def test_missing_stop_event(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    del bundles["stop_check"]
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"


def test_stop_check_timestamp_before_marker_b(tmp_path: Path):
    now = datetime.now(UTC)
    bundles = {
        "baseline": _write_bundle(tmp_path, "baseline", observed_at=now, state="WEB_PROFILE_OBSERVED", last_observed_text_sha256="sha-base"),
        "marker_a": _write_bundle(tmp_path, "marker_a", observed_at=now + timedelta(seconds=2), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-a"),
        "marker_b": _write_bundle(tmp_path, "marker_b", observed_at=now + timedelta(seconds=4), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
        "stop_check": _write_bundle(tmp_path, "stop_check", observed_at=now + timedelta(seconds=1), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
        "windows": _write_bundle(tmp_path, "windows", observed_at=now + timedelta(seconds=4), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
    }
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"
    order_check = next(
        c for c in verdict["checks"] if c["check"] == "utc_ordering_chronological"
    )
    assert order_check["passed"] is False


def test_invalid_stop_check_timestamp(tmp_path: Path):
    now = datetime.now(UTC)
    bundles = _valid_bundle_set(tmp_path)
    # Tamper stop_check to have naive timestamp
    _write_bundle(
        tmp_path, "stop_check",
        observed_at=now + timedelta(seconds=6),
        state="WEB_PROFILE_MATCH",
        last_observed_text_sha256="sha-marker-b",
        tamper="naive_ts",
    )
    # Re-build bundles dict pointing to the tampered stop_check
    bundles["stop_check"] = tmp_path / "stop_check"
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"


def test_unrelated_account_b(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    # Override account_b_reference in marker_b to be different
    t = BASE_TIME
    bundles["marker_b"] = _write_bundle(
        tmp_path, "marker_b",
        observed_at=t + timedelta(minutes=10),
        account_b_ref="acct-b-99",
        state="WEB_PROFILE_MATCH",
        last_observed_text_sha256="sha-marker-b",
    )
    verdict = reconcile_bundles(
        bundles,
        expected_nickname="Seaza",
        expected_account_b_reference="acct-b-01",
    )
    assert verdict["status"] == "FAIL"


def test_account_b_equals_account_a(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    now = datetime.now(UTC)
    bundles["marker_a"] = _write_bundle(
        tmp_path, "marker_a",
        observed_at=now + timedelta(seconds=2),
        account_b_ref="Seaza",  # same as Account A nickname
        state="WEB_PROFILE_MATCH",
        last_observed_text_sha256="sha-marker-a",
    )
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"


def test_missing_source_sha(tmp_path: Path):
    now = datetime.now(UTC)
    bundles = {
        "baseline": _write_bundle(tmp_path, "baseline", observed_at=now, source_commit_sha="", state="WEB_PROFILE_OBSERVED", last_observed_text_sha256="sha-base"),
        "marker_a": _write_bundle(tmp_path, "marker_a", observed_at=now + timedelta(seconds=2), source_commit_sha="", state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-a"),
        "marker_b": _write_bundle(tmp_path, "marker_b", observed_at=now + timedelta(seconds=4), source_commit_sha="", state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
        "stop_check": _write_bundle(tmp_path, "stop_check", observed_at=now + timedelta(seconds=6), source_commit_sha="", state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
        "windows": _write_bundle(tmp_path, "windows", observed_at=now + timedelta(seconds=4), source_commit_sha="", state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
    }
    verdict = reconcile_bundles(
        bundles,
        expected_source_commit_sha="abc123def456",
    )
    assert verdict["status"] == "FAIL"


def test_conflicting_source_shas(tmp_path: Path):
    now = datetime.now(UTC)
    bundles = {
        "baseline": _write_bundle(tmp_path, "baseline", observed_at=now, source_commit_sha="sha-1", state="WEB_PROFILE_OBSERVED", last_observed_text_sha256="sha-base"),
        "marker_a": _write_bundle(tmp_path, "marker_a", observed_at=now + timedelta(seconds=2), source_commit_sha="sha-2", state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-a"),
        "marker_b": _write_bundle(tmp_path, "marker_b", observed_at=now + timedelta(seconds=4), source_commit_sha="sha-1", state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
        "stop_check": _write_bundle(tmp_path, "stop_check", observed_at=now + timedelta(seconds=6), source_commit_sha="sha-1", state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
        "windows": _write_bundle(tmp_path, "windows", observed_at=now + timedelta(seconds=4), source_commit_sha="sha-1", state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
    }
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"


def test_missing_command_ids(tmp_path: Path):
    now = datetime.now(UTC)
    bundles = {
        "baseline": _write_bundle(tmp_path, "baseline", observed_at=now, command_id="", state="WEB_PROFILE_OBSERVED", last_observed_text_sha256="sha-base"),
        "marker_a": _write_bundle(tmp_path, "marker_a", observed_at=now + timedelta(seconds=2), command_id="", state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-a"),
        "marker_b": _write_bundle(tmp_path, "marker_b", observed_at=now + timedelta(seconds=4), command_id="", state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
        "stop_check": _write_bundle(tmp_path, "stop_check", observed_at=now + timedelta(seconds=6), command_id="", state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
        "windows": _write_bundle(tmp_path, "windows", observed_at=now + timedelta(seconds=4), command_id="", state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
    }
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] != "TRANSITION_CANDIDATE"


def test_forged_provider_success(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    now = datetime.now(UTC)
    bundles["marker_a"] = _write_bundle(
        tmp_path, "marker_a",
        observed_at=now + timedelta(seconds=2),
        external_effect_verified=True,  # forged!
        state="WEB_PROFILE_MATCH",
        last_observed_text_sha256="sha-marker-a",
    )
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"


def test_unexpected_extra_bundles(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    bundles["extra_unknown"] = tmp_path / "extra_unknown"
    (tmp_path / "extra_unknown").mkdir()
    verdict = reconcile_bundles(bundles)
    # Unknown roles are warned, not failed
    assert "extra_unknown" in str(verdict.get("warnings", []))


def test_malformed_input_schema(tmp_path: Path):
    # Pass a non-existent path as a bundle
    bundles = {"baseline": tmp_path / "does_not_exist"}
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"


def test_no_actual_transition_baseline_equals_marker_a(tmp_path: Path):
    now = datetime.now(UTC)
    bundles = {
        "baseline": _write_bundle(tmp_path, "baseline", observed_at=now, state="WEB_PROFILE_OBSERVED", last_observed_text_sha256="sha-same"),
        "marker_a": _write_bundle(tmp_path, "marker_a", observed_at=now + timedelta(seconds=2), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-same"),
        "marker_b": _write_bundle(tmp_path, "marker_b", observed_at=now + timedelta(seconds=4), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-marker-b"),
        "stop_check": _write_bundle(tmp_path, "stop_check", observed_at=now + timedelta(seconds=6), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-marker-b"),
        "windows": _write_bundle(tmp_path, "windows", observed_at=now + timedelta(seconds=4), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-marker-b"),
    }
    verdict = reconcile_bundles(bundles)
    # baseline == marker_a means no change after request A -> warning but not necessarily FAIL
    # The key assertion: marker_a == marker_b would fail, but here baseline == marker_a (warning)
    # marker_a != marker_b, so transition evidence is OK
    trans_check = next(
        c for c in verdict["checks"] if c["check"] == "transition_evidence_validated"
    )
    # Should still pass transition check since marker_a != marker_b
    assert trans_check["passed"] is True


def test_post_stop_change_detected(tmp_path: Path):
    now = datetime.now(UTC)
    bundles = {
        "baseline": _write_bundle(tmp_path, "baseline", observed_at=now, state="WEB_PROFILE_OBSERVED", last_observed_text_sha256="sha-base"),
        "marker_a": _write_bundle(tmp_path, "marker_a", observed_at=now + timedelta(seconds=2), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-a"),
        "marker_b": _write_bundle(tmp_path, "marker_b", observed_at=now + timedelta(seconds=4), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
        "stop_check": _write_bundle(tmp_path, "stop_check", observed_at=now + timedelta(seconds=6), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-changed-after-stop"),
        "windows": _write_bundle(tmp_path, "windows", observed_at=now + timedelta(seconds=4), state="WEB_PROFILE_MATCH", last_observed_text_sha256="sha-b"),
    }
    verdict = reconcile_bundles(bundles)
    # post_stop check should fail
    post_check = next(
        c for c in verdict["checks"] if c["check"] == "post_stop_observation_stable"
    )
    assert post_check["passed"] is False


def test_external_effect_verified_never_auto_assigned(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    verdict = reconcile_bundles(bundles)
    assert verdict["external_effect_verified"] is False
    assert verdict["review_required"] is True


# ---------------------------------------------------------------------------
# Verdict level semantics
# ---------------------------------------------------------------------------


def test_verdict_levels_are_ordered():
    from tools.evidence_reconciliation import VERDICT_LEVELS

    assert VERDICT_LEVELS["FAIL"] < VERDICT_LEVELS["INCONCLUSIVE"]
    assert VERDICT_LEVELS["INCONCLUSIVE"] < VERDICT_LEVELS["STRUCTURALLY_VALID"]
    assert VERDICT_LEVELS["STRUCTURALLY_VALID"] < VERDICT_LEVELS["CORRELATION_VALID"]
    assert VERDICT_LEVELS["CORRELATION_VALID"] < VERDICT_LEVELS["TRANSITION_CANDIDATE"]

def test_distinct_observer_run_ids_share_acceptance_session(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    verdict = reconcile_bundles(
        bundles,
        expected_nickname="Seaza",
        expected_account_b_reference="acct-b-01",
        expected_source_commit_sha="abc123def456",
    )
    assert verdict["status"] == "TRANSITION_CANDIDATE"
    run_check = next(c for c in verdict["checks"] if c["check"] == "run_id_present_per_bundle")
    session_check = next(c for c in verdict["checks"] if c["check"] == "acceptance_session_lineage_consistency")
    assert run_check["passed"] is True
    assert session_check["passed"] is True


def test_inconsistent_acceptance_session_ids_fail(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    _rewrite_summary_field(bundles["marker_b"], "acceptance_session_id", "SESSION-OTHER")
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"
    assert any("acceptance_session_id" in error for error in verdict["errors"])


def test_marker_observation_must_match_expected_hash(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    _rewrite_summary_field(bundles["marker_a"], "expected_text_sha256", "not-the-observed-hash")
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"
    assert any("does not match expected marker" in error for error in verdict["errors"])


def test_marker_observation_state_must_confirm_match(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    summary_path = bundles["marker_a"] / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["stage_chain"]["WEB_PROFILE_OBSERVED"] = "WEB_PROFILE_MISMATCH"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    manifest_path = bundles["marker_a"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = summary_path.read_bytes()
    for entry in manifest["files"]:
        if entry["file"] == "summary.json":
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["size_bytes"] = len(data)
            break
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"
    assert any("WEB_PROFILE_OBSERVED" in error for error in verdict["errors"])


def test_stop_check_without_observed_hash_fails_closed(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    _rewrite_summary_field(bundles["stop_check"], "last_observed_text_sha256", None)
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"
    assert any("stop_check: missing observed text SHA" in error for error in verdict["errors"])


def test_windows_manifest_content_is_validated(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    manifest_path = bundles["windows"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = []
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"
    assert verdict["windows_evidence"]["valid"] is False


def test_marker_command_ids_required_even_when_baseline_has_one(tmp_path: Path):
    now = datetime.now(UTC)
    bundles = {
        "baseline": _write_bundle(
            tmp_path, "baseline", command_id="CMD-BASELINE",
            observed_at=now, state="WEB_PROFILE_OBSERVED",
            last_observed_text_sha256="sha-base",
        ),
        "marker_a": _write_bundle(
            tmp_path, "marker_a", command_id="",
            observed_at=now + timedelta(seconds=2), state="WEB_PROFILE_MATCH",
            last_observed_text_sha256="sha-a",
        ),
        "marker_b": _write_bundle(
            tmp_path, "marker_b", command_id="",
            observed_at=now + timedelta(seconds=4), state="WEB_PROFILE_MATCH",
            last_observed_text_sha256="sha-b",
        ),
        "stop_check": _write_bundle(
            tmp_path, "stop_check", command_id="CMD-STOP",
            observed_at=now + timedelta(seconds=6), state="WEB_PROFILE_MATCH",
            last_observed_text_sha256="sha-b",
        ),
        "windows": _write_bundle(
            tmp_path, "windows", command_id="CMD-STOP",
            observed_at=now + timedelta(seconds=8), state="WEB_PROFILE_MATCH",
            last_observed_text_sha256="sha-b",
        ),
    }
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"
    assert any("marker bundles missing command_id" in error for error in verdict["errors"])


def test_missing_timestamp_on_any_ordered_bundle_fails(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    _rewrite_summary_field(bundles["marker_a"], "generated_at", "")
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"
    assert any("marker_a: missing or invalid generated_at" in error for error in verdict["errors"])


def test_partial_source_sha_missing_fails_when_expected_sha_is_supplied(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    _rewrite_summary_field(bundles["marker_a"], "source_commit_sha", "")
    verdict = reconcile_bundles(bundles, expected_source_commit_sha="abc123def456")
    assert verdict["status"] == "FAIL"
    assert any("source_commit_sha missing from bundles" in error for error in verdict["errors"])

def test_non_string_identity_field_fails_closed(tmp_path: Path):
    bundles = _valid_bundle_set(tmp_path)
    _rewrite_summary_field(bundles["marker_a"], "account_a_nickname", ["Seaza"])
    verdict = reconcile_bundles(bundles)
    assert verdict["status"] == "FAIL"
    assert verdict["external_effect_verified"] is False
