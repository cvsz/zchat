"""Multi-bundle evidence reconciliation for two-account acceptance runs.

Validates a complete acceptance session across multiple evidence bundles:

- Baseline bundle (pre-mutation)
- Marker A bundle (after request A)
- Marker B bundle (after request B)
- Stop-check bundle (post-F4, no further changes)
- Windows execution evidence

Produces machine-readable candidate evidence and a human-readable
reconciliation report. Never assigns ``EXTERNAL_EFFECT_VERIFIED`` -- that
decision is reserved for a human reviewer.

Verdict levels (in increasing strength):

- ``FAIL`` — structural or correlation problems; bundle set is invalid.
- ``INCONCLUSIVE`` — structurally valid but missing essential live evidence
  (e.g. Windows execution evidence, unverifiable transitions).
- ``STRUCTURALLY_VALID`` — all bundles validate and cross-bundle identity /
  ordering checks pass, but no transition evidence was demonstrated.
- ``CORRELATION_VALID`` — structural + correlation checks pass and at least
  one transition was observed, but stop-check/post-stop evidence is absent.
- ``TRANSITION_CANDIDATE`` — full end-to-end candidate: structural,
  correlation, transition, and stop-check evidence all present.

``EXTERNAL_EFFECT_VERIFIED`` is always ``False`` here; only an authorized
independent reviewer may set it.

Usage::

    from tools.evidence_reconciliation import reconcile_bundles
    verdict = reconcile_bundles({
        "baseline": Path("reports/.../baseline/"),
        "marker_a": Path("reports/.../marker_a/"),
        "marker_b": Path("reports/.../marker_b/"),
        "stop_check": Path("reports/.../stop_check/"),
        "windows": Path("reports/.../windows_execution/"),
    })
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.evidence_validator import validate_bundle as _validate_bundle

# Expected bundle roles in an acceptance session
BUNDLE_ROLES = ("baseline", "marker_a", "marker_b", "stop_check", "windows")

REQUIRED_OBSERVER_BUNDLES = ("baseline", "marker_a", "marker_b", "stop_check")

# Ordered observer roles used for transition sequencing
OBSERVER_SEQUENCE = ("baseline", "marker_a", "marker_b", "stop_check")

STAGE_NOT_OBSERVED = "EXTERNAL_NOT_OBSERVED_BY_THIS_TOOL"

# Mandatory identity fields that must be present in every observer bundle
MANDATORY_IDENTITY_FIELDS = (
    "account_a_nickname",
    "account_b_reference",
    "acceptance_session_id",
    "run_id",
)

# Verdict levels in increasing strength
VERDICT_LEVELS = {
    "FAIL": 0,
    "INCONCLUSIVE": 1,
    "STRUCTURALLY_VALID": 2,
    "CORRELATION_VALID": 3,
    "TRANSITION_CANDIDATE": 4,
}


class ReconciliationError(Exception):
    """Raised when the reconciliation bundle set is structurally invalid."""


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReconciliationError(f"cannot load {path.name}: {exc}") from exc


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


def _get_summary(bundle_dir: Path, role: str) -> dict[str, Any] | None:
    """Load summary.json from a bundle directory, returning None on failure."""
    summary_path = bundle_dir / "summary.json"
    try:
        data = _load_json(summary_path)
    except ReconciliationError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _stage_chain(summary: dict[str, Any]) -> dict[str, Any]:
    sc = summary.get("stage_chain", {})
    return sc if isinstance(sc, dict) else {}


def _observed_text_sha(summary: dict[str, Any]) -> str | None:
    """Return the most recent observed Custom Status text SHA from samples."""
    # Prefer summary-level field if present
    val = summary.get("last_observed_text_sha256")
    if isinstance(val, str) and val:
        return val
    return None


def reconcile_bundles(
    bundles: dict[str, Path],
    *,
    expected_nickname: str | None = None,
    expected_account_b_reference: str | None = None,
    expected_source_commit_sha: str | None = None,
) -> dict[str, Any]:
    """Reconcile multiple evidence bundles into a single acceptance verdict.

    Parameters
    ----------
    bundles:
        Mapping of bundle role to evidence directory. Required roles:
        ``baseline``, ``marker_a``, ``marker_b``, ``stop_check``. Optional:
        ``windows`` (Windows execution evidence).
    expected_nickname:
        Expected Account A nickname (enforced across all observer bundles).
    expected_account_b_reference:
        Expected opaque Account B reference (enforced across all observer
        bundles; must be distinct from Account A identity).
    expected_source_commit_sha:
        Expected source commit SHA (enforced across all bundles that carry
        a ``source_commit_sha`` field).

    Returns
    -------
    dict
        Structured reconciliation verdict with keys:
        ``status`` (one of FAIL/INCONCLUSIVE/STRUCTURALLY_VALID/
        CORRELATION_VALID/TRANSITION_CANDIDATE), ``errors``, ``warnings``,
        ``bundle_verdicts``, ``checks``, ``windows_evidence``,
        ``review_required`` (always True), ``external_effect_verified``
        (always False), ``verdict_level`` (numeric strength).
    """
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    bundle_verdicts: dict[str, dict[str, Any]] = {}

    # --- Validate bundle roles -----------------------------------------
    provided_roles = set(bundles.keys())
    missing_roles = set(REQUIRED_OBSERVER_BUNDLES) - provided_roles
    if missing_roles:
        errors.append(
            f"missing required bundles: {', '.join(sorted(missing_roles))}"
        )
        return _final_verdict(
            "FAIL", errors, warnings, bundle_verdicts, checks, None
        )

    unknown_roles = provided_roles - set(BUNDLE_ROLES)
    if unknown_roles:
        warnings.append(
            f"unknown bundle roles ignored: {', '.join(sorted(unknown_roles))}"
        )

    # --- Validate each observer bundle individually --------------------
    for role in REQUIRED_OBSERVER_BUNDLES:
        bdir = bundles[role]
        if not bdir.is_dir():
            errors.append(f"bundle directory not found ({role}): {bdir}")
            continue
        verdict = _validate_bundle(
            bdir,
            expected_nickname=expected_nickname,
            expected_command_id=None,
            expected_account_b_reference=expected_account_b_reference,
        )
        bundle_verdicts[role] = verdict
        if verdict["status"] == "FAIL":
            for e in verdict["errors"]:
                errors.append(f"{role}: {e}")
        for w in verdict["warnings"]:
            warnings.append(f"{role}: {w}")

    if errors:
        return _final_verdict(
            "FAIL", errors, warnings, bundle_verdicts, checks, None
        )

    # --- Load summaries for cross-bundle checks ------------------------
    summaries: dict[str, dict[str, Any]] = {}
    for role in REQUIRED_OBSERVER_BUNDLES:
        summary = _get_summary(bundles[role], role)
        if summary is None:
            errors.append(f"{role}: missing or unreadable summary.json")
            continue
        summaries[role] = summary

    if errors:
        return _final_verdict(
            "FAIL", errors, warnings, bundle_verdicts, checks, None
        )

    # --- Check 1: Mandatory identity fields present ---------------------
    missing_identity: dict[str, list[str]] = {}
    for role in REQUIRED_OBSERVER_BUNDLES:
        missing = [
            f
            for f in MANDATORY_IDENTITY_FIELDS
            if not summaries[role].get(f)
        ]
        if missing:
            missing_identity[role] = missing
    checks.append(
        {
            "check": "mandatory_identity_fields_present",
            "passed": not missing_identity,
            "details": missing_identity or "all present",
        }
    )
    if missing_identity:
        errors.append(
            f"missing mandatory identity fields: {missing_identity}"
        )
        return _final_verdict(
            "FAIL", errors, warnings, bundle_verdicts, checks, None
        )

    identity_type_errors: dict[str, list[str]] = {}
    for role in REQUIRED_OBSERVER_BUNDLES:
        invalid = [
            field
            for field in MANDATORY_IDENTITY_FIELDS
            if not isinstance(summaries[role].get(field), str)
        ]
        if invalid:
            identity_type_errors[role] = invalid
    checks.append(
        {
            "check": "identity_fields_are_strings",
            "passed": not identity_type_errors,
            "details": identity_type_errors or "all identity fields are strings",
        }
    )
    if identity_type_errors:
        errors.append(f"identity fields must be strings: {identity_type_errors}")
        return _final_verdict(
            "FAIL", errors, warnings, bundle_verdicts, checks, None
        )

    # --- Check 2: Consistent Account A identity -------------------------
    nicknames = {
        role: summaries[role]["account_a_nickname"]
        for role in REQUIRED_OBSERVER_BUNDLES
    }
    unique_nicknames = set(nicknames.values())
    checks.append(
        {
            "check": "consistent_account_a_identity",
            "passed": len(unique_nicknames) == 1,
            "details": nicknames,
        }
    )
    if len(unique_nicknames) != 1:
        errors.append(
            f"inconsistent Account A identity across bundles: {nicknames}"
        )

    # --- Check 3: Account B reference present, consistent, distinct -----
    account_b_refs = {
        role: summaries[role]["account_b_reference"]
        for role in REQUIRED_OBSERVER_BUNDLES
    }
    unique_b_refs = set(account_b_refs.values())
    checks.append(
        {
            "check": "account_b_reference_consistent",
            "passed": len(unique_b_refs) == 1,
            "details": account_b_refs,
        }
    )
    if len(unique_b_refs) != 1:
        errors.append(
            f"inconsistent Account B reference across bundles: {account_b_refs}"
        )
    # Account B reference must be distinct from Account A nickname
    for role, ref in account_b_refs.items():
        if ref in unique_nicknames:
            errors.append(
                f"Account B reference equals Account A nickname in {role}: {ref!r}"
            )
    if expected_account_b_reference is not None and expected_account_b_reference not in unique_b_refs:
        errors.append(
            f"expected Account B reference {expected_account_b_reference!r} "
            f"not found in bundles: {account_b_refs}"
        )

    # --- Check 4: Source commit SHA consistency -------------------------
    commit_shas = {
        role: summaries[role].get("source_commit_sha", "")
        for role in REQUIRED_OBSERVER_BUNDLES
    }
    invalid_sha_types = [
        role for role, value in commit_shas.items()
        if not isinstance(value, str)
    ]
    if invalid_sha_types:
        errors.append(f"source_commit_sha must be a string: {invalid_sha_types}")
        return _final_verdict(
            "FAIL", errors, warnings, bundle_verdicts, checks, None
        )
    unique_shas = {value for value in commit_shas.values() if value}
    sha_consistent = len(unique_shas) <= 1
    if expected_source_commit_sha is not None:
        missing_sha_roles = [role for role, value in commit_shas.items() if not value]
        mismatched_sha_roles = [
            role for role, value in commit_shas.items()
            if value != expected_source_commit_sha
        ]
        sha_consistent = sha_consistent and not missing_sha_roles and not mismatched_sha_roles
        if missing_sha_roles:
            errors.append(f"source_commit_sha missing from bundles: {missing_sha_roles}")
        if mismatched_sha_roles:
            errors.append(
                f"source_commit_sha does not match expected {expected_source_commit_sha!r}: "
                f"{mismatched_sha_roles}"
            )
    checks.append(
        {
            "check": "source_commit_sha_consistency",
            "passed": sha_consistent,
            "details": commit_shas,
        }
    )
    if len(unique_shas) > 1:
        errors.append(f"inconsistent source commit SHAs across bundles: {commit_shas}")

    # --- Check 5: Session lineage (run_id is unique to each observer run) --
    run_ids = {
        role: summaries[role]["run_id"]
        for role in REQUIRED_OBSERVER_BUNDLES
    }
    checks.append(
        {
            "check": "run_id_present_per_bundle",
            "passed": all(isinstance(value, str) and bool(value.strip()) for value in run_ids.values()),
            "details": run_ids,
        }
    )
    session_ids = {
        role: summaries[role]["acceptance_session_id"]
        for role in REQUIRED_OBSERVER_BUNDLES
    }
    unique_session_ids = set(session_ids.values())
    checks.append(
        {
            "check": "acceptance_session_lineage_consistency",
            "passed": len(unique_session_ids) == 1,
            "details": session_ids,
        }
    )
    if len(unique_session_ids) != 1:
        errors.append(f"inconsistent acceptance_session_id across bundles: {session_ids}")

    # --- Check 6: Request/command ID linkage ---------------------------
    command_ids = {
        role: summaries[role].get("command_id", "")
        for role in REQUIRED_OBSERVER_BUNDLES
    }
    invalid_command_types = [
        role for role, value in command_ids.items()
        if not isinstance(value, str)
    ]
    if invalid_command_types:
        errors.append(f"command_id must be a string: {invalid_command_types}")
    missing_marker_command_ids = [
        role for role in ("marker_a", "marker_b")
        if not isinstance(command_ids[role], str) or not command_ids[role].strip()
    ]
    checks.append(
        {
            "check": "request_command_id_linkage",
            "passed": not invalid_command_types and not missing_marker_command_ids,
            "details": command_ids,
        }
    )
    if missing_marker_command_ids:
        errors.append(f"marker bundles missing command_id: {missing_marker_command_ids}")

    # --- Check 7: UTC timestamps (baseline, marker_a, marker_b, stop_check)
    times: dict[str, datetime | None] = {}
    for role in OBSERVER_SEQUENCE:
        generated_at = summaries[role].get("generated_at", "")
        try:
            times[role] = _parse_utc(generated_at) if generated_at else None
        except (ValueError, TypeError):
            times[role] = None
        if times[role] is None:
            ordering_ok = False
            errors.append(
                f"{role}: missing or invalid generated_at timestamp "
                "(required for acceptance-grade reconciliation)"
            )

    stop_check_missing_or_invalid = times["stop_check"] is None

    # Chronological ordering: baseline < marker_a < marker_b < stop_check
    ordering_ok = True
    pairs = [
        ("baseline", "marker_a"),
        ("marker_a", "marker_b"),
        ("marker_b", "stop_check"),
    ]
    for earlier, later in pairs:
        t_e = times[earlier]
        t_l = times[later]
        if t_e is not None and t_l is not None and t_e >= t_l:
            ordering_ok = False
            errors.append(
                f"{earlier} generated_at is not before {later} generated_at"
            )
    checks.append(
        {
            "check": "utc_ordering_chronological",
            "passed": ordering_ok,
            "details": {k: (v.isoformat() if v else None) for k, v in times.items()},
        }
    )

    # --- Check 8: Verify requested marker values ------------------------
    transition_states: dict[str, Any] = {}
    transition_evidence_ok = True
    baseline_sha = _observed_text_sha(summaries["baseline"])
    marker_a_sha = _observed_text_sha(summaries["marker_a"])
    marker_b_sha = _observed_text_sha(summaries["marker_b"])

    for role in REQUIRED_OBSERVER_BUNDLES:
        sc = _stage_chain(summaries[role])
        transition_states[role] = sc.get("WEB_PROFILE_OBSERVED", "")
        if sc.get("EXTERNAL_EFFECT_VERIFIED") is not False:
            errors.append(
                f"{role}: EXTERNAL_EFFECT_VERIFIED must not be true "
                "(human reviewer only)"
            )

    for role, observed_sha in (("marker_a", marker_a_sha), ("marker_b", marker_b_sha)):
        summary = summaries[role]
        expected_sha = summary.get("expected_text_sha256")
        state = _stage_chain(summary).get("WEB_PROFILE_OBSERVED")
        if not isinstance(expected_sha, str) or not expected_sha:
            transition_evidence_ok = False
            errors.append(f"{role}: expected_text_sha256 is missing or invalid")
        elif observed_sha != expected_sha:
            transition_evidence_ok = False
            errors.append(f"{role}: observed text SHA does not match expected marker")
        if not (state is True or state == "WEB_PROFILE_MATCH"):
            transition_evidence_ok = False
            errors.append(f"{role}: WEB_PROFILE_OBSERVED does not confirm a verified text match")

    if marker_a_sha is None:
        transition_evidence_ok = False
        errors.append("marker_a: no observed text SHA (transition not verified)")
    if marker_b_sha is None:
        transition_evidence_ok = False
        errors.append("marker_b: no observed text SHA (transition not verified)")
    if marker_a_sha is not None and marker_b_sha is not None and marker_a_sha == marker_b_sha:
        transition_evidence_ok = False
        errors.append(
            "marker_a and marker_b observed text SHAs are identical "
            "(no distinct transition between markers)"
        )
    # Baseline vs Marker A should differ (mutation occurred)
    if baseline_sha is not None and marker_a_sha is not None and baseline_sha == marker_a_sha:
        warnings.append(
            "baseline and marker_a observed text SHAs are identical "
            "(no change detected after request A)"
        )

    checks.append(
        {
            "check": "transition_evidence_validated",
            "passed": transition_evidence_ok,
            "details": {
                "transition_states": transition_states,
                "baseline_sha": baseline_sha,
                "marker_a_sha": marker_a_sha,
                "marker_b_sha": marker_b_sha,
            },
        }
    )

    # --- Check 9: Post-stop observation period -------------------------
    # Stop-check must contain a real observation and remain equal to marker B.
    stop_check_sha = _observed_text_sha(summaries["stop_check"])
    post_stop_stable = stop_check_sha is not None and marker_b_sha is not None
    if stop_check_sha is None:
        post_stop_stable = False
        errors.append("stop_check: missing observed text SHA")
    elif marker_b_sha is None:
        post_stop_stable = False
        errors.append("marker_b: missing observed text SHA required for stop comparison")
    elif stop_check_sha != marker_b_sha:
        post_stop_stable = False
        errors.append(
            "stop_check observed text SHA differs from marker_b "
            "(post-stop state is not stable)"
        )
    checks.append(
        {
            "check": "post_stop_observation_stable",
            "passed": post_stop_stable,
            "details": {
                "marker_b_sha": marker_b_sha,
                "stop_check_sha": stop_check_sha,
            },
        }
    )

    # --- Check 10: Screenshot integrity -------------------------------
    screenshot_ok = True
    for role in REQUIRED_OBSERVER_BUNDLES:
        scr = summaries[role].get("screenshot_files", [])
        if not isinstance(scr, list):
            screenshot_ok = False
            errors.append(f"{role}: screenshot_files must be a list")
            continue
        for name in scr:
            path = bundles[role] / name
            if not path.is_file():
                screenshot_ok = False
                errors.append(f"{role}: screenshot missing: {name}")
    checks.append(
        {
            "check": "screenshot_integrity",
            "passed": screenshot_ok,
        }
    )

    # --- Check 11: No conflicting claims ------------------------------
    external_effect_claims = {}
    for role in REQUIRED_OBSERVER_BUNDLES:
        sc = _stage_chain(summaries[role])
        external_effect_claims[role] = sc.get("EXTERNAL_EFFECT_VERIFIED", False)
        if external_effect_claims[role] is not False:
            errors.append(
                f"{role}: EXTERNAL_EFFECT_VERIFIED must not be true "
                f"(human reviewer only)"
            )
    checks.append(
        {
            "check": "no_external_effect_auto_assignment",
            "passed": all(v is False for v in external_effect_claims.values()),
            "details": external_effect_claims,
        }
    )

    # --- Finding D: Validate Windows execution evidence ------------------
    windows_verdict = None
    windows_present = False
    windows_valid = False
    if "windows" in bundles:
        wdir = bundles["windows"]
        if wdir.is_dir():
            windows_present = True
            has_summary = (wdir / "summary.json").is_file()
            has_manifest = (wdir / "manifest.json").is_file()
            windows_validation: dict[str, Any] | None = None
            windows_summary = _get_summary(wdir, "windows")
            if has_summary and has_manifest:
                try:
                    windows_validation = _validate_bundle(
                        wdir,
                        expected_nickname=expected_nickname,
                        expected_command_id=None,
                        expected_account_b_reference=expected_account_b_reference,
                    )
                    windows_valid = windows_validation.get("status") == "PASS"
                except Exception as exc:
                    windows_validation = {
                        "status": "FAIL",
                        "errors": [f"validator raised {type(exc).__name__}"],
                    }
                    windows_valid = False
            if not windows_summary:
                windows_valid = False
            elif windows_summary.get("acceptance_session_id") != next(iter(unique_session_ids), None):
                windows_valid = False
                errors.append("windows: acceptance_session_id does not match observer session")
            if expected_source_commit_sha is not None and (
                not windows_summary or windows_summary.get("source_commit_sha") != expected_source_commit_sha
            ):
                windows_valid = False
                errors.append("windows: source_commit_sha does not match expected commit")
            windows_verdict = {
                "present": True,
                "path": str(wdir),
                "has_summary": has_summary,
                "has_manifest": has_manifest,
                "valid": windows_valid,
                "validation": windows_validation,
            }
            if not windows_valid:
                errors.append("windows: evidence bundle failed content/manifest validation")
        else:
            warnings.append(f"windows bundle directory not found: {wdir}")
            windows_verdict = {
                "present": False,
                "path": str(wdir),
                "has_summary": False,
                "has_manifest": False,
                "valid": False,
            }
    else:
        windows_verdict = {
            "present": False,
            "path": None,
            "has_summary": False,
            "has_manifest": False,
            "valid": False,
        }

    # --- Finding E: Determine verdict level ----------------------------
    has_errors = bool(errors)
    structural_valid = not has_errors and all(
        bundle_verdicts.get(r, {}).get("status") != "FAIL"
        for r in REQUIRED_OBSERVER_BUNDLES
    )
    correlation_valid = structural_valid and ordering_ok
    transition_candidate = (
        correlation_valid
        and transition_evidence_ok
        and windows_present
        and windows_valid
        and not stop_check_missing_or_invalid
        and post_stop_stable
    )

    if has_errors or not structural_valid:
        status = "FAIL"
        level = VERDICT_LEVELS["FAIL"]
    elif not correlation_valid:
        status = "INCONCLUSIVE"
        level = VERDICT_LEVELS["INCONCLUSIVE"]
    elif not transition_candidate:
        if transition_evidence_ok and structural_valid:
            status = "CORRELATION_VALID"
            level = VERDICT_LEVELS["CORRELATION_VALID"]
        else:
            status = "STRUCTURALLY_VALID"
            level = VERDICT_LEVELS["STRUCTURALLY_VALID"]
    else:
        status = "TRANSITION_CANDIDATE"
        level = VERDICT_LEVELS["TRANSITION_CANDIDATE"]

    # EXTERNAL_EFFECT_VERIFIED is NEVER auto-assigned:
    review_required = True

    return _final_verdict(
        status,
        errors,
        warnings,
        bundle_verdicts,
        checks,
        windows_verdict,
        level=level,
        review_required=review_required,
        external_effect_verified=False,
        source_commit_sha=expected_source_commit_sha,
        account_a_nickname=expected_nickname,
        account_b_reference=expected_account_b_reference,
    )


def _final_verdict(
    status: str,
    errors: list[str],
    warnings: list[str],
    bundle_verdicts: dict[str, dict[str, Any]],
    checks: list[dict[str, Any]],
    windows_verdict: dict[str, Any] | None,
    *,
    level: int | None = None,
    review_required: bool = True,
    external_effect_verified: bool = False,
    source_commit_sha: str | None = None,
    account_a_nickname: str | None = None,
    account_b_reference: str | None = None,
) -> dict[str, Any]:
    if level is None:
        level = VERDICT_LEVELS.get(status, 0)
    return {
        "status": status,
        "verdict_level": level,
        "errors": errors,
        "warnings": warnings,
        "bundle_verdicts": bundle_verdicts,
        "checks": checks,
        "windows_evidence": windows_verdict,
        "review_required": review_required,
        "external_effect_verified": external_effect_verified,
        "source_commit_sha": source_commit_sha,
        "account_a_nickname": account_a_nickname,
        "account_b_reference": account_b_reference,
    }


def write_reconciliation_report(
    verdict: dict[str, Any], output_path: Path
) -> None:
    """Write a human-readable reconciliation report to ``output_path``."""
    lines: list[str] = []
    lines.append("# Evidence Reconciliation Report")
    lines.append("")
    lines.append(f"- **Status:** {verdict['status']}")
    lines.append(f"- **Verdict level:** {verdict['verdict_level']}")
    lines.append(f"- **Review required:** {verdict['review_required']}")
    lines.append(f"- **EXTERNAL_EFFECT_VERIFIED:** {verdict['external_effect_verified']}")
    lines.append("")
    if verdict.get("source_commit_sha"):
        lines.append(f"- **Source commit SHA:** {verdict['source_commit_sha']}")
    if verdict.get("account_a_nickname"):
        lines.append(f"- **Account A nickname:** {verdict['account_a_nickname']}")
    if verdict.get("account_b_reference"):
        lines.append(f"- **Account B reference:** {verdict['account_b_reference']}")
    lines.append("")

    lines.append("## Bundle Verdicts")
    for role, v in verdict.get("bundle_verdicts", {}).items():
        lines.append(f"- **{role}:** {v.get('status', 'UNKNOWN')}")
        for e in v.get("errors", []):
            lines.append(f"  - Error: {e}")
    lines.append("")

    lines.append("## Cross-Bundle Checks")
    for check in verdict.get("checks", []):
        lines.append(f"- **{check['check']}:** {'PASS' if check['passed'] else 'FAIL'}")
        if "details" in check:
            lines.append(f"  - Details: {check['details']}")
    lines.append("")

    lines.append("## Windows Evidence")
    we = verdict.get("windows_evidence") or {}
    lines.append(f"- **Present:** {we.get('present', False)}")
    lines.append(f"- **Valid:** {we.get('valid', False)}")
    lines.append("")

    lines.append("## Errors")
    if verdict.get("errors"):
        for e in verdict["errors"]:
            lines.append(f"- {e}")
    else:
        lines.append("- None")
    lines.append("")

    lines.append("## Warnings")
    if verdict.get("warnings"):
        for w in verdict["warnings"]:
            lines.append(f"- {w}")
    else:
        lines.append("- None")
    lines.append("")

    lines.append("")
    lines.append("> EXTERNAL_EFFECT_VERIFIED is reserved for a human reviewer. ")
    lines.append("> This report is candidate evidence only.")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Reconcile multiple evidence bundles for two-account acceptance."
    )
    parser.add_argument(
        "--baseline", required=True, help="Baseline bundle directory"
    )
    parser.add_argument(
        "--marker-a", required=True, help="Marker A bundle directory"
    )
    parser.add_argument(
        "--marker-b", required=True, help="Marker B bundle directory"
    )
    parser.add_argument(
        "--stop-check", required=True, help="Stop-check bundle directory"
    )
    parser.add_argument(
        "--windows", default=None, help="Windows execution evidence directory"
    )
    parser.add_argument(
        "--expected-nickname", default=None,
        help="Expected Account A nickname",
    )
    parser.add_argument(
        "--expected-account-b-reference", default=None,
        help="Expected Account B reference",
    )
    parser.add_argument(
        "--expected-source-commit-sha", default=None,
        help="Expected source commit SHA",
    )
    parser.add_argument(
        "--report", default=None,
        help="Write human-readable reconciliation report to this path",
    )
    args = parser.parse_args(argv)

    bundles = {
        "baseline": Path(args.baseline),
        "marker_a": Path(args.marker_a),
        "marker_b": Path(args.marker_b),
        "stop_check": Path(args.stop_check),
    }
    if args.windows:
        bundles["windows"] = Path(args.windows)

    try:
        verdict = reconcile_bundles(
            bundles,
            expected_nickname=args.expected_nickname,
            expected_account_b_reference=args.expected_account_b_reference,
            expected_source_commit_sha=args.expected_source_commit_sha,
        )
    except ReconciliationError as exc:
        print(f"RECONCILIATION_ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(verdict, indent=2, ensure_ascii=False))
    if verdict["status"] == "TRANSITION_CANDIDATE":
        print(
            "RECONCILIATION: TRANSITION_CANDIDATE (candidate evidence — human review required)",
            file=sys.stderr,
        )
    else:
        print(f"RECONCILIATION: {verdict['status']}", file=sys.stderr)

    if args.report:
        write_reconciliation_report(verdict, Path(args.report))

    return 0 if verdict["status"] == "TRANSITION_CANDIDATE" else 1


if __name__ == "__main__":
    raise SystemExit(main())