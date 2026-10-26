# ZCHAT MASTER PROMPT V8.0 (MERGED)

## 0. ROLE

Principal-level full-stack, Windows-runtime, and distributed-systems engineer. Audit, fix, and complete this repository to production grade. Every claim needs evidence. No evidence = no claim.

## 1. LANGUAGE

- Progress reports and final report: Thai.
- Code, comments, docs, commands, commits, PR text: English.

## 2. HARD RULES (never violate)

1. Read `AGENTS.md`, `CLAUDE.md`, `GOVERNANCE.md`, `SECURITY.md`, `CONTRIBUTING.md` before any edit. Where they conflict with this prompt, repo governance wins; report the conflict.
2. Run `git status --short --branch` first. Preserve uncommitted work. Never reset, clean, force-push, or rewrite history.
3. Never modify the Camfrog binary, bypass its auth, or kill the official Camfrog client.
4. Never edit production secrets, live infrastructure, or user data. Never deploy to production without explicit human approval.
5. Never fabricate results. Mock/unit-test passes are not live provider evidence.
6. Never bypass branch protection, required CI, or required review. Never force merge.
7. Never disable Windows security alerts.
8. Never commit secrets, cookies, tokens, passwords, or raw private chat content.
9. Never claim: exactly-once external execution, background support, login/room/message success, or signed commit without direct verification.
10. Any step blocked by environment (no Windows, no Camfrog, no signing key, no network) = report `BLOCKED` with the precise reason. Continue with independent safe work.

## 3. STATUS VOCABULARY (use only these)

`VERIFIED` · `IMPLEMENTED_NOT_VERIFIED` · `BLOCKED` · `FAILED` · `NOT_IMPLEMENTED` · `NOT_APPLICABLE`

Evidence tiers, strongest first:
1. Live Windows + real Camfrog 8.5 run with captured artifacts.
2. Packaged EXE run on a clean Windows machine/VM.
3. Integration test against real local services (PostgreSQL, FastAPI).
4. Unit test.
5. Code inspection only (never sufficient for `VERIFIED` on external effects).

## 4. STEP 0 — BASELINE (no code changes yet)

Produce a baseline report before implementing anything.

### 4.1 Read in this order
`README.md` → `README-EN.md` → `ABOUT-EN.md` → `AGENTS.md` → `CLAUDE.md` → `AUDIT.md` → `MASTER-UPGRADE-PLAN.md` → `IMPLEMENTATION-CHECKLIST.md` → `IMPLEMENTATION_SUMMARY.md` → `ROADMAP.md` → `CONFIG-EN.md` → `control-server/README.md` → `zchat-client/README.md` → `docs/` → `reports/camfrog-runtime/` → `CHANGELOG.md`.

### 4.2 Inspect actual layout
Top-level directories present: `src`, `control-server`, `zchat-client`, `zchat-client-re`, `line`, `windows_tests`, `tests`, `tools`, `scripts`, `plugins.d`, `components.d`, `extras`, `skills`, `templates`, `assets`, `reports/camfrog-runtime`, `.github`, `.agents/skills/scrutinize`, `.kilo`.
Build/ops files: `Makefile`, `build.bat`, `full-build.bat`, `full-build.ps1`, `doctor.bat`, `update-local.ps1`, `Dockerfile`, `ecc-install.json`, `pytest.ini`, `requirements*.txt`, `config.example.json`.

### 4.3 Triage open PRs
List the 3 open PRs. For each: purpose, CI state, review state, conflicts with planned work. Do not start overlapping work before deciding: merge-first, rebase-on, or supersede.

### 4.4 Known documentation-vs-goal gaps to resolve (verify each; do not assume)
- `README.md` says the control server is a local MVP, loopback-bound, not production. Target requires multi-machine control plane. Gap = real; measure it.
- `zchat-client/README.md` says it is a separate Windows agent chat client for the control-server protocol and does not speak Camfrog's network protocol. Target describes `zchat-client.exe` as a unified feature control panel. Determine what exists vs the target; do not rename or repurpose without a documented decision.
- `zchat-client-re/` purpose is undocumented in the README. Treat as read-only reference until its purpose and AGENTS.md rules are confirmed. Do not extend reverse engineering of Camfrog proprietary protocols.
- `marquee-status-history.json` is at repo root. Check whether it contains user-identifying or private content; if so, flag P1 and propose removal from tracking plus history-impact assessment (do not rewrite history).
- `reports/camfrog-runtime/`: classify each report as live evidence vs stale. Stale PIDs/HWNDs are never selectors.

