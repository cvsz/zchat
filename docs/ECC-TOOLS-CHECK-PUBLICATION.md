# ECC Tools check publication (owner action)

The ECC Tools GitHub App reports "check publication denied". This is an
owner-side GitHub App permission issue; it cannot be fixed in repository code.

## Steps for the repository owner

1. Open GitHub Settings -> GitHub Apps -> ECC Tools (or the app used for checks).
2. Grant "Checks: write" and "Pull requests: write" permissions to the app for `cvsz/zchat`.
3. Re-run the latest workflow on the PR head to republish checks.
4. Verify required check names appear on the PR via `gh pr view <n> --json statusCheckRollup`.

## Required check names to pass to scripts/github_admin.py (dry-run first)

- `ci / test`
- `ci / control-server`
- `ci / build-windows`
- `CodeQL / Analyze`
- `Dependency Review / dependency-review`

Run `python scripts/github_admin.py --dry-run` first, then read-back verify before claiming CI_PASSED.