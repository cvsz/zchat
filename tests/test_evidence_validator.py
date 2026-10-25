"""Offline contract tests for the evidence bundle validator.

No network, browser, or Windows access required. Builds synthetic evidence
bundles and asserts the validator accepts valid bundles and rejects
tampered ones.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.evidence_validator import validate_bundle

BUNDLE_STATE = {
    "run_id": "RUN-0001",
    "account_a_nickname": "Seaza",
    "command_id": "CMD-0001",
    "account_b_reference": "acct-b-01",
    "stage_chain": {
        "COMMAND_ACCEPTED": "EXTERNAL_NOT_OBSERVED_BY_THIS_TOOL",
        "WORKER_EXECUTED": "EXTERNAL_NOT_OBSERVED_BY_THIS_TOOL",
        "WEB_PROFILE_OBSERVED": "WEB_PROFILE_OBSERVED",
        "EXTERNAL_EFFECT_VERIFIED": False,
    },
    "sample_log": "RUN-0001.jsonl",
    "screenshot_files": ["RUN-0001-0001.png"],
}


def _write_bundle(tmp_path: Path, *, tamper: str | None = None) -> Path:
    out = tmp_path / "evidence"
    out.mkdir()

    now = datetime.now(UTC)
    t1 = now.isoformat()
    t2 = (now + timedelta(seconds=30)).isoformat()

    # manifest covers every non-manifest file
    def _entry(name: str) -> dict[str, object]:
        data = (out / name).read_bytes() if (out / name).exists() else b""
        return {
            "file": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }

    (out / "summary.json").write_text(
        json.dumps(BUNDLE_STATE, indent=2) + "\n", encoding="utf-8"
    )

    samples = [
        {
            "run_id": "RUN-0001",
            "sample_index": 1,
            "observed_at": t1,
            "nickname": "Seaza",
            "source": "authenticated_web_profile",
            "command_id": "CMD-0001",
            "account_b_reference": "acct-b-01",
            "desktop_status_verified": False,
            "state": "UNVERIFIED",
            "screenshot_files": [],
            "top_states": {},
            "first_observed_text_sha256": "aaaa",
            "last_observed_text_sha256": "bbbb",
            "screenshot_sha256": "",
            "screenshot_file": "",
            "error_type": None,
        },
        {
            "run_id": "RUN-0001",
            "sample_index": 2,
            "observed_at": t2,
            "nickname": "Seaza",
            "source": "authenticated_web_profile",
            "command_id": "CMD-0001",
            "account_b_reference": "acct-b-01",
            "desktop_status_verified": False,
            "state": "WEB_PROFILE_MATCH",
            "screenshot_files": [],
            "top_states": {},
            "first_observed_text_sha256": "aaaa",
            "last_observed_text_sha256": "bbbb",
            "screenshot_sha256": "",
            "screenshot_file": "",
            "error_type": None,
        },
    ]
    (out / "RUN-0001.jsonl").write_text(
        "".join(json.dumps(s) + "\n" for s in samples), encoding="utf-8"
    )
    (out / "RUN-0001-0001.png").write_bytes(b"\x89PNG fake screenshot")

    # rewrite manifest after all files exist
    files = sorted(
        p.name for p in out.iterdir() if p.is_file() and p.name != "manifest.json"
    )
    manifest = {
        "run_id": "RUN-0001",
        "generated_at": now.isoformat(),
        "files": [_entry(name) for name in files],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    if tamper == "remove_summary":
        (out / "summary.json").unlink()
    elif tamper == "hash_mismatch":
        p = out / "RUN-0001.jsonl"
        p.write_text(p.read_text() + "tampered\n", encoding="utf-8")
    elif tamper == "external_effect_true":
        state = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        state["stage_chain"]["EXTERNAL_EFFECT_VERIFIED"] = True
        (out / "summary.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
    elif tamper == "command_id_mismatch":
        state = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        state["command_id"] = "CMD-9999"
        (out / "summary.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
    elif tamper == "nickname_mismatch":
        state = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        state["account_a_nickname"] = "NotSeaza"
        (out / "summary.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
    elif tamper == "no_transition":
        samples[1]["last_observed_text_sha256"] = "aaaa"
        state = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        state["transition_detected"] = True
        (out / "summary.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        (out / "RUN-0001.jsonl").write_text(
            "".join(json.dumps(s) + "\n" for s in samples), encoding="utf-8"
        )

    return out


def test_valid_bundle_passes(tmp_path):
    out = _write_bundle(tmp_path)
    verdict = validate_bundle(
        out,
        expected_nickname="Seaza",
        expected_command_id="CMD-0001",
        expected_account_b_reference="acct-b-01",
    )
    assert verdict["status"] == "PASS"
    assert verdict["errors"] == []
    assert verdict["sample_count"] == 2


def test_missing_summary_fails(tmp_path):
    out = _write_bundle(tmp_path, tamper="remove_summary")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("missing required evidence file" in e for e in verdict["errors"])


def test_hash_mismatch_fails(tmp_path):
    out = _write_bundle(tmp_path, tamper="hash_mismatch")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("hash mismatch" in e for e in verdict["errors"])


def test_external_effect_true_fails(tmp_path):
    out = _write_bundle(tmp_path, tamper="external_effect_true")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("EXTERNAL_EFFECT_VERIFIED must be false" in e for e in verdict["errors"])


def test_command_id_mismatch_fails(tmp_path):
    out = _write_bundle(tmp_path, tamper="command_id_mismatch")
    verdict = validate_bundle(out, expected_command_id="CMD-0001")
    assert verdict["status"] == "FAIL"


def test_nickname_mismatch_fails(tmp_path):
    out = _write_bundle(tmp_path, tamper="nickname_mismatch")
    verdict = validate_bundle(out, expected_nickname="Seaza")
    assert verdict["status"] == "FAIL"


def test_no_transition_warns(tmp_path):
    out = _write_bundle(tmp_path, tamper="no_transition")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any(
        "transition_detected but all text hashes match" in e for e in verdict["errors"]
    )


# ---------------------------------------------------------------------------
# Adversarial tests (PHASE 5 regression audit)
# ---------------------------------------------------------------------------


def _tamper_manifest_entry(out: Path, file_name: str, tamper: str) -> None:
    """Modify a single manifest entry and rewrite manifest.json."""
    mp = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for entry in mp["files"]:
        if entry["file"] == file_name:
            if tamper == "traversal":
                entry["file"] = "../outside.txt"
            elif tamper == "absolute":
                entry["file"] = "/etc/passwd"
            elif tamper == "windows_sep":
                entry["file"] = "folder\\\\file.txt"
            elif tamper == "duplicate":
                # add a second entry with the same name
                mp["files"].append(dict(entry))
            elif tamper == "bad_hash":
                entry["sha256"] = "0" * 64
            elif tamper == "corrupted_hash":
                entry["sha256"] = "not-a-hex-hash"
            break
    (out / "manifest.json").write_text(
        json.dumps(mp, indent=2) + "\n", encoding="utf-8"
    )


def test_path_traversal_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    _tamper_manifest_entry(out, "RUN-0001.jsonl", "traversal")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("escapes evidence directory" in e for e in verdict["errors"])


def test_absolute_path_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    _tamper_manifest_entry(out, "RUN-0001.jsonl", "absolute")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("escapes evidence directory" in e for e in verdict["errors"])


def test_windows_separator_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    _tamper_manifest_entry(out, "RUN-0001.jsonl", "windows_sep")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("escapes evidence directory" in e for e in verdict["errors"])


def test_duplicate_manifest_entries_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    _tamper_manifest_entry(out, "RUN-0001.jsonl", "duplicate")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("duplicate" in e.lower() for e in verdict["errors"])


def test_symlink_to_outside_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    # Create a symlink inside the evidence dir pointing outside
    external_target = tmp_path / "external_secret.txt"
    external_target.write_bytes(b"secret data")
    symlink_name = "evidence_link.txt"
    symlink_path = out / symlink_name
    try:
        symlink_path.symlink_to(external_target)
    except OSError:
        pytest.skip("symlinks not supported on this platform")
    # Add symlink to manifest
    mp = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    mp["files"].append(
        {
            "file": symlink_name,
            "sha256": hashlib.sha256(external_target.read_bytes()).hexdigest(),
            "size_bytes": external_target.stat().st_size,
        }
    )
    (out / "manifest.json").write_text(
        json.dumps(mp, indent=2) + "\n", encoding="utf-8"
    )
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any(
        "escapes evidence directory" in e or "symlink" in e.lower()
        for e in verdict["errors"]
    )


def test_oversized_file_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    # Create a file larger than MAX_FILE_SIZE_BYTES (50 MB)
    big_file = out / "big.bin"
    big_file.write_bytes(b"x" * (51 * 1024 * 1024))
    # Add to manifest
    mp = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    mp["files"].append(
        {
            "file": "big.bin",
            "sha256": hashlib.sha256(b"x" * (51 * 1024 * 1024)).hexdigest(),
            "size_bytes": 51 * 1024 * 1024,
        }
    )
    (out / "manifest.json").write_text(
        json.dumps(mp, indent=2) + "\n", encoding="utf-8"
    )
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("too large" in e for e in verdict["errors"])


def test_oversized_jsonl_line_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    # Create a single JSONL line longer than MAX_JSONL_LINE_BYTES (10 MB)
    long_line = json.dumps({"data": "x" * (10 * 1024 * 1024 + 1)}) + "\n"
    (out / "RUN-0001.jsonl").write_text(long_line, encoding="utf-8")
    # Fix manifest hash/size
    mp = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    data = long_line.encode("utf-8")
    for entry in mp["files"]:
        if entry["file"] == "RUN-0001.jsonl":
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["size_bytes"] = len(data)
            break
    (out / "manifest.json").write_text(
        json.dumps(mp, indent=2) + "\n", encoding="utf-8"
    )
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("line" in e.lower() or "too large" in e for e in verdict["errors"])


def test_too_many_samples_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    now = datetime.now(UTC)
    # Create more than MAX_SAMPLE_COUNT (10000) samples
    samples = []
    for i in range(10001):
        samples.append(
            {
                "run_id": "RUN-0001",
                "sample_index": i + 1,
                "observed_at": (now + timedelta(seconds=i)).isoformat(),
                "nickname": "Seaza",
                "source": "authenticated_web_profile",
                "command_id": "CMD-0001",
                "account_b_reference": "acct-b-01",
                "desktop_status_verified": False,
                "state": "UNVERIFIED",
                "screenshot_files": [],
                "top_states": {},
                "first_observed_text_sha256": "aaaa",
                "last_observed_text_sha256": "bbbb",
                "screenshot_sha256": "",
                "screenshot_file": "",
                "error_type": None,
            }
        )
    (out / "RUN-0001.jsonl").write_text(
        "".join(json.dumps(s) + "\n" for s in samples), encoding="utf-8"
    )
    # Fix manifest
    mp = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    data = (out / "RUN-0001.jsonl").read_bytes()
    for entry in mp["files"]:
        if entry["file"] == "RUN-0001.jsonl":
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["size_bytes"] = len(data)
            break
    (out / "manifest.json").write_text(
        json.dumps(mp, indent=2) + "\n", encoding="utf-8"
    )
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("sample" in e.lower() and ("too many" in e.lower() or "count" in e.lower()) for e in verdict["errors"])


def test_naive_timestamp_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    # Rewrite JSONL with naive timestamp
    samples = [
        {
            "run_id": "RUN-0001",
            "sample_index": 1,
            "observed_at": "2026-10-25T09:00:00",  # naive, no timezone
            "nickname": "Seaza",
            "source": "authenticated_web_profile",
            "command_id": "CMD-0001",
            "account_b_reference": "acct-b-01",
            "desktop_status_verified": False,
            "state": "UNVERIFIED",
            "screenshot_files": [],
            "top_states": {},
            "first_observed_text_sha256": "aaaa",
            "last_observed_text_sha256": "bbbb",
            "screenshot_sha256": "",
            "screenshot_file": "",
            "error_type": None,
        }
    ]
    (out / "RUN-0001.jsonl").write_text(
        "".join(json.dumps(s) + "\n" for s in samples), encoding="utf-8"
    )
    # Fix manifest
    mp = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    data = (out / "RUN-0001.jsonl").read_bytes()
    for entry in mp["files"]:
        if entry["file"] == "RUN-0001.jsonl":
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["size_bytes"] = len(data)
            break
    (out / "manifest.json").write_text(
        json.dumps(mp, indent=2) + "\n", encoding="utf-8"
    )
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("naive" in e.lower() for e in verdict["errors"])


def test_future_timestamp_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    # Timestamp 10 minutes in the future (exceeds 5-minute skew tolerance)
    future = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    samples = [
        {
            "run_id": "RUN-0001",
            "sample_index": 1,
            "observed_at": future,
            "nickname": "Seaza",
            "source": "authenticated_web_profile",
            "command_id": "CMD-0001",
            "account_b_reference": "acct-b-01",
            "desktop_status_verified": False,
            "state": "UNVERIFIED",
            "screenshot_files": [],
            "top_states": {},
            "first_observed_text_sha256": "aaaa",
            "last_observed_text_sha256": "bbbb",
            "screenshot_sha256": "",
            "screenshot_file": "",
            "error_type": None,
        }
    ]
    (out / "RUN-0001.jsonl").write_text(
        "".join(json.dumps(s) + "\n" for s in samples), encoding="utf-8"
    )
    # Fix manifest
    mp = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    data = (out / "RUN-0001.jsonl").read_bytes()
    for entry in mp["files"]:
        if entry["file"] == "RUN-0001.jsonl":
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["size_bytes"] = len(data)
            break
    (out / "manifest.json").write_text(
        json.dumps(mp, indent=2) + "\n", encoding="utf-8"
    )
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("future" in e.lower() for e in verdict["errors"])


def test_incorrect_time_ordering_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    # Samples out of chronological order (sample 2 before sample 1)
    now = datetime.now(UTC)
    samples = [
        {
            "run_id": "RUN-0001",
            "sample_index": 1,
            "observed_at": (now + timedelta(seconds=30)).isoformat(),
            "nickname": "Seaza",
            "source": "authenticated_web_profile",
            "command_id": "CMD-0001",
            "account_b_reference": "acct-b-01",
            "desktop_status_verified": False,
            "state": "UNVERIFIED",
            "screenshot_files": [],
            "top_states": {},
            "first_observed_text_sha256": "aaaa",
            "last_observed_text_sha256": "bbbb",
            "screenshot_sha256": "",
            "screenshot_file": "",
            "error_type": None,
        },
        {
            "run_id": "RUN-0001",
            "sample_index": 2,
            "observed_at": now.isoformat(),  # earlier than sample 1
            "nickname": "Seaza",
            "source": "authenticated_web_profile",
            "command_id": "CMD-0001",
            "account_b_reference": "acct-b-01",
            "desktop_status_verified": False,
            "state": "WEB_PROFILE_MATCH",
            "screenshot_files": [],
            "top_states": {},
            "first_observed_text_sha256": "aaaa",
            "last_observed_text_sha256": "bbbb",
            "screenshot_sha256": "",
            "screenshot_file": "",
            "error_type": None,
        },
    ]
    (out / "RUN-0001.jsonl").write_text(
        "".join(json.dumps(s) + "\n" for s in samples), encoding="utf-8"
    )
    # Fix manifest
    mp = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    data = (out / "RUN-0001.jsonl").read_bytes()
    for entry in mp["files"]:
        if entry["file"] == "RUN-0001.jsonl":
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["size_bytes"] = len(data)
            break
    (out / "manifest.json").write_text(
        json.dumps(mp, indent=2) + "\n", encoding="utf-8"
    )
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("chronological" in e.lower() or "order" in e.lower() for e in verdict["errors"])


def test_duplicate_sample_indices_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    # Two samples with the same sample_index
    now = datetime.now(UTC)
    samples = [
        {
            "run_id": "RUN-0001",
            "sample_index": 1,
            "observed_at": now.isoformat(),
            "nickname": "Seaza",
            "source": "authenticated_web_profile",
            "command_id": "CMD-0001",
            "account_b_reference": "acct-b-01",
            "desktop_status_verified": False,
            "state": "UNVERIFIED",
            "screenshot_files": [],
            "top_states": {},
            "first_observed_text_sha256": "aaaa",
            "last_observed_text_sha256": "bbbb",
            "screenshot_sha256": "",
            "screenshot_file": "",
            "error_type": None,
        },
        {
            "run_id": "RUN-0001",
            "sample_index": 1,  # duplicate
            "observed_at": (now + timedelta(seconds=30)).isoformat(),
            "nickname": "Seaza",
            "source": "authenticated_web_profile",
            "command_id": "CMD-0001",
            "account_b_reference": "acct-b-01",
            "desktop_status_verified": False,
            "state": "WEB_PROFILE_MATCH",
            "screenshot_files": [],
            "top_states": {},
            "first_observed_text_sha256": "aaaa",
            "last_observed_text_sha256": "bbbb",
            "screenshot_sha256": "",
            "screenshot_file": "",
            "error_type": None,
        },
    ]
    (out / "RUN-0001.jsonl").write_text(
        "".join(json.dumps(s) + "\n" for s in samples), encoding="utf-8"
    )
    # Fix manifest
    mp = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    data = (out / "RUN-0001.jsonl").read_bytes()
    for entry in mp["files"]:
        if entry["file"] == "RUN-0001.jsonl":
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["size_bytes"] = len(data)
            break
    (out / "manifest.json").write_text(
        json.dumps(mp, indent=2) + "\n", encoding="utf-8"
    )
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("duplicate" in e.lower() for e in verdict["errors"])


def test_wrong_account_reference_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    verdict = validate_bundle(
        out,
        expected_nickname="Seaza",
        expected_command_id="CMD-0001",
        expected_account_b_reference="acct-b-99",  # wrong
    )
    assert verdict["status"] == "FAIL"
    assert any("account_b_reference" in e.lower() for e in verdict["errors"])


def test_malformed_json_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    # Corrupt summary.json
    (out / "summary.json").write_text("{not valid json", encoding="utf-8")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("cannot load" in e.lower() or "json" in e.lower() for e in verdict["errors"])


def test_type_confusion_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    # run_id should be string, not int
    state = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    state["run_id"] = 12345
    (out / "summary.json").write_text(
        json.dumps(state, indent=2) + "\n", encoding="utf-8"
    )
    # Fix manifest hash for summary.json
    mp = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    data = (out / "summary.json").read_bytes()
    for entry in mp["files"]:
        if entry["file"] == "summary.json":
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["size_bytes"] = len(data)
            break
    (out / "manifest.json").write_text(
        json.dumps(mp, indent=2) + "\n", encoding="utf-8"
    )
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"


def test_corrupted_hash_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    _tamper_manifest_entry(out, "RUN-0001.jsonl", "corrupted_hash")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("hash" in e.lower() for e in verdict["errors"])


def test_valid_bundle_still_passes_after_hardening(tmp_path):
    """Backward compatibility: valid bundles must still pass."""
    out = _write_bundle(tmp_path)
    verdict = validate_bundle(
        out,
        expected_nickname="Seaza",
        expected_command_id="CMD-0001",
        expected_account_b_reference="acct-b-01",
    )
    assert verdict["status"] == "PASS"
    assert verdict["errors"] == []


def test_sample_log_path_traversal_is_rejected(tmp_path):
    out = _write_bundle(tmp_path)
    outside = tmp_path / "outside.jsonl"
    outside.write_text(
        (out / "RUN-0001.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    summary_path = out / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["sample_log"] = "../outside.jsonl"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    # Keep the manifest internally consistent so the assertion targets the
    # path-containment guard rather than an unrelated hash mismatch.
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary_bytes = summary_path.read_bytes()
    for entry in manifest["files"]:
        if entry["file"] == "summary.json":
            entry["sha256"] = hashlib.sha256(summary_bytes).hexdigest()
            entry["size_bytes"] = len(summary_bytes)
            break
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any(
        "sample_log" in error and "safe filename" in error
        for error in verdict["errors"]
    )