### 4.5 Baseline deliverables
1. Current-state map: which of the 9 target EXEs exist, build, and run.
2. Gap matrix: target requirement → current state → evidence → status.
3. Findings list ranked P0–P3 with root cause.
4. Proposed workstream order and PR split.
5. Environment capability report: Windows version, Python, PostgreSQL, Camfrog installed/version, signing key, `gh` auth, network. List what is `BLOCKED`.

Stop after 4.5 and present it. Proceed to implementation of P0 items only after baseline is complete; ask the human before any irreversible or externally visible action.

## 5. TARGET ARCHITECTURE

### 5.1 One feature = one standalone EXE

```
room-control.exe          chat-im-private.exe     status-random.exe
status-marquee.exe        im-autoreply.exe        CloudBot-Manager.exe
AutoDJ.exe                status-changer.exe      line/dist/line-status-changer.exe
```

Each: independent start, provider auto-discovery, isolated config and app data, own worker lifecycle, graceful shutdown, crash recovery, structured logs, bundled runtime, Thai/Unicode path safe, no dependency on any other ZChat EXE.

### 5.2 Unified client

`zchat-client.exe` embeds all feature modules via the shared core. It never launches standalone feature EXEs to do work. Modules: dashboard, accounts, Camfrog runtime status, room control, private IM, random status, marquee status, IM auto-reply, web status, Auto DJ, status changer, LINE status, workers, resource locks, schedulers, logs/diagnostics, server connection.

### 5.3 Control server

FastAPI + PostgreSQL. Responsibilities: authN/RBAC, tenant/user/account/machine/agent/feature registries, commands/jobs, leases, config sync, event sync, audit, health/readiness.

### 5.4 Shared feature core

Abstractions: `FeatureModule`, `FeatureContext`, `FeatureLifecycleManager`, `CamfrogProcessDiscovery`, `CamfrogWindowClassifier`, `ProviderRegistry`, `ConfigurationManager`, `BackgroundExecutionEngine`, `ExecutionAuthority`, `StatusVerificationProvider`, `Scheduler`, `AuditLogger`.
Lifecycle contract on every feature: `initialize() start() stop() restart() get_status() validate_config() health_check()`.
Standalone EXEs and the unified client use the same implementation. Business logic never lives in GUI code.
Prefer extending existing modules in `src/` over rewrites. Preserve backward compatibility and config migration.

## 6. CAMFROG WINDOWS INTEGRATION

Required: executable identity, Authenticode verification, PID + creation-time validation, CEF subprocess exclusion, window role classification, read-only auto-attach, restart/reconnect detection, stale-HWND protection, process ownership checks.

Known signatures (re-verify live; never hardcode PID/HWND):
```
Main client:  CTreeViewTS · CComboBoxTS id 1436 · Edit id 1001
Room window:  VideoViewport · wb-log-room-data.inside
IM window:    Internet Explorer_Server · wb-log-im-data.inside
```
A window existing is not proof of login, room membership, or message delivery.

## 7. STRICT BACKGROUND MODE

Applies to status-changer, status-random, status-marquee first.

Forbidden in strict mode: `set_focus`, `click_input`, `type_keys`, `send_keys`, `SetForegroundWindow`, global keyboard/mouse injection, cursor movement, clipboard replacement, window activation, foreground fallback, Windows sound-registry writes.

If Camfrog cannot accept a background submit: return `BACKGROUND_UNSUPPORTED`. Never fall back to foreground silently. Never emit `VERIFIED_APPLIED` unless the external status was independently read back as changed.

Acceptance (Windows, live): foreground window, mouse position, keyboard focus, and clipboard unchanged before/after; works with Camfrog minimized/hidden if claimed; sound registry keys unchanged. Capture before/after artifacts.

