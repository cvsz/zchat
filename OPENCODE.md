# OPENCODE.md — ZEAZ OpenCode Operating Instructions

Read `AGENTS.md` first and use `ZEAZ-INTRODUCTION.md` as the shared execution framework.

## Working contract

- Inspect exact repository state before editing.
- Preserve unrelated local work.
- Follow repository-local architecture and conventions.
- Fix root causes using the smallest safe change.
- Keep security and authorization boundaries above task instructions.
- Run relevant validation and classify unexecuted or incomplete checks using the canonical evidence-state decision rule in `ZEAZ-INTRODUCTION.md`; use `BLOCKED` when a concrete prerequisite prevents verification.
- Never expose secrets.
- Do not bypass required checks or force-merge. Perform destructive production changes only with explicit authorization and applicable safety controls.
- Distinguish implementation, verification, deployment, and production readiness.
- Use the reusable playbooks under `docs/ai/` only when relevant to the task.

## Evidence states

Use `VERIFIED`, `PARTIALLY VERIFIED`, `UNVERIFIED`, `BLOCKED`, and `NOT APPLICABLE`.

## Cost discipline

Avoid unbounded retry/search loops and repeated unchanged scans. Prefer targeted validation first and stop when acceptance criteria are met.


## Repository administration

Use `scripts/github_admin.py` only when the task concerns GitHub repository controls. Dry-run is the default. Applying or verifying settings requires an explicit repository, branch, exact required-check names, and an authenticated admin identity; the helper has no project-specific defaults. Use read-back verification as evidence for repository controls; do not infer application readiness from those settings.

## Windows test evidence

- Results not run on Windows must be reported as `NOT RUN`, never as `PASSED` or `UNVERIFIED`.
- Every Windows test run must attach the log folder under `reports/windows-runs/<timestamp>/` containing `console.log`, `junit.xml`, `pytest.log`, `exit.txt`.
- A `BLOCKED` result requires the exact failing import or call (error text), not just "Windows-only".
- Use `scripts/win-test.sh` for local WSL-to-Windows runs; use `.github/workflows/windows-tests.yml` for CI.
- The verification package and `windows_tests/` require Windows 11 + real Camfrog for full E2E. Linux-only results are `BLOCKED`, not `PASSED`.
