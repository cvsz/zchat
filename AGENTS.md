# AGENTS.md — ZChat Repository Engineering Contract

## Purpose
This repository contains ZChat application code: Camfrog Windows automation, standalone GUI/CLI executables, a unified Windows client, a FastAPI/PostgreSQL control server, and integration boundaries with zWorkforce. Maintain real application behavior and security boundaries; do not treat this repository as an uninitialized template or infer readiness from scaffolding.

## Operating rules
- Read README.md, CONTRIBUTING.md, SECURITY.md, ROADMAP.md, and the closest AGENTS.md before editing.
- Keep code, configuration, filenames, commit messages, and technical documentation in English.
- Prefer the smallest reviewable change that satisfies the requested scope.
- Never weaken CI, security scanning, dependency review, branch protections, or release controls merely to make a check pass. 
- Governance verification (2026-10-29): live repository ruleset `26005245` is active but currently contains only `deletion` and `non_fast_forward` rules; it does not require pull-request reviews or status checks, and the API reports `current_user_can_bypass=always`. The connected GitHub integration receives HTTP 403 when reading branch protection, so administrator read-back remains outstanding. Until an administrator verifies effective settings, treat required reviews/checks as NOT enforced by GitHub, manually verify CI on the exact merge head, and do not claim that branch protection or required CI gates are configured.
- Never commit credentials, tokens, private keys, production endpoints, personal data, or realistic secrets. Use documented placeholders.
- Do not invent project-specific owners, domains, deployment providers, package registries, cloud accounts, or credentials.
- Preserve standalone-executable isolation, control-server tenant/account scoping, and Windows foreground/background safety; do not make a simulated Camfrog action appear externally successful.
- Reuse existing workflows and documents instead of creating overlapping alternatives.
- Pin permissions for GitHub Actions to least privilege and prefer maintained first-party/verified actions.
- Treat external input, generated artifacts, pull requests from forks, and dependency metadata as untrusted.

## Configuration and sensitive data
Use obviously fictitious placeholders in examples. Treat Camfrog credentials, session cookies, account identity, machine identifiers, live chat contents, and evidence bundles as sensitive. Never log or commit them. Require explicit authorization and fail closed before external mutations.

## Review skill

- Use [Scrutinize](.agents/skills/scrutinize/SKILL.md) when asked to review, audit, sanity-check, or give a second opinion on a plan, PR, diff, design, or code change, or when invoked with `/scrutinize` in a compatible agent.
- Question whether the change is necessary or can be smaller before tracing real code paths and verifying behavioral claims. Cite concrete file/line evidence and distinguish unverified claims from confirmed behavior.
- The skill guides agent behavior; it does not itself install a slash command or replace required tests, CI, or human review.

## ZChat runtime and release boundaries

- Separate source/unit tests, Windows packaged-executable smoke tests, native Camfrog external-effect acceptance, and production rollout evidence; none implies the next level.
- A status field update is not a nickname or profile flair update. Require a verified provider-specific adapter and observed external state before recording success.
- F3/F4 worker lifecycle must use validated configuration paths and supervised stop/start; do not force-kill arbitrary or unverified processes.
- Do not synthesize second-account observer evidence, GUI screenshots, process identity, external outcomes, signatures, or successful workflow states.
- Keep zWorkforce integration read-only or disabled for external effects until account-scoped authorization, approval binding, durable operation states, single-writer protections, and actual Windows acceptance are verified.
- Changes to actual account/profile, room/chat, microphone, clipboard, keyboard/mouse, and desktop-focus behavior require explicit adverse-case tests and fail-closed handling.

## Change workflow
1. Inspect the current exact branch/head and existing files.
2. Identify the smallest missing or inconsistent ZChat capability.
3. Add tests or validation first when practical.
4. Implement without widening scope.
5. Run the relevant validation and security checks.
6. Update documentation when behavior, setup, governance, or release procedures change.
7. Open a pull request; do not claim merge/release readiness without exact-head evidence.

## Verification
At minimum, verify Markdown/YAML syntax for touched files, workflow permissions/triggers, links and placeholders, absence of committed secrets, and consistency between README, actual source behavior, governance, security, and release documentation.

## Pull requests and releases
PRs must state scope, tests, security impact, compatibility/migration impact, documentation impact, deployment impact, and rollback. Releases require green required checks and explicit evidence; never infer production readiness from documentation alone.

## Security
Report vulnerabilities through SECURITY.md, not public issues. Security-related documentation must redirect sensitive reports accordingly. Fail closed when a security-sensitive configuration is incomplete.

## Documentation ownership
- `.github/`: GitHub automation, community health, ownership, issue/PR templates.
- `docs/`: versioned engineering, operations, release, repository-administration, and rollout guidance.
- `docs/adr/`: architecture decision records.
- Root Markdown files: repository-wide policy and project lifecycle guidance.

## Nested AGENTS.md
Add a child AGENTS.md only when a subtree has durable rules that differ from this contract. The nearest AGENTS.md may add stricter local requirements but must not weaken repository-wide security rules.


## ZEAZ reusable execution layer

- Use [ZEAZ-INTRODUCTION.md](ZEAZ-INTRODUCTION.md) as the cross-agent execution framework.
- Use [docs/ai/README.md](docs/ai/README.md) to select task-specific reusable prompts and playbooks.
- Task playbooks are additive and never weaken this repository contract or narrower subtree rules.
- Use the canonical evidence-state definitions and decision rule in [ZEAZ-INTRODUCTION.md](ZEAZ-INTRODUCTION.md); do not redefine them per harness or playbook.
- Keep implementation, verification, deployment, and production readiness as distinct states.


## Existing-repository rollout

For shared ZEAZ governance updates, audit the ZChat runtime first and import only compatible controls. Do not overwrite ZChat-specific safety rules, CI matrices, release workflows, infrastructure ownership, or operational evidence merely to match a generic template.

For repository administration, `scripts/github_admin.py` requires an authenticated GitHub identity with Administration permission. Treat successful read-back verification—not script presence—as evidence that the controls are effective.

## Windows test evidence

- Results not run on Windows must be reported as `NOT RUN`, never as `PASSED` or `UNVERIFIED` to close work.
- Every Windows test run must attach the log folder path under `reports/windows-runs/<timestamp>/` containing `console.log`, `junit.xml`, `pytest.log`, `exit.txt`.
- A `BLOCKED` result requires the exact failing import or call (error text), not just "Windows-only".
- Use `scripts/win-test.sh` for local WSL-to-Windows runs; use `.github/workflows/windows-tests.yml` for CI.
- The verification package and `windows_tests/` require Windows 11 + real Camfrog for full E2E. Linux-only results are `BLOCKED`, not `PASSED`.