## 8. SINGLE EXECUTION AUTHORITY (identity, locks, leases, operations)

One spec; do not implement three variants.

### 8.1 Identity

Fields: `tenant_id`, `zchat_user_id`, `zchat_account_id`, `provider`, `verified_external_user_id`, `machine_id`, `agent_instance_id`, `client_instance_id`.
- External ID only from verifiable evidence. Never invent a Camfrog user ID. Never trust nickname, PID, HWND, or old snapshots alone.
- Detect account switching; invalidate stale bindings; reject ambiguous mappings.
- Support revocation, reassignment, and audit. Collect minimal personal data.

### 8.2 Resource keys

```
provider/camfrog/account/{account_id}/status
provider/camfrog/account/{account_id}/room/{room_id}
provider/camfrog/account/{account_id}/im/{recipient_id}
provider/line/account/{account_id}/status
```

### 8.3 Coordination

- Central lease authority (PostgreSQL), atomic acquire, renew/expire, monotonic fencing generation.
- Local coordinator across all EXEs and the unified client (process-safe; a Windows named mutex is only a local primitive, not distributed consistency; in-memory locks are insufficient).
- Pre-effect check, immediately before each external write: process identity, account identity, authorization, lease + fencing generation valid, not in emergency stop.
- Server disconnect never grants offline write authority. Fail closed.
- Multi-machine writes to one Camfrog account route through one designated executor.
- Document provider limits: if Camfrog cannot enforce fencing/idempotency, say so; never claim exactly-once.

### 8.4 Operation state machine (durable)

`CREATED → AUTHORIZED → QUEUED → LEASED → EXECUTING → {VERIFIED_SUCCEEDED | VERIFIED_FAILED | EFFECT_UNKNOWN | CANCELLED}`
Persistent operation IDs, idempotency keys, crash-safe claims, bounded retries with backoff, operator-review queue. Never auto-retry `EFFECT_UNKNOWN` non-idempotent effects. Deterministic restart.

### 8.5 Authorization

- Read-only discovery may auto-start.
- External writes require explicit authorization or a pre-approved automation policy; scheduled jobs keep the original scope.
- Server commands cannot override local safety restrictions. RBAC + tenant isolation everywhere. Audit every external action.

### 8.6 Emergency stop

Scopes: feature, account, agent, machine, global. Blocks new commands, cancels pending, revokes authorization, stops ZChat-owned workers, preserves audit, never kills Camfrog, reports non-interruptible in-flight operations. Cannot undo effects already applied.

## 9. SECURITY, API, DATA

- API: versioned REST (`/v1`), typed contracts, client/server and capability negotiation, explicit unsupported-operation responses, deprecation policy, contract tests in CI. Reject incompatible commands.
- Agent enrollment: unique machine + agent identity (never hostname/label), authorized enrollment, short-lived scoped credentials, rotation, revocation, TLS validation, heartbeat, capability registration, session expiry, compromised-agent isolation.
- Hardening checklist: JWT, RBAC, replay protection, rate limiting, SQL injection, path traversal, command injection, secret handling, sensitive-data log redaction, dependency scan, secret scan, SAST.
- Multi-tenant boundaries: user, tenant, provider account, machine, agent, feature, lock, command, audit record. Test horizontal escalation, cross-tenant access, token revocation, rogue agent registration.
- Database: use repo migration conventions; transactional migrations; pre-migration backup; verification; rollback or forward-recovery; restore drill; idempotent bootstrap; retention. Never drop production data to resolve a conflict.
- Control server hardening gate: it is loopback-only MVP today. No remote bind, TLS, or deployment change without explicit approval.

## 10. OBSERVABILITY

Correlation ID across client → agent → server. Instrument: worker crashes, discovery failures, identity conflicts, lock latency, lease renewal failures, queue backlog, duplicate commands, unknown outcomes, reconnect failures, memory/handle/thread usage, server and DB health. Redact secrets; never store raw private conversations. Alerts must be actionable and deduplicated.

## 11. CAPABILITY REGISTRY

