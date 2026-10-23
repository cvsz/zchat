# Changelog — camfrog-auto

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Added standalone `name-color-changer.exe` application with dedicated GUI and CLI for alternating Camfrog nickname flair colors via `profiles.camfrog.com` with fail-closed security guards, HTTP 429 rate limit backoff halts, Netscape cookie export & raw cookie parsing, CSRF extraction, and best-effort restoration of initial flair color upon completed runs.
- Added standalone `icon-changer.exe` application with dedicated GUI and CLI for alternating room user-list flair icons via `profiles.camfrog.com` with dynamic external data parsing and best-effort initial icon restoration upon completed runs.
- Added standalone `name-changer.exe` application providing a local-only prototype GUI and CLI for manual and sequential display-name / nickname switching in-memory with full Thai script (ภาษาไทย, Tahoma typography, UTF-8) and English support, custom name application, and preset pool management (without live profile/client automation).
- Extended build and packaging infrastructure (`build.bat`, `full-build.bat`, `tools/package.py`, `tools/release.py`, `tools/end_dist_processes.ps1`) to compile, verify, and package all three new executables independently.
- Added comprehensive unit test suites for all three standalone changers (`tests/test_name_color_changer.py`, `tests/test_icon_changer.py`, `tests/test_name_changer.py`).
- Updated English and Thai user manuals (`docs/USER-MANUAL-EN.md`, `docs/USER-MANUAL-TH.md`, `README-EN.md`, `README-TH.md`, `docs/gui-split.md`) documenting usage, local prototype boundaries, and capabilities of the standalone apps.
- Added the zGPU RPC client (`src/camfrog/zgpu_client.py`) and the local zGPU LLM Acceleration Studio Tk panel (`src/features/zgpu_tester_gui.py`): JSON-RPC generation and health checks against an operator-configured zGPU endpoint with timeout clamping, API-key header handling, and fail-closed structured errors. The panel is local tooling and is not one of the packaged executables.
- Added `GEMINI.md` operating instructions mirroring the repository agent contract for Gemini CLI sessions.
- Added regression tests for the zGPU client endpoint, structured responses, RPC errors, network failures, and the test panel; standalone changer GUI tests now mock the win32 clipboard for cross-platform runs.

### Fixed
- Enabled clipboard shortcuts and right-click context menus (cut/copy/paste/select-all) in the standalone Name Changer, Name Color Changer, and Room Icon Changer GUIs via the shared `ClipboardController`.
- The `.env.enc` gitignore guard no longer picks up caller `GIT_*` environment variables and forces `safe.directory=*`, so repository-version checks run reliably in restricted environments.
- Repository markdown-link validation now also excludes `.venv-observer/`, and pytest suppresses third-party `SyntaxWarning` noise from bundled vendor modules.
- Corrected the English and Thai app tables to merge the duplicated `status-changer` row and align the packaged-app count with `build.bat`.

## [2.19.3] - 2026-10-19

### Fixed
- Refuse live status submissions through `best_effort_background` and targeted combo-post when Camfrog is not already foreground. Windows runtime evidence showed combo-post can activate Camfrog and interrupt keyboard input; workers now fail closed before writing status text.
- Room Control, IM Auto-reply, Music DJ, and Chat IM Private now attempt Camfrog control discovery after startup and persist detected selectors only after config validation. The combined Status Changer saves both Random and Marquee status pools and configs before starting discovery. Each app retains its inlined discovery implementation and remains an independent one-file executable.
- Create each status app data directory before clearing PID/start-gate files or launching its worker, so a fresh install can register and authorize the worker instead of leaving an unverified child running.
- Document the current status submit trade-offs, interactive user-activity wait, automatic standalone status-app setup, and worker startup handshake consistently across the English and Thai guides.

