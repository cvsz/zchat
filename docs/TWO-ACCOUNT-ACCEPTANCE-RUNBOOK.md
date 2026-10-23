# Two-Account Status-Change Acceptance Runbook

This runbook defines how a real Windows Status Changer action is checked against an
independent Account B observation in Linux Chrome. It may only be executed on
authorized accounts by an operator who runs the live systems; this repository's CI
and offline tests cannot perform it. Every result below must carry the exact-head
SHA of the checkout that produced it.

## Stage chain (never skip a stage)

| Stage | Who records it | Tool / location |
| --- | --- | --- |
| `COMMAND_ACCEPTED` | Windows runtime | `StatusExecutionEvidence.request_id` minted per `execute_status_change` in `src/camfrog/camfrog_status_engine.py` |
| `WORKER_EXECUTED` | Windows runtime | worker PID/authorization handshake + history records (initially `UNVERIFIED` dispatch, per repo policy) |
| `WEB_PROFILE_OBSERVED` | Linux Chrome observer | `tools/web_profile_observer.py` summary.json `stage_chain.WEB_PROFILE_OBSERVED` |
| `EXTERNAL_EFFECT_VERIFIED` | Human reviewer only | reconciliation of the exact-head Windows evidence with the observer bundle; never auto-assigned by any tool |

A matching web string alone (`WEB_PROFILE_MATCH`) proves only what the browser showed.
Worker logs, HTTP status, page title, or a screenshot are each individually insufficient.

## Acceptance procedure

1. **Baseline (Windows).** Sign in to Camfrog Desktop as Account A. Confirm the
   account profile URL `https://profiles.camfrog.com/th/<Account-A>` is exact.
2. **Baseline (Linux).** In the dedicated Account B Chrome profile (see
   `docs/WEB-PROFILE-ACCOUNT-B-OBSERVER.md`), record the pre-change status:
   ```bash
   python tools/web_profile_observer.py \
     --nickname <ACCOUNT-A> \
     --expected '<CURRENT STATUS OR MISMATCH MARKER>' \
     --browser-profile "$HOME/.local/share/zchat-observer/account-b" \
     --output "$HOME/zchat-observer-evidence/baseline" \
     --refresh-seconds 60 --samples 3 --login
   ```
   Keep the JSONL/PNG evidence of this run: it is the before state.
3. **Marker.** Generate a unique harmless marker and stamp the Windows-side linkage:
   ```bash
   python -c "import uuid;print(uuid.uuid4().hex[:12].upper())"   # e.g. RUNMARKER-A38B
   ```
4. **Apply marker (Windows).** Use the real Status Changer (F3 start / F4 stop) to
   submit the marker as the account status. Export the Windows evidence for that
   operation (history/`StatusExecutionEvidence` fields, worker log) with its
   `request_id`.
5. **Observe (Linux).** Run the observer with `--expected <MARKER>` and
   `--command-id <request_id>` + an opaque `--account-b-reference`:
   ```bash
   python tools/web_profile_observer.py \
     --nickname <ACCOUNT-A> \
     --expected '<RUNMARKER-A38B>' \
     --command-id '<WINDOWS-REQUEST-ID>' \
     --account-b-reference '<OPAQUE-B-REF>' \
     --browser-profile "$HOME/.local/share/zchat-observer/account-b" \
     --output "$HOME/zchat-observer-evidence/marker-a" \
     --refresh-seconds 30 --samples 20
   ```
   `summary.json` records `transition_detected` only when the first and last
   observed content hashes differ, and never auto-claims any Windows stage.
6. **Second marker.** Repeat steps 3-5 with a distinct marker to prove repeatability.
7. **Stop check.** After F4 stop, run a short observer run `--expected <marker-2>`;
   the field must NOT transition to that marker during the check window. This is
   necessary but not sufficient evidence that no further authorized changes occur.
8. **Reconcile (reviewer).** A second reviewer correlates:
   the Windows request/operation ids and timestamps, the observer `summary.json`
   (both marker runs), `manifest.json` SHA-256 of every file, and the before/after
   transition across the baseline and marker bundles. Only this human-reviewed
   agreement may return `EXTERNAL_EFFECT_VERIFIED`. Produce the reconciled report
   under `reports/windows-runs/<timestamp>/` (NOT RUN until executed).

## Privacy rules

- Account B logs in interactively inside the dedicated Chrome profile; credentials,
  cookies, CSRF tokens, and session storage are never exported, logged, printed, or
  committed.
- The observer records only text digests (SHA-256), not the rendered text, and
  screenshots stay local with restrictive permissions.
- `--account-b-reference` is an operator-supplied opaque tag; the tool never
  captures the browser identity itself.

## Missing evidence needed to move this runbook to VERIFIED

- A real Chrome run with an authenticated distinct Account B session
  (repository CI cannot provide it).
- The post-render DOM confirmation that a stable unique element carries Custom
  Status for the current site (a provided screenshot showed the banner string
  `, but every challenge brings`; a validated selector must still be confirmed
  live, and must not be a hardcoded permanent status).
- A real Windows run of two distinct markers plus the stop check with worker
  evidence export.
- Independent reviewer sign-off recorded with both exact-head SHAs.
