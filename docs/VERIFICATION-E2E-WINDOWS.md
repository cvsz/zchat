# Windows E2E Verification Instructions

## Prerequisites
- Two Windows 11 machines (Primary + Secondary)

> **Not yet executable end-to-end:** `FriendListObserver.observe()` is a fail-closed placeholder. Discovery in the unified client warns that the Camfrog friend list cannot yet be read. Do not treat UI input, command dispatch, or log records as external provider verification.
- Two authorized Camfrog accounts (Account A and Account B)
- zchat-client built on both machines
- `.env` configured with verification settings

## Setup
1. From the repository root, run `build.bat` to create the repository `.venv` and required build prerequisites.
2. Build client: `zchat-client\build.bat`
3. Configure `.env`:
   - Primary: `ZCHAT_VERIFICATION_ENABLED=true`, `ZCHAT_VERIFICATION_ROLE=primary`, `ZCHAT_VERIFICATION_TARGET_ACCOUNT=<Account B>`
   - Secondary: `ZCHAT_VERIFICATION_ENABLED=true`, `ZCHAT_VERIFICATION_ROLE=secondary`, `ZCHAT_VERIFICATION_TARGET_ACCOUNT=<Account A>`

## Execution
1. Start Camfrog on both machines and log in with respective accounts.
2. On primary: start zchat-client, connect agent, start worker (F3).
3. On secondary: start zchat-client, start observer.
4. Confirm Account A is visible in Account B's Friend List.
5. Capture original observed status and display name.
6. Trigger status or display-name update on primary.
7. Record command timestamp and local execution outcome.
8. Poll secondary Friend List for expected change.
9. Compare observed vs requested values.
10. Capture screenshots on change detection.
11. Once a real observer and evidence collector are implemented and independently tested, verify evidence bundle: `evidence/<run_id>/` with manifest.json and checksums.sha256.

## Hotkeys
- `F3` — Start configured worker.
- `F4` — Stop configured worker.

## Troubleshooting
- If Camfrog not detected: ensure process running and version supported.
- If friend missing: verify exact account identifier and authentication.
- If permission denied: run as non-administrator.
- Logs: `%LOCALAPPDATA%\ZChat\keep_log.txt`, `evidence/` directory (when evidence collection is implemented).

## Rollback
- Stop worker (F4), close client, remove evidence directory if needed.
- Revert `.env` to `ZCHAT_VERIFICATION_ENABLED=false`.
