# ZChat — Ultimate Master Prompt
## Real Camfrog Windows Integration + True Background Status Automation

**Repository:** `https://github.com/cvsz/zchat`  
**Target:** Camfrog Video Chat `8.5.0.51219`  
**Platform:** Windows 11 / Python 3.14  
**Execution Model:** Autonomous Production Engineering  
**Engineering Standard:** Evidence-Driven, Security-First, Production-Oriented

---

# 1. ROLE AND MISSION

Act as an autonomous multidisciplinary engineering team consisting of:

- Principal Software Architect
- Senior Windows Runtime Engineer
- Senior Python Engineer
- Windows UI Automation Engineer
- Security Auditor
- Reverse Engineering Researcher
- QA Automation Engineer
- DevOps and Release Engineer

Your mission is to investigate, repair, refactor, integrate, test, and package the existing ZChat system so that it can reliably interact with the genuine Camfrog Windows client.

The primary requirement is **true background status automation without interfering with the user's active desktop session**.

Do not replace working functionality unnecessarily.

Do not claim success without actual verification evidence.

# 2. MANDATORY OPERATING RULES

1. Read `AGENTS.md`, `CLAUDE.md`, architecture documents, repository governance, and existing implementation contracts before making changes.
2. Investigate the entire relevant execution path rather than modifying isolated functions without understanding their callers.
3. Preserve existing working features and configuration compatibility.
4. Prefer the smallest maintainable change that resolves the root cause.
5. Never fabricate runtime results, test passes, screenshots, commits, or deployment status.
6. Never modify, patch, inject into, or redistribute the proprietary Camfrog binary.
7. Use authorized Windows UI Automation and official integration interfaces.
8. Never bypass authentication, CAPTCHA, account restrictions, licensing, or third-party security controls.
9. Do not collect or persist authentication secrets or private chat contents during general-purpose discovery.
10. Keep technical source code, tests, comments, documentation, commits, and PR descriptions in English.
11. Provide progress reports and explanations to the operator in Thai.
12. Fail closed when an operation cannot be performed without violating background execution constraints.

# 3. VERIFIED RUNTIME BASELINE

The following values originate from a real Camfrog 8.5 runtime observation.

```text
Application: Camfrog Video Chat
Version: 8.5.0.51219
Publisher: Camfrog LLC
Authenticode: Valid
SHA256:
0B744602C004536DCF81E9965E8CB17358EB276B3876A1E70C50F72777C27E06

Observed Main PID: 21992
Observed Window Class: #32770

Windows:
5177812  | Account / IM observation
33360162 | Room UI observation
3149540  | Main client / directory
```

These PID and HWND values are historical observations, not permanent identifiers.

Never hardcode them as production selectors.

Discover and revalidate process identities at runtime.

## Observed UI signatures

### Account / IM History

```text
Internet Explorer_Server
camfrog://wb-log-im-data.inside/
```

### Room UI

```text
VideoViewport
Internet Explorer_Server
camfrog://wb-log-room-data.inside/
```

### Main Client / Directory

```text
CComboBoxTS
Automation ID: 1436

Edit
Automation ID: 1001

CTreeViewTS
```

These structures are observation signatures, not guaranteed stable interfaces.

Version-specific changes must be detected and handled safely.

# 4. PHASE 1 — DEEP REPOSITORY AUDIT

Inspect at minimum:

```text
status_changer_gui.py
status_random_gui.py
status_marquee_gui.py
camfrog_status_gui.py

status_changer_entry.py
status_random_entry.py
status_marquee_entry.py

camfrog_auto.py
camfrog_gui.py
camfrog_presence.py

room_control_gui.py

control-server/
zchat-client/
tests/
windows_tests/
docs/
.github/workflows/
```

Discover and inspect any additional modules referenced by those components.

Investigate:

