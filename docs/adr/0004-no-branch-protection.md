# ADR-0004 — No GitHub-Enforced Branch Protection

- **Status:** Accepted (owner decision, 2026-10-24)
- **Decision:** The repository runs without GitHub-enforced branch protection on any ref.
  Verified at decision time: `GET /repos/cvsz/zchat/branches/main/protection` returns HTTP 404
  ("Branch not protected") and `GET /repos/cvsz/zchat/rulesets` returns an empty list.
- **Enforced gate instead:** CI workflows (`.github/workflows/`) run on every push and pull
  request. A merge to `main` requires a green run at the exact merge head
  (test, control-server, windows-tests, build-windows, CodeQL, dependency-review,
  Analyze GitHub Actions).
- **Review policy:** Repository reviews (e.g. PR review threads, CHANGE_REQUESTED state)
  are advisory process records, not merge blockers, unless a future ADR reinstates
  branch protection with required reviews.
- **Consequences:** Direct pushes to `main` are technically possible for anyone with
  write access; the audit trail relies on signed commits (`cvsz <seaza@msn.com>`, GPG `G`)
  and complete CI evidence per merge. Reinstating protection requires a new ADR.