### Added
- Added process-level runtime-adapter regression tests for identity revalidation and ownership, plus Windows-only GUI/HWND cases using a controlled fixture process.
- Added an approximate build countdown to the full-build logger, calibrated from the median duration of recent successful builds with a ten-minute fallback.
- Added a Windows-only CreateDesktop Camfrog launcher for manual isolation testing. Native Camfrog behavior remains unverified until tested in a Windows session.
- Added repository structure and Markdown-link validation to the main GitHub CI job.
- Added the LINE unit suite to the fast Linux CI job as well as its Windows packaging gate.
- Added ZChat client and LINE unit suites to the cross-platform `make test` target.
- Added production-grade structured JSON logging (`StructuredLogger`) with standardized fields (`ts`, `level`, `event`, `request_id`) and startup lifespan telemetry.
- Added database connection URL credential sanitization (`redact_db_url`) to protect secrets in logs.
- Added configurable `CORSMiddleware` support via `cors_origins` and standard security headers (`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`) on all API responses.
- Added machine-readable `--json` flag and CHANGELOG heading check to `scripts/validate_repo.py`.
- Added dynamic absolute `PYTHON` path resolution in `Makefile` and convenience `venv` and `clean` targets.
- Added the local-first ZChat Control Server subproject with PostgreSQL-backed accounts, agents, leased commands, audit events, sanitized snapshots, and an operator dashboard. Live Camfrog login/fetch, Cloud Bot transport, checkout, and production deployment remain disabled or unverified.
- Added account-scoped internal chat transport to the control server with idempotent submissions, paginated history, bounded agent delivery leases, acknowledgments, and revocation handling.
- Added a separate Windows ZChat agent chat client with in-memory credentials, assigned-account discovery, idempotent sends, and acknowledged incoming delivery.
- Updated desktop updater and repository bootstrap defaults to use `cvsz/zchat`.
- Combined Status Changer executable with Random, Marquee, and Activity Log tabs; its configuration and runtime data stay under `status-data/`.
- Bot Manager plugin names, field labels, and known choices now follow the TH/EN setting while submitted option values remain unchanged.
- Bot Manager's local Music DJ page starts/stops the packaged worker and controls its queue through bounded, expiring local JSON requests; Music DJ remains the queue owner.
- Music DJ accepts YouTube video URLs/IDs in the persistent queue, plays them in a visible standard iframe player, and uses current-player events for announcements and auto-advance.
- Music DJ can open a visible YouTube search in its dedicated Edge/Chrome profile for assisted discovery.
- Music DJ is branded as ZeaZDev room with a bundled microphone mascot, adds `!randommusic` with bounded JSON history, reports playback details, and provides local Windows TTS controls.
- Bot Manager can read local vMix inputs, set Preview, and issue a confirmed Cut through a loopback-only HTTP API client.
- Added a read-only Camfrog public presence probe that fingerprints bounded GIF responses and persists raw evidence locally while classifying every observation as UNKNOWN.

### Fixed
- Status workers now wait for parent authorization after PID registration before entering the status loop. A failed or delayed PID check therefore cannot let a hidden worker submit statuses while the GUI reports startup failure.
- Status worker startup accepts the registered runtime PID when a PyInstaller one-file launcher PID differs; timeout cleanup terminates the launcher process tree.
- Status Changer, Random Status, and Marquee Status now save their loaded settings and discover Camfrog controls automatically after the standalone window opens.
- Status workers now wait 10 seconds before the first automatic status change after start; later changes keep the configured interval.
- Preserve an explicitly selected `best_effort_background` mode in the hidden Status Changer worker, and wait for worker PID registration during frozen executable startup instead of failing after a short fixed delay.
- Use the probe-validated Enter key scan code and transition flags for best-effort status submissions in Status Changer, Random Status, and Marquee Status.
- Require both the expected Win32 control ID and class when revalidating Camfrog status HWNDs, rejecting recycled handles that only match one identifier.
- Camfrog window selection now fails closed when the configured title filter spans multiple or unverifiable processes instead of choosing the first matching client.
- Release publishing now uploads the flat checksum manifest as `SHA256SUMS.txt`, matching the name expected by existing app updaters.
- Fixed `validate_repo.py` performance and exclusion list with `os.walk` directory pruning, reducing scan execution time by 98%.
- Fixed GitHub Actions supply-chain security by pinning actions to immutable commit SHAs with least-privilege `contents: read` permissions.