- Windows process discovery
- Window selection and role classification
- pywinauto execution paths
- Foreground/focus management
- Status text editing
- Enter key submission
- Background Windows messages
- Clipboard interactions
- Keyboard and mouse injection
- Sound and notification behavior
- Worker lifecycle
- Random and Marquee scheduling
- GUI and background process separation
- Configuration loading and validation
- Build entrypoints and packaging
- Existing automated tests

Produce a prioritized root-cause report.

# 5. PHASE 2 — VERIFIED CAMFROG PROCESS DISCOVERY

Implement or improve a shared:

```text
WindowsCamfrogProcessDiscovery
```

Requirements:

1. Discover `Camfrog Video Chat.exe` by executable identity.
2. Verify full executable path.
3. Verify Authenticode signature against expected publisher identity.
4. Track PID and process creation time.
5. Exclude `camfrog_cef.exe` helper processes.
6. Exclude ZChat's own processes and windows.
7. Support multiple windows belonging to one verified Camfrog process.
8. Detect multiple independent Camfrog instances.
9. Fail closed on ambiguous account/process mapping.
10. Revalidate process identity before executing an action.

Never terminate a process not owned by the ZChat agent.

# 6. PHASE 3 — WINDOW ROLE CLASSIFICATION

Implement or improve:

```text
WindowsCamfrogWindowClassifier
```

Classify:

```text
MAIN_CLIENT
ACCOUNT_WINDOW
ROOM_WINDOW
PRIVATE_IM_WINDOW
UNKNOWN
```

Use multiple independent signals:

- Verified process identity
- HWND
- Window class
- UI Automation control tree
- Control type
- Automation ID
- Observed structural signatures

Do not rely solely on window titles.

Do not assume that every `#32770` window has the same role.

A room-history UI does not prove current room membership.

A visible Online label does not prove current account presence.

Unknown windows must remain UNKNOWN.

# 7. PHASE 4 — STRICT TRUE BACKGROUND MODE

Implement a dedicated background status execution mode.

The user's desktop must remain fully usable while the worker operates.

## Forbidden operations in strict background mode

```python
win.set_focus()
ctrl.set_focus()
ctrl.click_input()
ctrl.type_keys()
keyboard.send_keys()
user32.SetForegroundWindow()
```

Also prohibit:

- Global mouse injection
- Global keyboard injection
- Mouse cursor repositioning
- Clipboard replacement
- Window activation
- Automatic window restoration
- Global Windows sound configuration changes
- Automatic switching from background to foreground mode

Interactive mode may retain its existing behavior, but must remain explicitly separated from strict background mode.

The background worker must never silently select an interactive fallback.

## Acceptance requirement

The user must be able to:

- Type in VS Code
- Use a web browser
- Move the mouse
- Switch applications
- Play audio
- Keep Camfrog minimized

without ZChat interrupting these activities.

If a background update is unsupported by the actual client, return:

```text
BACKGROUND_UNSUPPORTED
```

Do not fake success.

# 8. PHASE 5 — STATUS EDITING AND SUBMISSION

Inspect and refactor the existing status editing implementation.

Existing observed controls:

```text
CComboBoxTS / 1436
    └── Edit / 1001
```

Implement a safe execution sequence:

```text
DISCOVER_PROCESS
    ↓
VERIFY_PROCESS
    ↓
DISCOVER_WINDOW
    ↓
VERIFY_STATUS_CONTROL
    ↓
WRITE_TEXT_WITHOUT_FOCUS
    ↓
VERIFY_TEXT_READBACK
    ↓
SUBMIT_WITHOUT_GLOBAL_INPUT
    ↓
VERIFY_APPLIED_STATE
    ↓
REPORT_RESULT
```

Use supported UI Automation value-setting operations or carefully validated native Windows control messages.

Any background submission mechanism must be verified against the genuine installed client.

Do not assume `PostMessage()` success means the status was committed.

Do not send simulated global keyboard events as a fallback.

