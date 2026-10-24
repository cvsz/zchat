"""Deterministic evidence-bundle validation for two-account acceptance runs.

Validates an observer evidence directory produced by
``tools/web_profile_observer.py`` without performing any network, browser, or
Windows-side access. Intended for offline review of a completed evidence bundle:

- missing evidence files
- mismatched request IDs / identities
- stale or interrupted observations
- invalid SHA-256 hashes
- missing status transitions
- ambiguous status-field identity

Returns an exit code and a structured verdict. Never assigns
``EXTERNAL_EFFECT_VERIFIED`` -- that decision is reserved for a human reviewer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_STALE_SECONDS = 86400  # observations older than one day are treated as stale
MAX_FUTURE_SKEW_SECONDS = 300  # allow 5 minutes of clock skew into the future
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB per evidence file
MAX_JSONL_LINE_BYTES = 10 * 1024 * 1024  # 10 MB per JSONL line
MAX_SAMPLE_COUNT = 10000
MAX_MANIFEST_ENTRIES = 10000

SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

STAGE_NOT_OBSERVED = "EXTERNAL_NOT_OBSERVED_BY_THIS_TOOL"


class EvidenceValidationError(Exception):
    """Raised when an evidence bundle fails a deterministic check."""


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceValidationError(f"cannot load {path.name}: {exc}") from exc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_safe_path(base: Path, name: str) -> Path | None:
    """Return a path strictly contained under ``base``, or None if unsafe.

    Rejects absolute paths, ``..`` traversal, Windows-style separators,
    directory separators, empty names, and symlinks that escape ``base``.
    """
    if not name or not isinstance(name, str):
        return None
    # Reject directory separators and Windows-style traversal
    if "/" in name or "\\\\" in name:
        return None
    # Reject components that are not safe filenames
    if name in (".", ".."):
        return None
    if not SAFE_FILENAME_RE.match(name):
        return None
    candidate = (base / name).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        return None
    # Reject symlinks that point outside the evidence directory
    try:
        if candidate.is_symlink():
            target = os.readlink(candidate)
            resolved_target = (candidate.parent / target).resolve()
            try:
                resolved_target.relative_to(base.resolve())
            except ValueError:
                return None
    except OSError:
        return None
    return candidate


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp must be a string")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"unparseable timestamp: {value!r}")
    if dt.tzinfo is None:
        raise ValueError(f"naive timestamp (no timezone): {value!r}")
    return dt.astimezone(UTC)


def validate_bundle(
    output: Path,
    *,
    expected_nickname: str | None = None,
    expected_command_id: str | None = None,
    expected_account_b_reference: str | None = None,
) -> dict[str, Any]:
    """Validate every file in ``output`` and return a structured verdict dict."""
    errors: list[str] = []
    warnings: list[str] = []
    checked: list[str] = []

    output = Path(output)
    if not output.is_dir():
        raise EvidenceValidationError(f"output directory not found: {output}")

    summary_path = output / "summary.json"
    manifest_path = output / "manifest.json"

    # --- Required top-level files ---------------------------------------
    for required in (summary_path, manifest_path):
        if not required.is_file():
            errors.append(f"missing required evidence file: {required.name}")
    if errors:
        return _verdict("FAIL", errors, warnings, checked)

    summary = _load_json(summary_path)
    manifest = _load_json(manifest_path)
    checked.extend(["summary.json", "manifest.json"])

    # --- Manifest covers every file, hashes match -----------------------
    manifest_files = manifest.get("files", [])
    manifest_names = {entry.get("file") for entry in manifest_files}
    actual_files = {p.name for p in output.iterdir() if p.is_file()}
    # manifest.json excludes itself by design
    actual_files.discard("manifest.json")

    missing_from_manifest = sorted(actual_files - manifest_names)
    extra_in_manifest = sorted(manifest_names - actual_files)
    if missing_from_manifest:
        errors.append(
            f"files missing from manifest: {', '.join(missing_from_manifest)}"
        )
    if extra_in_manifest:
        errors.append(f"manifest lists missing files: {', '.join(extra_in_manifest)}")

    # --- Duplicate manifest entries -------------------------------------
    manifest_file_names = [entry.get("file") for entry in manifest_files]
    seen = set()
    duplicates = sorted(name for name in manifest_file_names
                        if name in seen or seen.add(name))
    if duplicates:
        errors.append(f"duplicate manifest entries: {', '.join(duplicates)}")

    # --- Manifest entry count limit -------------------------------------
    if len(manifest_files) > MAX_MANIFEST_ENTRIES:
        errors.append(
            f"manifest has too many entries: {len(manifest_files)} "
            f"(max {MAX_MANIFEST_ENTRIES})"
        )

    for entry in manifest_files:
        name = entry.get("file")
        safe_path = _resolve_safe_path(output, name)
        if safe_path is None:
            errors.append(f"manifest entry escapes evidence directory: {name!r}")
            continue
        if not safe_path.is_file():
            errors.append(f"manifest entry missing or unsafe: {name!r}")
            continue
        actual_sha = _sha256(safe_path)
        if actual_sha != entry.get("sha256"):
            errors.append(
                f"hash mismatch for {name}: expected {entry.get('sha256')}, "
                f"got {actual_sha}"
            )
        if entry.get("size_bytes") != safe_path.stat().st_size:
            errors.append(f"size mismatch for {name}")
        if safe_path.stat().st_size > MAX_FILE_SIZE_BYTES:
            errors.append(
                f"file too large: {name} ({safe_path.stat().st_size} bytes)"
            )
        checked.append(name)

    # --- Summary structure ----------------------------------------------
    run_id = summary.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        errors.append("summary.json: missing or empty run_id")
    checked.append("summary.json.run_id")

    nickname = summary.get("account_a_nickname", summary.get("nickname"))
    if expected_nickname is not None and nickname != expected_nickname:
        errors.append(
            f"summary.json: nickname mismatch: expected {expected_nickname!r}, "
            f"got {nickname!r}"
        )

    command_id = summary.get("command_id", "")
    if expected_command_id is not None and command_id != expected_command_id:
        errors.append(
            f"summary.json: command_id mismatch: expected {expected_command_id!r}, "
            f"got {command_id!r}"
        )

    account_b_ref = summary.get("account_b_reference", "")
    if (
        expected_account_b_reference is not None
        and account_b_ref != expected_account_b_reference
    ):
        errors.append(
            f"summary.json: account_b_reference mismatch: "
            f"expected {expected_account_b_reference!r}, "
            f"got {account_b_ref!r}"
        )

    # --- Stage chain guard ----------------------------------------------
    stage_chain = summary.get("stage_chain", {})
    if stage_chain.get("COMMAND_ACCEPTED") != STAGE_NOT_OBSERVED:
        warnings.append(
            "summary.json: COMMAND_ACCEPTED observed by observer tool "
            "(this stage is Windows-runtime only)"
        )
    if stage_chain.get("WORKER_EXECUTED") != STAGE_NOT_OBSERVED:
        warnings.append(
            "summary.json: WORKER_EXECUTED observed by observer tool "
            "(this stage is Windows-runtime only)"
        )
    if stage_chain.get("EXTERNAL_EFFECT_VERIFIED") is not False:
        errors.append(
            "summary.json: EXTERNAL_EFFECT_VERIFIED must be false "
            "from the observer tool"
        )
    checked.append("summary.json.stage_chain")

    # --- Sample log ------------------------------------------------------
    jsonl_name = summary.get("sample_log")
    samples: list[dict[str, Any]] = []
    if jsonl_name:
        jsonl_path = output / jsonl_name
        if not jsonl_path.is_file():
            errors.append(f"sample log missing: {jsonl_name}")
        else:
            with jsonl_path.open("r", encoding="utf-8") as fh:
                for line_no, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        sample = json.loads(line)
                    except json.JSONDecodeError as exc:
                        errors.append(f"{jsonl_name}:{line_no}: invalid JSON: {exc}")
                        continue
                    samples.append(sample)
            checked.append(jsonl_name)

    # --- Sample index, ordering, and identity consistency ----------------
    seen_indices: set[int] = set()
    prev_time: datetime | None = None
    prev_run_id: str | None = None
    prev_command_id: str | None = None
    if len(samples) > MAX_SAMPLE_COUNT:
        errors.append(
            f"sample log has too many entries: {len(samples)} "
            f"(max {MAX_SAMPLE_COUNT})"
        )
    for sample in samples:
        idx = sample.get("sample_index")
        if idx is not None:
            if not isinstance(idx, int):
                errors.append(
                    f"sample_index must be an integer, got {type(idx).__name__}"
                )
            elif idx in seen_indices:
                errors.append(f"duplicate sample_index: {idx}")
            seen_indices.add(idx)
        # Chronological ordering
        observed_at = sample.get("observed_at")
        if observed_at:
            try:
                obs_time = _parse_utc(observed_at)
            except ValueError:
                pass  # already reported above
            else:
                if prev_time is not None and obs_time < prev_time:
                    errors.append(f"sample out of chronological order: index {idx}")
                prev_time = obs_time
        # Run ID consistency across samples
        sample_run_id = sample.get("run_id")
        if sample_run_id is not None:
            if prev_run_id is not None and sample_run_id != prev_run_id:
                errors.append(
                    f"inconsistent run_id across samples: {sample_run_id!r} "
                    f"vs {prev_run_id!r}"
                )
            prev_run_id = sample_run_id
        # Command ID consistency across samples
        sample_command_id = sample.get("command_id")
        if sample_command_id is not None:
            if (
                prev_command_id is not None
                and sample_command_id != prev_command_id
            ):
                errors.append(
                    f"inconsistent command_id across samples: "
                    f"{sample_command_id!r} vs {prev_command_id!r}"
                )
            prev_command_id = sample_command_id

    # --- Transition detection --------------------------------------------
    text_hashes = [
        s.get("first_observed_text_sha256") or s.get("last_observed_text_sha256")
        for s in samples
    ]
    text_hashes = [h for h in text_hashes if h]
    if len(text_hashes) >= 2 and len(set(text_hashes)) < 2:
        warnings.append("no text transition detected across samples")
    if summary.get("transition_detected") is True and len(set(text_hashes)) < 2:
        errors.append(
            "summary.json claims transition_detected but all text hashes match"
        )

    # --- Stale observations and future skew ------------------------------
    now = datetime.now(UTC)
    for sample in samples:
        observed_at = sample.get("observed_at")
        if not observed_at:
            errors.append("sample missing observed_at timestamp")
            continue
        try:
            obs_time = _parse_utc(observed_at)
        except ValueError:
            errors.append(f"sample has unparseable observed_at: {observed_at}")
            continue
        age = (now - obs_time).total_seconds()
        if age > MAX_STALE_SECONDS:
            warnings.append(
                f"sample {sample.get('sample_index')} is {int(age)}s old (stale)"
            )
        if age < -MAX_FUTURE_SKEW_SECONDS:
            errors.append(
                f"sample {sample.get('sample_index')} timestamp is in the future "
                f"by {-age:.0f}s (exceeds {MAX_FUTURE_SKEW_SECONDS}s skew tolerance)"
            )

    # --- Request-ID linkage across summary and samples -------------------
    sample_command_ids = {s.get("command_id") for s in samples if s.get("command_id")}
    if sample_command_ids and command_id and command_id not in sample_command_ids:
        errors.append(f"command_id {command_id!r} not present in any sample")

    # --- Screenshot references -------------------------------------------
    screenshot_files = summary.get("screenshot_files", [])
    if not isinstance(screenshot_files, list):
        errors.append("summary.json: screenshot_files must be a list")
        screenshot_files = []
    for scr in screenshot_files:
        safe_path = _resolve_safe_path(output, scr)
        if safe_path is None:
            errors.append(f"screenshot path escapes evidence directory: {scr!r}")
            continue
        if not safe_path.is_file():
            errors.append(f"screenshot missing: {scr}")
    checked.append("summary.json.screenshot_files")

    # --- Account B reference present -------------------------------------
    if expected_account_b_reference is not None and not account_b_ref:
        errors.append("summary.json: account_b_reference empty but one was expected")

    status = "PASS" if not errors else "FAIL"
    return {
        "status": status,
        "output": str(output),
        "run_id": run_id,
        "errors": errors,
        "warnings": warnings,
        "checked_files": checked,
        "sample_count": len(samples),
        "manifest_file_count": len(manifest_files),
    }


def _verdict(
    status: str, errors: list[str], warnings: list[str], checked: list[str]
) -> dict[str, Any]:
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "checked_files": checked,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate a web-profile-observer evidence bundle deterministically."
    )
    parser.add_argument(
        "output", help="Evidence directory containing summary.json + manifest.json"
    )
    parser.add_argument(
        "--expected-nickname",
        default=None,
        help="Expected Account A nickname in the bundle",
    )
    parser.add_argument(
        "--expected-command-id",
        default=None,
        help="Expected Windows-side command/request ID linkage",
    )
    parser.add_argument(
        "--expected-account-b-reference",
        default=None,
        help="Expected opaque Account B reference",
    )
    args = parser.parse_args(argv)

    try:
        verdict = validate_bundle(
            Path(args.output),
            expected_nickname=args.expected_nickname,
            expected_command_id=args.expected_command_id,
            expected_account_b_reference=args.expected_account_b_reference,
        )
    except EvidenceValidationError as exc:
        print(f"EVIDENCE_VALIDATION_ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(verdict, indent=2, ensure_ascii=False))
    if verdict["status"] == "PASS":
        print("EVIDENCE_VALIDATION: PASS", file=sys.stderr)
        return 0
    print("EVIDENCE_VALIDATION: FAIL", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
