# Camfrog standalone feature apps

Windows only. Uses UI Automation. Selector defaults were derived from static analysis of Camfrog 8.5.0.51219; standalone Camfrog apps now attempt discovery and save valid selectors on startup. Open Camfrog first; use **Auto-detect** or **Discover controls** to retry, and verify selectors before enabling live actions. Limited Windows 11 smoke testing verified the IM local-echo path, but not recipient delivery or every control.

Full user manual: `docs/USER-MANUAL-EN.md` (Thai: `docs/USER-MANUAL-TH.md`).

## Standalone feature apps

Each executable is built into its own folder and creates its own config and runtime files there. Config, logs, status pools, and history are not shared between the apps. The combined Status Changer keeps Random and Marquee pools separate and reuses an existing mode pool when it can find one.

| App | Scope |
|---|---|
| `room-control\room-control.exe` | Room control and room auto-reply. Status changes and private IM reply are disabled. |
| `chat-im-private\chat-im-private.exe` | Lists and focuses private-chat windows that are already open in Camfrog. It does not read or send messages. |
| `status-changer\status-changer.exe` | One window with **Random**, **Marquee**, and **Activity Log** tabs. Random and Marquee keep separate config files, ten-slot pools, and worker settings. |
| `status-random\status-random.exe` | Random status rotation with its own ten-slot pool under `random-data\`. |
| `status-marquee\status-marquee.exe` | Marquee status rotation with its own ten-slot pool and Loop control under `marquee-data\`. |
| `status-changer\status-changer.exe` | Combined Random and Marquee modes with an Activity Log, stored under its own `status-data\` folder. |
| `im-autoreply\im-autoreply.exe` | Private IM auto-reply only. Room auto-reply and status changes are disabled. |
| `music-dj\music-dj.exe` | ZeaZDev room music DJ: answers `!request` / `!randommusic` / `!queue` / `!current` / `!skip` / `!help`, keeps a persistent queue, saves generated music searches in `youtube_random_music.json`, and supports local audio or a visible YouTube player. `!current` reports requester, playback time, and next song. The Music tab can speak custom text or the current queue summary through Windows TTS. Status changes and private IM reply are disabled. |
| `web-status\web-status.exe` | GUI + console prototype: update status via profiles.camfrog.com (dry-run by default; the GUI verifies a browser `PHPSESSID` session before live rotation). No args opens the GUI; any args use the CLI. The GUI has ten one-line status fields saved in `webtext.db`; **Start** rotates through the non-empty lines every N seconds. |

The Web Status **Bot Manager** also controls the separate `music-dj.exe` queue through local files and can read/control vMix on the same PC. Music DJ can queue YouTube videos by URL or ID in a visible standard player and advances from player events. See [`docs/zBOT-Youtube-MUSIC-DJ.md`](docs/zBOT-Youtube-MUSIC-DJ.md) for boundaries and Windows acceptance checks.

Room Control and IM Auto-reply use separate `config.json` files; Chat IM Private uses `private-chat-config.json`; Random and Marquee each use `camfrog-status-config.json` in their own data folder. Random and Marquee also keep their pools, logs, history, and worker files separate. Every standalone Camfrog app attempts control discovery after its window opens and saves selector proposals only when configuration validation succeeds. Web Status uses website controls and is excluded. Status **Start** creates the data folder, launches the bundled worker, and waits for PID registration and authorization before status work begins.

## Build

Double-click `build.bat` (needs 64-bit Python in PATH). It selects Python 3.12 first and builds the eight apps above into separate subfolders under `dist\`. It preserves each app's local config and runtime data when refreshing the binaries, copies no shared `config.json`, and creates a zip containing only the executables and checksums. It also writes `dist\SHA256SUMS.txt`.

Run `full-build.bat` or `full-build.ps1` to build the feature apps and LINE app. Releases are opt-in; the PowerShell script accepts `--release vX.Y.Z` only when it matches `APP_VERSION`.

- **Icon:** replace `app.ico` (a real multi-size `.ico`, ideally 16–256 px) and rebuild. If Explorer still shows the old icon it is the Windows icon cache: rename/move the exe or restart Explorer.
- **Checksums & zip:** `SHA256SUMS.txt` lists each executable's path inside `dist\`; `tools\package.py` creates it and the zip using Python. Verify an app with `certutil -hashfile dist\room-control\room-control.exe SHA256`.

### Build troubleshooting

Run **`doctor.bat`** first. It prints your Python version/bit/build type, flags known-bad setups, detects an old copy of this project, and tests whether pip can fetch `pywin32`.

`pywin32 ... no matching distribution` means pip could not install a dependency for the selected interpreter. This project supports only standard CPython 3.12 64-bit. Fix: install `winget install Python.Python.3.12`, delete the `.venv` folder, then run `build.bat` again.

If the error says `requirements.txt (line 1)` and mentions only pywinauto, you are running an **older copy** of the project — extract the newest zip into a new empty folder.

## Quick start

1. Open Camfrog and join a room before launching a standalone Camfrog app; it attempts control discovery automatically. Use **Setup → Auto-detect** or **Discover controls** to retry. Findings: `docs/CAMFROG-8.5-FINDINGS.md`.
2. Edit that app's `config.json`, set your nickname, and configure room replies. Keep `dry_run` on while checking behavior.
3. Use `im-autoreply.exe` for private IM auto-reply; its config and logs are separate from Room Control.
4. Use `status-random.exe` or `status-marquee.exe` for separate status apps, or `status-changer.exe` for their combined interface and Activity Log. Each has its own data folder. Strict background submission still stops with `BACKGROUND_UNSUPPORTED`. The opt-in `best_effort_background` mode posts Enter to Camfrog's status combo, but Camfrog may activate or flash and provider delivery remains unverified. Interactive sends use desktop input and wait for user inactivity before proceeding. See [`docs/STATUS-SUBMIT-MODES.md`](docs/STATUS-SUBMIT-MODES.md). `web-status.exe` is separate.
5. Open a private chat in Camfrog, then use `chat-im-private.exe` to refresh and focus its window. It attempts selector discovery on startup; **Discover controls** remains available to dump the control tree to `controls.txt` for diagnostics.

`python camfrog_auto.py ...` remains available as a source-only developer CLI. It uses the local `config.json`, which is ignored by Git; create it with `python camfrog_auto.py init --config config.json` or copy `config.example.json`. CI and builds validate the safe example. The CLI is not one of the eight packaged apps. The `extras\*.bat` helpers are source-checkout shortcuts, except `extras\gui.bat` which launches the built `dist\room-control\room-control.exe`.

The source-only `python camfrog_presence.py probe NICKNAME` command makes one bounded probe call and stores public Camfrog status-image evidence in a local SQLite database. It always classifies presence as `UNKNOWN`; see [`docs/CAMFROG-PRESENCE.md`](docs/CAMFROG-PRESENCE.md) before interpreting samples.

## ZChat Control Server

The separate local-first control plane uses FastAPI and a persistent PostgreSQL Docker volume bound to this machine. It provides an operator dashboard, account and agent registry, leased commands, audit events, and sanitized profile/presence snapshots. Live authenticated Camfrog fetching, Cloud Bot transport, Store checkout, and production deployment remain unverified or disabled. See [`control-server/README.md`](control-server/README.md) for setup, API scope, and limits.

## Background mode

Room Control and IM Auto-reply use the GUI **Save & Start** and **Stop** controls. Status apps use their own **Start** and **Stop** controls and bundled background worker.

| Command | Description |
|---|---|
| `python camfrog_auto.py start --config config.json` | Source CLI: run hidden, no window |
| `python camfrog_auto.py state --config config.json` | Source CLI: show if running |
| `python camfrog_auto.py stop --config config.json` | Source CLI: graceful stop |

Each app writes logs beside its own config. The source CLI uses `camfrog_auto.log` in the repository folder.

**Status submission:** Strict background mode returns `BACKGROUND_UNSUPPORTED` before writing or submitting because Camfrog 8.5 has no verified non-activating route. `best_effort_background` posts Enter to the status combo without global keyboard or mouse input, but Camfrog may activate or flash; dispatch and local readback do not prove provider delivery. Interactive paths using focus, mouse, or real keystrokes wait for 2 seconds of user inactivity (`ZCHAT_USER_IDLE_MS` overrides this; 0 disables the wait) and defer after 120 seconds of continued activity. This reduces collisions but does not make submission non-activating. Worker startup creates its data directory and waits for PID authorization before entering the status loop. Room-chat replies use a different embedded input and require the room window to be foreground.

## Scrolling status + history switch

- **Marquee** (`status.marquee`): long statuses scroll across a window of `width` characters, one frame every `step_seconds` (minimum 0.3 s; the GUI adjusts in 0.1 s steps). Each populated slot finishes its frames before advancing to the next slot. **Loop slots** starts again at slot 1 after the last populated slot; with it off, the sequence stops after the last slot. The default advances 2 Thai-safe clusters every 0.5 seconds; the runner wakes at each frame deadline without an extra random delay after sending.
- **History switch** (`status.history`): every status you set is saved to `status_history.json`. With `use_as_source: true` the next status is picked from that history (`rotate` = round-robin, `random`, `most_used`). Your `status.messages` are seeded into it automatically.
- **TH/EN auto switch** (`status.language_cycle: ["th","en"]`): alternates Thai, English, Thai, English… each time the status changes. Works with history and with plain messages.
- **Safety:** every frame is a real status change on Camfrog, so frames are rate-limited (`step_seconds >= 0.3`). Keep `interval_seconds` generous for Random mode and start with `dry_run: true`.
- **Live status send:** status apps start in dry-run mode. **Save Data** only saves settings. Strict live submission returns `BACKGROUND_UNSUPPORTED`; `best_effort_background` is opt-in and may activate or flash Camfrog, with provider outcome unverified. Interactive submission waits for desktop inactivity before using focus or real input. `web-status.exe` uses a separate website flow with separate authentication and verification limits.
- **Background status option:** choose `strict_background` to fail closed or explicitly opt into `best_effort_background` to post to Camfrog's combo control with possible activation. See `status.execution_mode` in `CONFIG-EN.md` and `docs/STATUS-SUBMIT-MODES.md`.

Preview offline without opening Camfrog:
```
python camfrog_auto.py marquee "a long status that scrolls" --width 20
python camfrog_auto.py history                       # list history
python camfrog_auto.py history-add "hello"            # add
python camfrog_auto.py history-import old_status.txt # one status per line
```

## Extra tools

| Command | Description |
|---|---|
| `python camfrog_auto.py init --config config.json` | Source CLI: write a starter config |
| `python camfrog_auto.py test-rules "text" --config config.json` | Source CLI: test reply rules offline |
| `python camfrog_auto.py state --config config.json` | Source CLI: running state and counters |

- **Hot reload:** edit `config.json` while running; valid changes apply within ~3 s, invalid ones are rejected and logged (setting `dry_run` to `false` without `own_nickname` is rejected).
- **Variables:** `{sender}` `{me}` `{time}` `{date}` in statuses and replies.
- **Schedules:** different status pools by time of day (`status.schedules`).
- **Mention rules:** `"mention": true` replies only when your nick appears in the message.
- **Spam guard:** `ignore_links` (default on) and `skip_patterns`.

## Development

Install `requirements-dev.txt` and run the offline test suite from the source checkout. CI (`.github/workflows/ci.yml`) runs tests and template validation on Ubuntu, then builds the Camfrog and LINE apps on Windows. Audit notes: `AUDIT.md`. Full user manual: `docs/USER-MANUAL-EN.md`.

## Language

`"language": "auto"` follows the Windows UI language. Override with `--lang th` or `--lang en`. Status items can be `{th, en}` pairs and replies follow the language of the incoming message — see `CONFIG-EN.md`.

## Private-message auto-reply (opt-in)

Use `im-autoreply.exe`; its `config.json` is private to that app. Room auto-reply never touches private chats. IM auto-reply has its own rules, a **mandatory friend allowlist** (`only_nicknames`), its own dry-run and stricter limits (default: one reply per friend per hour, 3 per day, 10 per hour). Every reply starts with `[auto] `, and lines carrying that prefix are never answered. Windows already open when the bot starts are never answered; a window that opens later with one line from a listed friend is.

Set `autoreply.own_nickname`, `autoreply_im.only_nicknames`, and `autoreply_im.rules` in that app's config. Keep `dry_run: true` until you have checked its behavior, then enable live mode from this app only when ready.

Windows 11 smoke evidence: the worker attaches to the selected private-chat window and its outgoing local echo is present in that window's MSHTML history. The app now scrolls that row into the chat viewport. This does not prove that Camfrog delivered it to the recipient; the UI exposes no delivery acknowledgement. The `wb-log-mtim-data` window kind is deliberately ignored, and `private_chat_request` is never auto-accepted. See `CONFIG-EN.md`.

## Safety

`dry_run` is on by default; replies are rate-limited (per-sender cooldown, global gap, hourly cap); lines not in `name: message` form are ignored; replies starting with `/` are blocked; there is no auto-kick. Check Camfrog's terms and your room's rules before enabling automation.