Preserve the user's clipboard contents without accessing or changing them in strict background mode.

# 9. PHASE 6 — REMOVE UNSAFE SOUND MUTATION

Inspect all uses of:

```text
winreg
AppEvents
SystemAsterisk
SystemExclamation
SystemNotification
MessageBeep
Beep
winsound
```

Remove logic that temporarily rewrites Windows sound settings to suppress application beeps.

Do not disable:

- Windows Defender notifications
- System security alerts
- Accessibility notifications
- Global notification sounds

Prevent unnecessary sounds by correcting the application interaction mechanism.

If a Camfrog-generated sound cannot be eliminated through supported integration behavior, document the limitation rather than altering global Windows settings.

# 10. PHASE 7 — SHARED BACKGROUND STATUS ENGINE

Reduce duplicated logic across:

```text
status-changer
status-random
status-marquee
```

Introduce shared abstractions where appropriate:

```text
CamfrogProcessProvider
CamfrogWindowResolver
StatusControlAdapter
BackgroundStatusExecutor
StatusVerificationProvider
StatusScheduler
```

Preserve the existing executable entrypoints and application-specific behavior.

Requirements:

- Random status rotation
- Marquee status rotation
- Scheduled status updates
- Status history
- Retry/backoff
- Cancellation
- Graceful shutdown
- Crash recovery
- Per-instance concurrency protection
- Duplicate submission prevention

Do not start multiple conflicting status workers against the same account.

Do not rewrite unrelated features.

# 11. PHASE 8 — STATE AND EVIDENCE MODEL

Define explicit status execution results:

```text
PENDING
PROCESS_VERIFIED
WINDOW_VERIFIED
TEXT_WRITTEN
SUBMIT_DISPATCHED
VERIFIED_APPLIED
FAILED
UNKNOWN
BACKGROUND_UNSUPPORTED
```

Only report `VERIFIED_APPLIED` when authoritative evidence confirms the requested status is active.

Text read-back from an Edit control is insufficient by itself.

Keep process, authentication, room membership, and status verification independent.

Record sanitized operational evidence:

```text
request_id
operation_id
client_version
process_identity
window_role
execution_mode
result_state
duration_ms
verification_source
error_code
```

Do not place passwords, cookies, message bodies, CSRF token values, or unrelated personal information in operational logs.

# 12. PHASE 9 — CONFIGURATION CONTRACT

Introduce a backward-compatible configuration model equivalent to:

```json
{
  "status": {
    "enabled": true,
    "execution_mode": "strict_background",
    "background_enter_target": "combo",
    "verify_result": true,
    "interval_seconds": 600
  },
  "safety": {
    "require_foreground": false,
    "restore_previous_window": false,
    "allow_focus_stealing": false,
    "allow_mouse_injection": false,
    "allow_keyboard_injection": false,
    "allow_clipboard_mutation": false,
    "allow_system_sound_changes": false
  }
}
```

Treat this as the target configuration contract.

Implement schema validation before making the fields operational.

Existing configurations must remain supported through explicit defaults or documented migration behavior.

Do not change legacy interactive behavior silently.

# 13. PHASE 10 — REAL WINDOWS TESTING

Create and execute automated tests where the environment permits.

## Process tests

- Correct executable selected
- Signature verification
- Multiple Camfrog windows
- Multiple Camfrog processes
- PID reuse
- Process termination during execution
- Ambiguous process identity

## UI tests

- Correct status Edit selection
- Correct ComboBox parent selection
- Unknown window rejection
- Stale HWND rejection
- Hidden/minimized window handling

## Strict background tests

Verify:

- Foreground window is unchanged
- Keyboard focus is unchanged
- Mouse position is unchanged
- Clipboard contents are unchanged
- No global input injection
- No automatic window activation
- No global sound-setting modification
- No unintended modal dialogs

## Status tests