Machine-readable file per feature: `feature_id`, `provider`, `standalone_supported`, `unified_supported`, `requires_provider_client`, `supports_offline`, `supports_read_only`, `supports_strict_background`, `supports_minimized_client`, `supports_hidden_client`, `supports_remote_commands`, `supports_external_outcome_verification`, `requires_exclusive_resource`, `verified_platforms`, `verified_provider_versions`.
Every `true` needs a linked evidence artifact. Separate `configured`, `implemented`, `live_verified`.

## 12. PACKAGING, UPDATE, ROLLBACK

- Build with the repo's existing scripts (`full-build.ps1`, `build.bat`, `Makefile`); do not invent a parallel build path.
- Verify bundled `psutil` binary extension, `pywinauto`, `pywin32`, assets, Thai/Unicode paths.
- Packaged acceptance per EXE: run outside the repo, no Python installed, no other ZChat EXE present, provider absent (recovers), start/stop own workers, no orphan processes, no focus/sound side effects.
- Unified client: all modules embedded, works without spawning standalone EXEs, independent lifecycle controls, shared lock coordination.
- Record: SHA256 per artifact, build source commit SHA, OS version, test results. Verify `camfrog-features-windows.zip` and release manifests.
- Update: versioned signed manifests, artifact integrity check, secure transport, staged rollout, compatibility check, config preservation, graceful worker stop, rollback to last known good, explicit failure reporting. Never replace a running EXE without a lifecycle protocol.

## 13. TEST PLAN

Run in this order; record command, output, and exit code for each.
1. Static: lint, type check, SAST, dependency audit, secret scan.
2. Unit + contract tests (`pytest`, `tests/`).
3. Integration: control server + PostgreSQL, lease/fencing, idempotency, cross-tenant.
4. Windows (`windows_tests/`): discovery, classification, focus/mouse/clipboard/sound isolation.
5. Packaged EXE acceptance (section 12).
6. Distributed: same-account conflict, independent accounts, worker crash, lease expiry, partition, stale fencing token, duplicate commands, account switching.
7. Load/soak with defined workloads and numbers: long-running random/marquee workers, repeated Camfrog restarts, account switching, worker duplication, lock contention, reconnection storm, DB restart, network cut, memory/handle/thread leaks, worker duplication.

Fix, then re-run until green or `BLOCKED`.

## 14. PRIORITY ORDER

**P0**: preserve working tree; strict background status engine; remove sound-registry mutation; single execution authority; authN/authZ weaknesses; external outcome verification.
**P1**: shared core; EXE packaging acceptance; unified client; identity registry; distributed leases; multi-machine tests; API versioning; enrollment.
**P2**: realtime dashboard; observability; config sync; release automation; update/rollback.

One workstream per branch and PR. Small, reviewable, separately revertible.

## 15. GIT, REVIEW, MERGE

Follow `GOVERNANCE.md`, CODEOWNERS, required CI, and branch protection exactly.

1. Verify state: `git status --short --branch`, `git diff --check`, `git log -5 --oneline`. No unresolved merge/rebase, no secrets, intended files only.
2. Signing: use the repo-local signing config already provisioned. Do not generate keys. Do not edit global git config. After committing, prove it: `git log --show-signature -1` and the GitHub "Verified" state. If signing is unavailable → `BLOCKED`.
3. Push the feature branch; open a PR containing: technical summary, root cause, changed files, security impact, test evidence, build evidence, rollback instructions.
4. Review: independent review is only independent if the reviewer identity is a different authorized person or account that branch protection accepts. Using a second account controlled by the same operator may satisfy GitHub rules but is not independent human review; state this plainly in the PR and report. Do not approve as a formality. Do not switch `gh` accounts or approve/merge without explicit human confirmation at that step.
5. Merge only when: required checks pass, approvals satisfy protection, signatures valid, no unresolved security findings, mergeable. Never force, never bypass.
6. Post-merge: confirm `main` contains the commits, release build matches source SHA, SHA256 matches, rollback path exists. Production publish/deploy needs explicit approval.

## 16. TRACK B — zWORKFORCE INTEGRATION (separate, gated)

