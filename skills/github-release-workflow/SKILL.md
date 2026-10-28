# GitHub Release & Deployment Workflow Skill

## Overview
This skill provides procedural guidance for executing secure GPG-signed commits, pushing code, and performing multi-account review/audit and merging workflows using GitHub CLI (`gh`) and Git in accordance with project mandates.

## Step-by-Step Protocol

1. **GPG Commit & Push:**
   - Ensure all tests and repository validations (`make validate` / `python3 scripts/validate_repo.py`) pass successfully.
   - Execute GPG-signed commit: `git commit -S -m "<commit message>"`
   - Push to remote repository.

2. **Step 1: Switch Account for Review & Audit**
   - Switch to the designated reviewer account:
     `gh auth switch -u policedbc` (or `cvsz` depending on who pushed).
   - Act as Exec Reviewer to inspect changes, audit security/correctness, review, and approve (Approve).

3. **Step 2: Switch Account for Merge**
   - Switch to the designated merger account:
     `gh auth switch -u cvsz` (or `policedbc`).
   - Execute merge into `main` to finalize the release and deployment pipeline.
