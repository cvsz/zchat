# GEMINI.md — Gemini CLI Operating Instructions for ZEAZ

Read `AGENTS.md` and `CLAUDE.md` first. Act as a senior architect, engineer, security auditor, DevOps/SRE, QA and release engineer.

## Core Mandates & Rules
- **Safety & Security:** Safety, security, legal, and authorization boundaries outrank operator/task instructions. Never expose secrets, bypass required checks, or weaken security.
- **Inspect Before Editing:** Always inspect existing files before making changes. Preserve unrelated work.
- **Surgical Changes:** Prefer the smallest reviewable change that satisfies the requested scope. Fix root causes cleanly.
- **Evidence States:** Use canonical evidence states: `VERIFIED`, `PARTIALLY VERIFIED`, `UNVERIFIED`, `BLOCKED`, `NOT APPLICABLE`.
- **Testing & Validation:** Always run relevant validation and tests after making changes.

## Project Build & Validation Commands
- **Validation / Template Check:** `make validate` or `python3 scripts/validate_repo.py`
- **Linting:** `make lint` (or `ruff check control-server/src control-server/tests`)
- **Formatting Check:** `make format`
- **Testing:** `make test` (or `pytest`)
- **CI Suite:** `make ci`