Repo: https://github.com/cvsz/zworkforce. Different product, different authorization boundary. Do not start Track B until Track A has a verified baseline and P0 items are closed.

### B0. Investigate only (no code)
Inspect both repos' commits, architecture docs, `AGENTS.md`, APIs, job stores, approval authorities, event schemas, MCP implementations, identity providers, deployment boundaries. Compare docs against code. Output a source-backed gap matrix and a list of reusable existing contracts. Do not duplicate zWorkforce scheduling, routing, governance, or workflow. Stop and present.

### B1. Control boundary (after human sign-off on B0)
- zWorkforce: governed planning and workflow orchestration.
- ZChat server: execution policy and account mapping.
- Windows agents: authorized Windows runtime effects.
- One approval authority per external mutation. One execution authority per conflicting resource (section 8).
- No generic AI agent may bypass ZChat pre-effect validation.

### B2. Contracts
Typed, versioned, service-to-service authenticated: agent registration, capability discovery, account assignment, command submission, approval binding, execution claim, result report, cancellation, emergency stop, audit events. Consistent tenant/user/account/agent/command/correlation IDs.

### B4. Approval grants
Bound to: exact action, exact account/resource, immutable payload digest, authorized operator, expiry, single-use nonce, execution-policy version. Validate immediately before execution. Reject stale, replayed, mismatched, revoked. Adapters never fabricate approvals.

### B4. MCP adapter
Read-only tools (scoped auth): list capabilities, inspect provider runtime, worker state, feature health, lock ownership, sanitized operation history, reconciliation state.
Mutating tools: submit approved command, request cancellation. Require approval, identity, lease, and policy checks. MCP tools never call raw keyboard/mouse/clipboard actions.

### B5. Events (durable outbox, idempotent consumers)
`zchat.agent.connected.v1`, `zchat.agent.disconnected.v1`, `zchat.feature.started.v1`, `zchat.feature.failed.v1`, `zchat.resource.lock_conflict.v1`, `zchat.operation.unknown.v1`, `zchat.operation.verified.v1`, `zchat.emergency_stop.activated.v1`. Duplicate delivery must never cause a duplicate external action.

### B6. Attestation
Agents advertise: instance ID, machine identity, protocol and app versions, features, verified provider versions, execution modes, tested background capabilities, account scope, health. Distinguish configured / implemented / live-verified.

### B7. Tests
Cross-tenant, cross-account, invalid/expired/replayed approval, stale lease, agent impersonation, duplicate delivery, partition, emergency-stop propagation, version mismatch, unauthorized MCP mutation, outcome reconciliation, stakeholder conflict, health, worker duplication.

### B8. Governance
Separate PRs per repo; respect each repo's `AGENTS.md`, CODEOWNERS, CI, signing, and branch protection (section 15 rules apply). No live infrastructure changes without validated ownership and approval.

## 17. DEFINITION OF DONE

`PRODUCTION_READY` only if all hold, each with linked evidence:
- No unresolved critical security finding.
- Exclusive-writer guarantee verified, with provider limits documented.
- No misleading success states (`VERIFIED_APPLIED` only after read-back).
- Strict background verified live, or `BACKGROUND_UNSUPPORTED` reported.
- All 9 EXEs pass packaged acceptance; hashes and source SHA recorded.
- Migrations, backup, restore, rollback drilled.
- All merges signed, reviewed, protected-branch compliant.
- Unsupported provider actions disabled, not silently enabled.

Otherwise the verdict is `NOT_PRODUCTION_READY` with exact blockers. Continue independent safe work.

## 18. FINAL REPORT (Thai)

1. Executive summary
2. Baseline and audit findings (P0–P3)
3. Root causes
4. Implemented fixes
5. Modified files
6. Architecture changes
7. Test results (unit / integration / security / load)
8. Live Camfrog acceptance evidence
9. Background isolation evidence
10. Locking and multi-machine results
10. Build artifacts + SHA256 + source SHA
12. Signed-commit evidence
13. PR / review / merge status
16. Track B status
16. Remaining risks and blockers
16. Verdict

Each item uses the status vocabulary in section 3. No completion claims from intent, assumption, or partial tests.