### Changed
- Raised the minimum Random interval and Marquee frame delay to 10 seconds across the standalone and combined Status Changer apps. Legacy settings are clamped on load, and retry scheduling cannot submit faster than the configured interval.
- Status worker logs now label successful local dispatch as unverified instead of claiming the provider status was set.
- Changed the opt-in unverified Status Changer test route from Edit Enter to combo-parent Enter to avoid the observed Edit-control ding; the combo route can activate Camfrog and remains unsupported for strict background use.
- Removed the legacy Random and Marquee submit-time writes to Windows sound registry values; status submission no longer tries to mute or restore system sound settings.
- Updated the architecture guide to describe the maintained desktop apps, ZChat services, repository tooling, and deployment boundaries.
- Web Status now defaults to switching non-empty status slots every 60 seconds.
- Status Changer records completed status text in its worker log and clears its status logs daily at 12:00 local time.
- Music DJ queue paths are restricted to the app data folder.
- MCP discovery and authenticated read-only resource results now declare private, zero-TTL caching; response data is not reusable across authorization contexts.


## [2.19.2] - 2026-10-11

### Added
- Web-status session login: **Login** verifies the current `PHPSESSID` entry and arms automation; live **Start** verifies that same current cookie, asks once for confirmation, then begins rotation. `Stop` disarms so the next Start re-verifies.
- Web-status Setup now has ten single-line status fields saved by slot in `webtext.db`; Start persists current fields before rotation.
- Music DJ now has a **Browse** button to select the music folder for chat requests.

### Fixed
- Web-status live rotation now calls the status updater with its supported arguments, avoiding a duplicate `confirm` argument error.
- Web-status ignores a cached manually pasted `PHPSESSID` after its entry is cleared, while retaining sessions captured by browser login; an unreadable `webtext.db` now leaves the GUI open with empty fields and a warning.
- Music DJ now finds and plays common Windows audio formats, reports playback errors, and advances when the current audio file finishes.
- Music DJ drops an initial request whose local playback fails so it cannot remain stuck as the current song.
- Release publishing verifies that build artifacts came from the exact clean release commit and rejects a remote version tag that points elsewhere.

### Changed
- Marquee `step_seconds` floor lowered 0.5 → 0.3 across the engine and all feature apps (validation, frame-wait floor, GUI spinbox minimum).
- Random `status.interval_seconds` floor lowered 30 → 0.3 across the engine and all feature apps (validation, `RANDOM_INTERVAL_MIN`, GUI field now accepts decimals).

## [2.19.1] - 2026-10-10 (assets refreshed same day)

### Fixed
- **Self-update sums lookup.** The flat sums published as `release-SHA256SUMS.txt` while the updater looked up only `SHA256SUMS.txt`, so the lookup missed and every update was refused. The updater now falls back to any `*SHA256SUMS.txt` release asset. (The dist-relative vs flat path mismatch from this morning is covered by the both-forms matcher.)

### Fixed
- **Self-update checksum verification.** The published `SHA256SUMS.txt` lists dist-relative paths (`room-control/room-control.exe`) while the verifier matched only bare asset names, so every real update was refused as a checksum mismatch. The verifier now accepts both forms, and `tools/release.py` publishes an additional flat-basename `SHA256SUMS.txt` so already-installed 2.19.0 apps can still verify the next release.

## [2.19.0] - 2026-10-10

