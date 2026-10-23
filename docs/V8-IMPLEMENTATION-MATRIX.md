# ZChat V8 Implementation Matrix (source-backed, final head 7b1d0e4946096cde03cee498c6b54ced2a6f85b2)

Evidence states follow AGENTS.md: VERIFIED / PARTIALLY VERIFIED / UNVERIFIED / BLOCKED / NOT APPLICABLE.
Unit/CI-green is never conflated with external-provider execution. "Native Camfrog E2E" means a real
two-account Windows+Linux acceptance run with reconciled evidence at one exact head.

## Phase 0 - Reconnaissance

| Area | State | Evidence |
| --- | --- | --- |
| Open PRs | NOT APPLICABLE | `gh pr list --state open` is empty; #50/#51/#57/#61/#69/#79/#80/#81 all resolved with review evidence. |
| Open CodeQL alerts | NOT APPLICABLE | 0 open as of final state (both PR #81 remediation queries cleared locally via CodeQL CLI 2.27.3 over a fresh database and via the default-branch analysis run). |
| Stale branches | PARTIALLY VERIFIED | Many `origin/*` branches predate current main; none hold unique merged-in-review content after the #61 supersede mapping. Deletion is an owner decision, not enforced here. |
| Governance | VERIFIED | AGENTS.md/CLAUDE.md/GEMINI.md converge on the same hard rules (fail-closed, NOT RUN evidence, no synthetics). |

## Phase 1 - Status changer chain

| Item | State | Source evidence |
| --- | --- | --- |
| Worker start/stop, PID gates, no duplicate workers | VERIFIED (unit/CI) | `src/camfrog/camfrog_status_engine*`, 2.19.3 changelog entries, `tests` suite 1139 passed, `build-windows` CI |
| F3/F4 + Emergency Stop | VERIFIED (unit/CI) | `feat/f3-f4-hotkeys-*` line of work merged; windows_tests include MCI/runtime cases |
| Unique request ids + acknowledgement != provider success | VERIFIED (unit/CI) | `StatusExecutionEvidence(request_id, operation_id)` minted per `execute_status_change`; `verification_source` distinguishes local dispatch from observed provider state |
| Unicode multilingual statuses | VERIFIED (unit) | Thai/status tests; UTF-8 payload handling |
| Real provider-state verification (Native Camfrog E2E) | BLOCKED in this environment | Requires Windows 11 + Camfrog + authorized second account; CI windows-tests cover runtime fixtures, not live provider effects |

## Phase 2/3 - Two-account Linux Chrome observer (PR #78 line)

| Item | State | Source evidence |
| --- | --- | --- |
| Allowlisted profile URL, no cookie export | VERIFIED (unit) | `tools/web_profile_observer.py::allowed_url/observe` + `tests/test_web_profile_observer.py` |
| Bounded polling (default 30s, 10..300s; samples 1..1000) | VERIFIED (unit) | argparse validation + parametrized rejection tests |
| Exact visible match inside `#content-inner`; generic selectors rejected | VERIFIED (unit) | `observe()` denylist + no-selector mode test using the known reference string |
| Interactive Account B login in dedicated profile, GUI default / optional headless | VERIFIED (unit) | persistent context `channel="chrome"`; `--login` pause (real login itself NOT RUN here) |
| Evidence dir 0700 + files 0600, screenshots hashed | VERIFIED (unit) | chmod guards in run(); POSIX-only |
| summary.json + manifest.json stage-chain bundle | VERIFIED (unit) | `build_summary` never auto-asserts worker stages or an external effect; `write_manifest` hashes every file; 30 observer tests |
| command linkage / account-B reference flags | VERIFIED (unit) | `--command-id`, `--account-b-reference` validated (printable, bounded, never auto-captured) |
| Real Chrome observer live run with authenticated Account B | BLOCKED here | Needs installed Chrome + real Account B session; offline tests only |

## Phase 4 - Keep Log / evidence system

| Item | State | Source evidence |
| --- | --- | --- |
| Keep Log tab + persistent log | VERIFIED (unit) | merged #69; ui.py keep_log page, logger UTC timestamps, PII redaction |
| Observation schema (run_id, sample_index, UTC, hashes, screenshot sha) | VERIFIED (unit) | observer samples + tests |
| Account A/B identity references and command linkage | VERIFIED (unit) | `--command-id` / `--account-b-reference` stamps + summary/manifest in this change |
| Stage chain no-early-implication | VERIFIED (unit) | summary stage_chain fixed contract in tests (worker stages external, external effect always false from observer) |

## Phase 5 - Real two-account acceptance

State: BLOCKED in this environment; the executable procedure and gates are in
[`docs/TWO-ACCOUNT-ACCEPTANCE-RUNBOOK.md`](TWO-ACCOUNT-ACCEPTANCE-RUNBOOK.md)
(no simulated results are claimed). The acceptance decision itself stays with a human reviewer correlating Windows worker evidence with the
observer bundle; the tools refuse to fabricate an EXTERNAL_EFFECT_VERIFIED state.

## Phase 6 - Full feature audit

| App | State |
| --- | --- |
| room-control / chat-im-private / im-autoreply / music-dj / status-* / web-status / name-changer / name-color-changer / icon-changer | VERIFIED (unit/CI level; packaging covered by build-windows CI) |
| Combined Windows client + zchat-client | VERIFIED (unit/CI) |
| control-server | VERIFIED (unit/CI: 92 tests; RLS/audit posture documented in control-server/README) |
| zGPU client | PARTIALLY VERIFIED (loopback tooling; no real zGPU backend exercised) |
| zWorkforce | NOT APPLICABLE until authorization gates satisfied (read-only/disabled boundary holds) |

## Phase 7 - Suites run in this session (Linux WSL)

Command evidence recorded in the PR body after execution.

- Windows native suite and native Camfrog acceptance: NOT RUN here (CI runners cover the packaged smoke/runtime fixtures).
- Final CI state (PR #81 head c662dc9): all required checks passed (build-windows x2, test x2, control-server x2, windows-tests, CodeQL, dependency-review, Analyze GitHub Actions); 0 open CodeQL alerts.
