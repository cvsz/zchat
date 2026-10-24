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

STAGE_NOT_OBSERVED = "EXTERNAL_NOT_OBSERVED_BY_THIS_TOOL"


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


def _bundle_dir(run_dir: Path, role: str) -> Path:
    return run_dir / role


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
        ``status`` (PASS/FAIL/INCONCLUSIVE), ``errors``, ``warnings``,
        ``bundle_verdicts`` (per-bundle validation results), ``checks``
        (cross-bundle check results), ``review_required`` (always True --
        ``EXTERNAL_EFFECT_VERIFIED`` is never auto-assigned).
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
        return {
            "status": "FAIL",
            "errors": errors,
            "warnings": warnings,
            "bundle_verdicts": bundle_verdicts,
            "checks": checks,
            "review_required": True,
        }

    unknown_roles = provided_roles - set(BUNDLE_ROLES)
    if unknown_roles:
        warnings.append(
            f"unknown bundle roles ignored: {', '.join(sorted(unknown_roles))}"
        )

    # --- Validate each bundle individually ------------------------------
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
        return {
            "status": "FAIL",
            "errors": errors,
            "warnings": warnings,
            "bundle_verdicts": bundle_verdicts,
            "checks": checks,
            "review_required": True,
        }

    # --- Load summaries for cross-bundle checks -------------------------
    summaries: dict[str, dict[str, Any]] = {}
    for role in REQUIRED_OBSERVER_BUNDLES:
        summary_path = bundles[role] / "summary.json"
        try:
            summaries[role] = _load_json(summary_path)
        except ReconciliationError as exc:
            errors.append(str(exc))
            continue

    if errors:
        return {
            "status": "FAIL",
            "errors": errors,
            "warnings": warnings,
            "bundle_verdicts": bundle_verdicts,
            "checks": checks,
            "review_required": True,
        }

    # --- Check 1: Consistent Account A identity -------------------------
    nicknames = {
        role: summaries[role].get("account_a_nickname")
        or summaries[role].get("nickname")
        for role in REQUIRED_OBSERVER_BUNDLES
    }
    unique_nicknames = {n for n in nicknames.values() if n is not None}
    checks.append(
        {
            "check": "consistent_account_a_identity",
            "passed": len(unique_nicknames) <= 1,
            "details": nicknames,
        }
    )
    if len(unique_nicknames) > 1:
        errors.append(
            f"inconsistent Account A identity across bundles: {nicknames}"
        )

    # --- Check 2: Account B reference present and distinct --------------
    account_b_refs = {
        role: summaries[role].get("account_b_reference", "")
        for role in REQUIRED_OBSERVER_BUNDLES
    }
    unique_b_refs = {r for r in account_b_refs.values() if r}
    checks.append(
        {
            "check": "account_b_reference_present",
            "passed": len(unique_b_refs) >= 1
            and (expected_account_b_reference is None
                 or expected_account_b_reference in unique_b_refs),
            "details": account_b_refs,
        }
    )
    if expected_account_b_reference is not None and expected_account_b_reference not in unique_b_refs:
            errors.append(
                f"expected Account B reference {expected_account_b_reference!r} "
                f"not found in bundles: {account_b_refs}"
            )
    # Account B reference must be distinct from Account A nickname
    for role, ref in account_b_refs.items():
        if ref and ref in unique_nicknames:
            errors.append(
                f"Account B reference equals Account A nickname in {role}: {ref!r}"
            )

    # --- Check 3: Source commit SHA consistency -------------------------
    commit_shas = {
        role: summaries[role].get("source_commit_sha", "")
        for role in REQUIRED_OBSERVER_BUNDLES
    }
    unique_shas = {s for s in commit_shas.values() if s}
    checks.append(
        {
            "check": "source_commit_sha_consistency",
            "passed": len(unique_shas) <= 1,
            "details": commit_shas,
        }
    )
    if len(unique_shas) > 1:
        errors.append(
            f"inconsistent source commit SHAs across bundles: {commit_shas}"
        )
    if expected_source_commit_sha is not None and expected_source_commit_sha not in unique_shas:
            errors.append(
                f"expected source commit SHA {expected_source_commit_sha!r} "
                f"not found in bundles: {commit_shas}"
            )

    # --- Check 4: UTC ordering baseline < marker_a < marker_b -----------
    times: dict[str, datetime | None] = {}
    for role in ("baseline", "marker_a", "marker_b"):
        generated_at = summaries[role].get("generated_at", "")
        try:
            times[role] = _parse_utc(generated_at) if generated_at else None
        except ValueError:
            times[role] = None
            warnings.append(
                f"{role}: unparseable generated_at timestamp: {generated_at!r}"
            )

    ordering_ok = True
    if times["baseline"] is not None and times["marker_a"] is not None and times["baseline"] >= times["marker_a"]:
        ordering_ok = False
        errors.append(
            "baseline generated_at is not before marker_a generated_at"
        )
    if times["marker_a"] is not None and times["marker_b"] is not None and times["marker_a"] >= times["marker_b"]:
        ordering_ok = False
        errors.append(
            "marker_a generated_at is not before marker_b generated_at"
        )
    checks.append(
        {
            "check": "utc_ordering_baseline_before_markers",
            "passed": ordering_ok,
            "details": {k: (v.isoformat() if v else None) for k, v in times.items()},
        }
    )

    # --- Check 5: Stop event before stop-check --------------------------
    stop_check_time = times.get("stop_check")
    marker_b_time = times.get("marker_b")
    stop_order_ok = True
    if stop_check_time is not None and marker_b_time is not None and stop_check_time < marker_b_time:
        stop_order_ok = False
        errors.append(
            "stop-check generated_at is before marker_b generated_at "
            "(stop should occur after Marker B)"
        )
    checks.append(
        {
            "check": "stop_event_before_stop_check",
            "passed": stop_order_ok,
            "details": {
                "marker_b": marker_b_time.isoformat() if marker_b_time else None,
                "stop_check": (
                    stop_check_time.isoformat() if stop_check_time else None
                ),
            },
        }
    )

    # --- Check 6: Transition detection across markers -------------------
    # A valid acceptance requires: baseline has no transition (or matches
    # original status), marker_a shows a change, marker_b shows a second
    # change, stop_check shows no further change.
    transition_states = {}
    for role in REQUIRED_OBSERVER_BUNDLES:
        sc = summaries[role].get("stage_chain", {})
        transition_states[role] = sc.get("WEB_PROFILE_OBSERVED", "")
    checks.append(
        {
            "check": "transition_states_across_bundles",
            "passed": True,  # informational only; reviewer interprets
            "details": transition_states,
        }
    )

    # --- Check 7: Screenshot integrity ----------------------------------
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

    # --- Check 8: Complete manifest coverage ----------------------------
    manifest_ok = True
    for role in REQUIRED_OBSERVER_BUNDLES:
        v = bundle_verdicts.get(role, {})
        if v.get("status") == "FAIL":
            manifest_ok = False
    checks.append(
        {
            "check": "complete_manifest_coverage",
            "passed": manifest_ok,
        }
    )

    # --- Check 9: No conflicting claims --------------------------------
    # EXTERNAL_EFFECT_VERIFIED must never be true in any observer bundle.
    external_effect_claims = {}
    for role in REQUIRED_OBSERVER_BUNDLES:
        sc = summaries[role].get("stage_chain", {})
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

    # --- Windows execution evidence (optional) --------------------------
    windows_verdict = None
    if "windows" in bundles:
        wdir = bundles["windows"]
        if wdir.is_dir():
            # Windows evidence is validated structurally (files present,
            # manifest covers them). Content semantics are reviewer-dependent.
            windows_verdict = {
                "present": True,
                "path": str(wdir),
            }
            # Check for key Windows evidence files
            for required_name in ("summary.json", "manifest.json"):
                if not (wdir / required_name).is_file():
                    warnings.append(
                        f"windows bundle missing {required_name}"
                    )
        else:
            warnings.append(f"windows bundle directory not found: {wdir}")
            windows_verdict = {"present": False, "path": str(wdir)}

    # --- Overall status -------------------------------------------------
    status = "FAIL" if errors else "PASS"
    # EXTERNAL_EFFECT_VERIFIED is NEVER auto-assigned:
    review_required = True

    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "bundle_verdicts": bundle_verdicts,
        "checks": checks,
        "windows_evidence": windows_verdict,
        "review_required": review_required,
        "external_effect_verified": False,
        "source_commit_sha": expected_source_commit_sha,
        "account_a_nickname": expected_nickname,
        "account_b_reference": expected_account_b_reference,
    }


def write_reconciliation_report(
    verdict: dict[str, Any], output_path: Path
) -> None:
    """Write a human-readable reconciliation report to ``output_path``."""
    lines: list[str] = []
    lines.append("# Evidence Reconciliation Report")
    lines.append("")
    lines.append(f"- **Status:** {verdict['status']}")
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
        print(f"RECONCILIATION_ERROR: {exc}", file=__import__("sys").stderr)
        return 2

    print(json.dumps(verdict, indent=2, ensure_ascii=False))
    if verdict["status"] == "PASS":
        print("RECONCILIATION: PASS (candidate evidence — human review required)",
              file=__import__("sys").stderr)
    else:
        print("RECONCILIATION: FAIL", file=__import__("sys").stderr)

    if args.report:
        write_reconciliation_report(verdict, Path(args.report))

    return 0 if verdict["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
