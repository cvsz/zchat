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