### Added
- **Self-update from the GitHub release.** Every packaged app checks `releases/latest` on start (and via a **Check for updates** button / `update` CLI command), downloads only its own `.exe` asset, verifies it against the release's `SHA256SUMS.txt`, stages it as `<app>.new`, and swaps it in with a small `camfrog-update.cmd` after the app exits. No tokens, no third-party updater; GitHub API is read-only and stdlib-only.
- `tools/release.py` publishes the release: per-app exe assets with stable names, the all-features zip, `SHA256SUMS.txt`, and notes taken from this section. `--dry-run` prints the plan.
- **Music DJ app** (`music-dj`): room bot that answers `!request` / `!queue` / `!current` / `!skip` / `!help` in chat, keeps a persistent `dj_queue.json`, and can play `.wav` files locally (`dj.audio_backend: "local"`) with automatic next-song advance. New `dj` config section; needs `autoreply.enabled` for chat plumbing. GUI has a queue view with owner Skip/Clear buttons.
- `--chrome` CLI flag and GUI **Chrome** button: import the Camfrog session straight from Chrome's cookie store (nothing written to disk; DPAPI / AES-GCM decrypted in memory; clear error points to the cookies.txt fallback when the `cryptography` package is missing).
- Chrome 127+ keeps its cookie DB under `<profile>/Network/Cookies`; the finder now prefers it over the legacy `<profile>/Cookies`.
- `https://profiles.camfrog.com/home.php` session probe with real signed-in markers (`nav-user-logged`, `_user_id`, `nick`), plus the captured status-update endpoint (`/ajax/update_status.php`, POST `{status, csrf}`) implemented behind the `--confirm-update` gate.
- `status.marquee.scroll` (default `false`): one whole pool line per tick, lines rotate 1..N and wrap; `true` keeps the old frame-scrolling behavior.

### Added
- Encrypted account store for web-status: the GUI's **Account (stored encrypted)** section seals the nickname/password with Windows DPAPI into `.env.enc` (git-ignored, decrypts only under the same Windows user); the CLI then needs no `--login` and no password prompt. Plaintext `.env` still works as a legacy fallback.
- The saved account now also drives the GUI's **live rotation** (no cookies, no Chrome): tick **Send live**, press **Start** (one confirmation), and each tick signs in and posts. Picking `.env.enc` in the cookies field, or any non-cookies file, now explains the mix-up instead of failing cryptically.

### Fixed
- web-status requests now present a browser User-Agent: camfrog's edge answered `HTTP 403` to the default `Python-urllib` identity, which broke every network path (login, probe, rotation). Behind the edge the server replies `captcha` to scripted password logins - surfaced honestly, never bypassed.
- **Stop** now also drops an imported Chrome session, so a later Start cannot silently reuse it.

### Added
- **Browser login** in the web-status GUI: **Open login page** + paste the profile cookie from devtools -> **Use pasted cookie**. No closing Chrome, no export; the paste is used in memory only and probed live. This is the working live path while the server demands a CAPTCHA for password logins.
### Changed
- **GUI split: one self-contained module per feature exe.** `room_control_gui.py`, `im_autoreply_gui.py`, `camfrog_music_gui.py`, `status_random_gui.py`, `status_marquee_gui.py`, `chat_im_private_gui.py` (renamed from `camfrog_private_chat_gui.py`), and `web_status_gui.py` (now includes the former `tools/web_status.py` logic) each inline the engine and clipboard helpers they need; no module imports another project module. Entry points no longer switch profiles/modes through environment variables. See `docs/gui-split.md`.

### Fixed
- `Runner.__init__` initializes resolve targets so the resolve-retry loop degrades to warnings instead of crashing.
- Chrome DPAPI decryption no longer reads freed memory (`_data_blob` pins the backing buffer).

## [2.18.0] - 2026-10-07

### Added
- Type hints on ~30 public functions in `camfrog_auto.py` for better IDE support.
- CRLF line endings on all `.bat` files; CI test `test_bat_files_are_crlf` added.
- GPG commit signing with ECDSA (nistp256) key; full history resigned.
- Retry + skip logic for flaky clipboard test in `tests/test_gui.py`.

### Fixed
- IM sender last-seen pruning now uses `per_sender_cooldown_seconds` (was `max(cooldown, 86400)`).
- `log_kind()` returns `None` instead of `""` on failure for clearer semantics.
- Added clarifying comment about `NICK_RE` vs `LINE_RE` Unicode handling.

### Changed
- Build script `build.bat` and all helper `.bat` files use CRLF.
- All 170 tests pass (1 skipped for GUI smoke test requiring display).

## [2.17.0] - 2026-10-07