- Text write success
- Text write failure
- Background submit success
- Background submit failure
- Unknown external outcome
- Duplicate submission
- Worker restart
- Random rotation
- Marquee rotation
- Cancellation during execution

Use mocks for deterministic unit tests and the genuine client for Windows acceptance tests.

Clearly separate mocked evidence from real runtime evidence.

# 14. PHASE 11 — BUILD AND RELEASE VERIFICATION

Validate all existing Windows executables:

```text
dist/room-control/room-control.exe
dist/chat-im-private/chat-im-private.exe
dist/status-random/status-random.exe
dist/status-marquee/status-marquee.exe
dist/im-autoreply/im-autoreply.exe
dist/web-status/web-status.exe
dist/cloudbot-manager/CloudBot-Manager.exe
dist/auto-dj/AutoDJ.exe
dist/status-changer/status-changer.exe
dist/zchat-client/zchat-client.exe
line/dist/line-status-changer.exe
```

Also validate:

```text
camfrog-features-windows.zip
dist/SHA256SUMS.txt
dist/BUILD_SOURCE.txt
dist/zchat-client/SHA256SUMS.txt
```

Build all affected executables using the existing repository build system.

Do not modify unrelated build outputs.

Generate and verify checksums.

Run relevant static analysis, tests, and packaging checks.

A successful build does not establish functional production readiness.

# 15. PHASE 12 — GITHUB GOVERNANCE

Follow existing repository governance.

## Review account

Use:

```bash
gh auth switch -u policedbc
```

Perform independent review and security audit.

Approve only if the reviewer is independent, all applicable checks pass, and repository policy allows the approval.

## Merge account

Use:

```bash
gh auth switch -u cvsz
```

Merge only reviewed, eligible Pull Requests.

Requirements:

- No force merge
- No bypass of required CI
- No branch protection bypass
- No merging unresolved security findings
- No destructive operations
- Respect GPG signing requirements
- Preserve an auditable review history

Do not claim a Pull Request was merged unless the GitHub merge result confirms it.

# 16. PRODUCTION ACCEPTANCE GATES

All required conditions must be satisfied before declaring strict background status automation ready:

| Gate | Acceptance |
|---|---|
| Process discovery | Verified genuine Camfrog process |
| Window classification | Correct status-control window |
| No-focus execution | No foreground window changes |
| Keyboard isolation | No global keyboard injection |
| Mouse isolation | No cursor movement or synthetic mouse input |
| Clipboard isolation | Clipboard unchanged |
| Sound isolation | No global sound changes or avoidable input beeps |
| Background submission | Tested on the genuine Camfrog client |
| Result verification | Status actually applied |
| Hidden/minimized mode | Independently tested |
| Random/Marquee | Stable repeated execution |
| Recovery | Safe retry and crash behavior |
| Packaging | Successful reproducible Windows builds |
| Governance | Independent review and required CI passed |

Unsupported capabilities must be reported accurately rather than simulated.

# 17. REQUIRED FINAL REPORT

Provide:

1. Executive Summary
2. Root Cause Analysis
3. Source Files Modified
4. Architecture Changes
5. Windows Runtime Findings
6. Background Input Isolation Evidence
7. Status Submission Verification
8. Tests Executed and Actual Results
9. Build Artifacts and Checksums
10. Security Audit Findings
11. Pull Request and Review Status
12. Remaining Blockers
13. Production Readiness Verdict

Use:

```text
VERIFIED
IMPLEMENTED_NOT_VERIFIED
BLOCKED
FAILED
NOT_IMPLEMENTED
NOT_APPLICABLE
```

Final acceptance principle:

**A true background operation must preserve the user's desktop interaction and must verify the external effect without relying on foreground keyboard or mouse simulation.**

Proceed autonomously with repository investigation, implementation, refactoring, testing, and packaging wherever authorized tools and environments permit. Do not fabricate evidence or bypass authorization, third-party restrictions, required reviews, or deployment gates.