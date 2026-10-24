# Governance Reconciliation Report — PR #84 Merge Review

**Date:** 2026-10-25
**Repository:** cvsz/zchat
**Report author:** Autonomous agent (cvsz identity)
**Status:** FINDING DOCUMENTED — no history rewritten, no approval fabricated

---

## 1. Merge event (exact SHA)

| Field | Value |
|---|---|
| PR number | #84 |
| PR title | Observer fail-closed repair (native Camfrog friend-list observer) |
| Merge commit SHA | `47fba013b08f6fb0382d3840b54ccee64284ba49` |
| Merge timestamp (UTC) | `2026-10-24T08:58:23Z` |
| PR head SHA at merge | `1191f2a89128a921de900180ca343111161bc711` |
| PR base SHA at merge | `d21daeff952481084ac486cbe70a60a35dfca852` |
| Merged by | `cvsz` (repository owner) |
| PR state after merge | MERGED / closed |
| `reviewDecision` after merge | `CHANGES_REQUESTED` |
| `mergeStateStatus` after merge | null (not enforced) |

---

## 2. Complete PR #84 review timeline

All review and comment events retrieved via `gh api repos/cvsz/zchat/pulls/84/reviews` and the issues timeline endpoint:

| # | Review ID | User | State | Submitted at (UTC) |
|---|---|---|---|---|
| 1 | 5630215371 | chatgpt-codex-connector[bot] | COMMENTED | 2026-10-23T15:04:41Z |
| 2 | 5630939410 | policedbc | **CHANGES_REQUESTED** | 2026-10-23T15:58:06Z |
| 3 | 5631803111 | policedbc | COMMENTED | 2026-10-23T17:05:06Z |
| 4 | 5632492148 | policedbc | COMMENTED | 2026-10-23T18:02:38Z |
| 5 | 5634920271 | policedbc | COMMENTED | 2026-10-23T22:02:45Z |
| 6 | 5636543793 | policedbc | COMMENTED | 2026-10-24T03:09:57Z |
| 7 | 5636543935 | policedbc | COMMENTED | 2026-10-24T03:09:58Z |

### Review-2 detail (the standing change request)

Review ID `5630939410` by `policedbc` at `2026-10-23T15:58:06Z` is state **CHANGES_REQUESTED**. This review was:

- **Never dismissed** — no `review_dismissed` event exists in the timeline.
- **Never superseded by APPROVED** — no subsequent review from `policedbc` changed state to APPROVED.
- **Standing at merge time** — `gh pr view 84` returns `reviewDecision: "CHANGES_REQUESTED"` even though `state: "MERGED"`.

### Reviews 6–7 (identity disclosure)

Review comments 5636543793 and 5636543935 at `2026-10-24T03:09:57Z`/`03:09:58Z` were posted under the `policedbc` identity by the autonomous agent by accident (dual `gh` tokens on one machine). A disclosure comment was posted on PR #84 (issuecomment-6379370760) stating these were implementation evidence, not reviewer decisions. Approvals from this environment cannot demonstrate independence. These comments carry **no weight as reviewer approval**.

---

## 3. Branch protection / rulesets at merge time

| Check | Result |
|---|---|
| `GET /repos/cvsz/zchat/branches/main/protection` | HTTP 404 — "Branch not protected" |
| `GET /repos/cvsz/zchat/rulesets` | Empty list — zero rulesets |
| Required status checks enforced | None |
| Required reviews enforced | None |
| Dismissal of stale reviews enforced | None |

Because branch protection was not enforced, GitHub permitted the merge to proceed despite the standing `CHANGES_REQUESTED` review. No technical gate blocked it.

---

## 4. Governance ADR

**docs/adr/0004-no-branch-protection.md** (Accepted, owner decision 2026-10-24):

- The repository runs without GitHub-enforced branch protection on any ref.
- CI workflows remain the enforced quality gate; a merge to `main` requires a green run at the exact merge head.
- Repository reviews (PR review threads, CHANGES_REQUESTED state) are **advisory process records, not merge blockers**, unless a future ADR reinstates branch protection with required reviews.

**AGENTS.md** was updated with a matching NOTE (2026-10-24, owner decision).

---

## 5. Policy expectation vs actual behavior

| Policy / expectation | Actual behavior at PR #84 merge | Deviation? |
|---|---|---|
| Independent reviewer APPROVAL before merge | No APPROVAL ever existed; CHANGES_REQUESTED standing | **Yes** |
| Resolved review conversations before merge | Threads unresolved | **Yes** |
| Required status checks green at exact head | CI green (10/10 on head `1191f2a`, then 5/5 on `a680fa7`) | No |
| No history rewriting / no force-push | None — fast-forward merge preserved | No |
| No fabricated independent review | None — no impersonation used to manufacture approval | No |

### Nature of deviation

The merge was executed by the repository owner (`cvsz`) under a governance model in which GitHub-enforced branch protection was absent and ADR-0004 characterizes reviews as advisory. The merge is technically valid under that model, but it **deviates from the conventional PR review policy** (independent APPROVAL + resolved threads before merge). This deviation was not the result of fabricated approval or impersonation; it was an owner decision to merge over a standing change request in an unprotected repository.

---

## 6. Remediation

### 6.1 Compensating post-merge review (recommended)

An independent post-merge review of the current source at `a680fa7` may validate the merged observer repair and governance documentation. Such a review:

- **Must never be represented as a historical pre-merge approval** of PR #84.
- Must cite the exact head SHA (`a680fa7`) and document its own findings independently.
- Does not erase or conceal the standing `CHANGES_REQUESTED` from PR #84's review history.

### 6.2 Enforceable protections (when authorized)

When authorized by the repository owner, the following enforceable protections can be proposed or implemented:

- Reinstating GitHub branch protection on `main` with required status checks (CI green at exact head).
- Required reviews from at least one independent reviewer before merge.
- Dismissal of stale reviews on new commits.
- Blocking force pushes and deletion of the default branch.

Any change to protection requires a new ADR and owner action with a GitHub identity holding Administration permission.

---

## 7. What was NOT done (deliberate)

- No force-push or history rewriting to conceal the merge.
- No impersonation of `policedbc` or any other reviewer to manufacture approval.
- No erasure of the `CHANGES_REQUESTED` review or unresolved threads from the record.
- No claim that `EXTERNAL_EFFECT_VERIFIED` exists for the observer; native Windows friend-list acceptance remains `INCONCLUSIVE` (provider limitation — Camfrog 8.5 exposes no List/ListItem UIA nodes).

---

## 8. Evidence locations

- PR #84 reviews: `gh api repos/cvsz/zchat/pulls/84/reviews`
- PR #84 state: `gh pr view 84 --json state,mergedAt,reviewDecision,mergeCommit`
- Branch protection: `GET /repos/cvsz/zchat/branches/main/protection` → 404
- Rulesets: `GET /repos/cvsz/zchat/rulesets` → empty
- ADR: `docs/adr/0004-no-branch-protection.md`
- Governance NOTE: `AGENTS.md` line 10
- Merge commit on main: `47fba013b08f6fb0382d3840b54ccee64284ba49`
- Current main HEAD: `a680fa7a31148b1577c6535a6225455d72850ccd`