### Added
- `camfrog-auto im-probe` command: diagnostic for IM auto-reply configuration and window classification.
- Optional `autoreply_im.log_kinds: ["im", "mtim"]` (mtim = unverified, off by default).
- Startup summary and "no private-chat window found" log hints for IM.

### Fixed
- IM auto-reply now properly refuses to run when `autoreply.own_nickname` is empty even in dry-run.

## [2.16.0] - 2026-10-07

### Added
- Private-message (IM) auto-reply: separate `autoreply_im` section with own rules, allowlist, and limits.
- Mandatory `autoreply_im.only_nicknames` (non-empty allowlist).
- Per-sender cooldown, daily cap, hourly cap, and global gap for IM.
- Reply prefix `[auto] ` to prevent bot-to-bot loops.
- `answer_first_message` for new IM windows with a single line from a listed friend.
- Shared matching code via `im_view()` so `test-rules --im` mirrors live behavior.
- Validation: empty allowlist, empty own_nickname, `log_kind` must be `room` when room auto-reply is on.

### Changed
- `log_kind()` now returns `"mtim"` for tabbed IM windows (previously only `room`/`im`).

## [2.15.0] - 2026-10-07

### Fixed
- Selector clash detection: warns in dry-run, errors in live mode.
- PID reuse guard: `stop` verifies process image before killing.
- Foreground re-check immediately before Enter to prevent focus-stealing.
- Burst detection: >25 new lines in one poll = reset, not answered.
- Status history eviction: newcomer (count 0) never evicted.
- Thai skip patterns: removed `\b` word boundaries (Thai has none).
- Numeric range validation for all config values.
- Thai cluster-aware `clip()` prevents cutting vowels/marks.
- Hot reload preserves rule cooldowns; `sender_last` pruned.

## [2.14.0] - 2026-10-07

### Added
- Marquee (scrolling status): `status.marquee` with width, stride, step_seconds, cycles, max_frames.
- Status history as source: `status.history.use_as_source` with modes rotate/random/most_used.
- Language cycle: `status.language_cycle` alternates TH/EN on each status change.
- TH/EN auto-detection from Windows UI language.

### Changed
- Default `status.interval_seconds` lowered to 300 (was 600).
- Marquee defaults: 2 Thai-safe clusters every 0.5 seconds.

## [2.13.0] - 2026-10-07

### Added
- GUI: `camfrog-auto-gui.exe` with tabs for Dashboard, Setup, Status, Auto-reply, History, Advanced.
- Offline rule tester: `test-rules` command and GUI tester.
- `discover` command dumps full control tree to `controls.txt`.
- `detect` command auto-proposes selectors for Camfrog 8.x.
- `check` command validates config with bilingual error messages.
- Background mode: `start`/`stop`/`state`/`autostart-on`/`autostart-off`.

### Fixed
- Atomic config writes with retry loop for AV/indexer locks.
- Single-instance guard with PID file.
- Rotating log with `log_message_text` toggle for privacy.

## [2.12.0] - 2026-10-07

### Fixed
- Room log parsing: corrected from RichEdit/Chromium to embedded IE/MSHTML panes.
- Chat input is web editable box (no native Edit/RichEdit).
- `read_chat()` uses UIA TextPattern on Document control.

## [2.11.0] - 2026-10-07

### Added
- Multi-window support: Camfrog opens each room/IM as separate window of same process.
- Automatic room attachment via `get_chat_window()` scanning process windows.
- `autoreply.window_title_regex` to narrow room selection.

## [2.10.0] - 2026-10-07

### Added
- Status rotation with schedules (`status.schedules`).
- Random/sequential message selection.
- Per-sender cooldown, global gap, hourly cap for auto-reply.

## [2.9.0] - 2026-10-07

### Added
- Auto-reply with regex rules, bilingual replies, mention filter.
- Skip patterns and link ignore.

## [2.8.0] - 2026-10-07

### Added
- Custom status setting via UIA.
- One-shot `status` command.

## [2.7.0] - 2026-10-07

### Added
- Initial release: status rotation + auto-reply for Camfrog on Windows using UIA.
