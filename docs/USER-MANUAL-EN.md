# Camfrog User Manual

Thai version: `USER-MANUAL-TH.md`.

Eight apps, one manual.

Apps: `room-control`, `chat-im-private`, `status-random`, `status-marquee`, `status-changer`, `im-autoreply`, `music-dj`, `web-status`. All are Windows-only and need Camfrog open, except `web-status` which works through the website.

**Updates.** Every app checks the project's GitHub release when it starts (a banner appears only when something newer exists) and each has a **Check for updates** button; the CLI equivalent is `<app> update`. An update downloads only that app's own file, verifies it against the release's `SHA256SUMS.txt`, and installs it the next time you close the app (`<app>.new` + `camfrog-update.cmd` do the swap). No account, no token, nothing is uploaded. Releases are published with `python tools/release.py`.

## 1. Install

1. Install standard CPython 3.12 64-bit.
2. Run `doctor.bat` first. It checks your Python, flags bad setups, and tests the `pywin32` download.
3. Double-click `build.bat` for the eight Camfrog apps, or run `full-build.ps1` in PowerShell to build them with the LINE app. Executables land in `dist\<app>\`.
4. If the build says `pywin32 ... no matching distribution`, your Python has no wheel: install 64-bit Python 3.13, delete `.venv`, rerun.

## 2. First run

1. Open Camfrog and join a room (not tray-minimized).
2. Open the app after Camfrog is running. Every standalone Camfrog app attempts control discovery and saves valid selector proposals automatically; Web Status uses website controls and is excluded. Use **Setup → Auto-detect** or **Discover controls** to retry. Verify the selected controls before enabling live actions.
   Keep only the intended Camfrog client process within the configured window-title filter. If matching windows belong to multiple or unverifiable processes, the desktop apps stop instead of guessing which account to control.
3. Keep `dry_run` ON while checking behavior. Dry-run only logs what *would* be sent.
4. Keep dry-run enabled while checking behavior. Strict status submission returns `BACKGROUND_UNSUPPORTED`. The opt-in `best_effort_background` route may activate or flash Camfrog and does not verify provider delivery. Interactive submission waits for user inactivity before using desktop input. `web-status.exe` is a separate website-based feature; see `docs/STATUS-SUBMIT-MODES.md`.

## 3. Room Control

- Set your nickname (`My nickname`) — required for live mode.
- **Window title regex** picks which Camfrog window to drive.
- Auto-reply rules: pattern (regex), reply text, per-sender cooldown, mention-only, language (`th`/`en`). Test offline with **Test rule** before going live.
- **Save & Start** runs the hidden background bot; **Stop** ends it. Closing the window does NOT stop the bot.
- Spam guard: links are ignored by default (`ignore_links`), plus `skip_patterns`.

## 4. IM Auto-reply

- Separate app, separate `config.json`, separate rules. Room auto-reply never touches private chats.
- **Mandatory friend allowlist** (`only_nicknames`): only listed nicknames are ever answered.
- Stricter limits by default (one reply per friend per hour, 3 per day, 10 per hour). Every reply starts with `[auto] `, and such lines are never answered.
- Windows already open when the bot starts are never answered.
- After a live reply, IM Auto-reply looks for the outgoing line in that private chat's local history and scrolls it into the chat viewport. This is local UI evidence only; Camfrog exposes no recipient-delivery acknowledgement here. A send that fails before submission stays pending for the next polling pass; an ambiguous send is not repeated automatically to avoid duplicates. Set `safety.restore_previous_window` to `false` if you want the Camfrog chat window left in front.
- The **Activity Log** tab follows the configured app log. It records application open/close, config saves/reloads, Start/Stop and helper results, worker lifecycle, IM filters, unmatched rules, previews, replies, and failures with available error details. It refreshes while the GUI is open. The **Log chat text** setting in Advanced controls whether message contents appear in worker entries; turn it off to keep message text out of the log. Each app keeps its own log file and worker.
- Only one worker can run for this app's data folder. The Activity Log records when an incoming message has no matching reply rule or is filtered.

## 5. Chat IM Private

- Lists private-chat windows already open in Camfrog. **Refresh** reloads, double-click or **Activate selected** focuses one. It never reads or sends messages.
- **Window title filter** (regex) + **Max windows** limit the list; **Save filters** stores them in this app's own config.
- **Discover controls** retries discovery and dumps the control tree to `controls.txt` for diagnostics.

## 6. Status Changer: Random, Marquee, and Activity Log

- `status-changer.exe` puts Random and Marquee in separate tabs with separate ten-slot pools. It reuses existing mode pool databases when it finds their data folders. The legacy `status-random.exe` and `status-marquee.exe` remain available for compatibility.
- **Random:** each switch picks a status from its ten slots. **Switch every** is in seconds and has a minimum of 10 seconds.
- **Marquee:** **Step** sets seconds per frame and the delay between whole-line slots when Scroll is off (minimum 0.3 seconds; the GUI adjusts in 0.1-second increments, with a 0.5-second default); **Stride** sets character clusters per frame, and **Scroll** turns frame scrolling on or off. Every frame is an external status submission. Marquee follows the populated slots in order; History source cannot reorder them. With scrolling on, each populated slot completes its frames before the next slot starts. **Loop slots** restarts at slot 1 after the last populated slot; with it off, the sequence stops after the last slot. Thai vowel/tone clusters stay together. **Switch every** applies to Random only.
- **Start** starts the selected mode; its first status change is sent after 10 seconds. Only one status worker can run at a time. The window shows which mode is active. **Stop** ends that worker.
- **Activity Log** combines GUI actions with both worker logs. GUI events omit status text; worker logs label a local dispatch separately from provider verification. Use **Open log folder** to inspect the files.

## 7. Status history, schedules, language

- Long statuses scroll across a window of `width` characters: **Step** (seconds per frame, minimum 0.3, adjusted in 0.1-second increments), **Stride** (clusters per frame), **Loop** (repeat without settling). With **Scroll** off (default), each pool line is applied whole, one per tick, rotating 1..N and wrapping.
- Random switches and automatic retries remain spaced at least 10 seconds apart. Marquee frames follow the configured Step (minimum 0.3 seconds); the first status change waits 10 seconds after startup. Start with dry-run.
- Thai-safe: never splits vowel/tone clusters, never emits a blank frame.
- Use `status-changer.exe` for the combined Random and Marquee interface with an Activity Log. It stores its settings, pools, and logs separately under `status-data\`; only one status mode runs at a time.

## 8. Status history, schedules, language

- Every status you set is saved to history; with `use_as_source` the next status is picked from it (`rotate`/`random`/`most_used`).
- `status.schedules` uses different pools by time of day; entries need `start`/`end` as `HH:MM`.
- `status.language_cycle: ["th","en"]` alternates Thai/English on each change. Status items can be `{th, en}` pairs.
- `"language": "auto"` follows the Windows UI language.

## 8. Music DJ

- A room bot that also plays music: it answers song requests from chat and announces what is playing. It needs Camfrog open and `autoreply` enabled (the DJ reuses the chat plumbing); set `dj.enabled`.
- Chat commands, prefix `!` by default: `!request <song>` (or `!req`, `!song`), `!randommusic`, `!queue`, `!current` (or `!np`), `!skip`, `!help`. `!randommusic` posts a random YouTube music search and saves it in `youtube_random_music.json` (up to 200 entries). `!current` reports the requester, elapsed/total playback time when available, and next queued song. The Music tab can speak custom text or the current-song summary with Windows TTS; route Windows playback output to the Camfrog microphone input with a virtual cable or Stereo Mix. The queue view in the app mirrors the file, with **Skip current** / **Clear queue** owner buttons.
- The first request starts playing immediately; each extra song is queued (`max_per_user` per person, duplicates and the currently playing song refused). `!skip` works for the requester or you (the bot owner).
- Music folder: use **Browse** to choose it; `!request` matches supported audio filenames (substring, case-insensitive). The app searches common audio extensions including WAV, MP3, WMA, AAC/M4A, AIFF/AU, FLAC, OGG/Opus, and MIDI.
- **Audio backend** `chat` (default) only announces in the chat. `local` plays audio files that Windows MCI can open and starts the next song when playback finishes. `youtube` accepts a YouTube video URL or ID in `!request`, opens the standard visible YouTube player in a separate Edge/Chrome window, announces after playback starts, and advances when YouTube reports the video ended. You may need to press **Play** if autoplay is blocked. For either player to be heard in the room, route this PC's output into Camfrog's input with a virtual cable or "Stereo Mix". Playback support depends on Windows MCI, browser, device, and codec setup.
- Start with dry-run: the bot then only logs the announcements it would send. Requests still queue, and nothing is typed into the room.
- In **Bot Manager → Music DJ**, choose `music-dj.exe` (the packaged zip layout is detected automatically when available). The page can open the Music DJ configuration window, start or stop its worker, request queue status, skip or clear, and send up to 240 characters to the worker's Windows TTS voice output. The Music DJ worker remains the only owner of chat commands and queue state; when installing the apps separately, use **Browse** to select the executable.
- **YouTube assisted search** opens a visible Edge or Chrome search window with its own browser profile. Choose a result, then copy its URL into `!request`. The queue player uses YouTube's standard visible controls and detects playback/end events. The separate profile is stored under `%LOCALAPPDATA%\Camfrog\MusicDJ\YouTubeProfile` and is not shared with Camfrog browser login. See [`zBOT-Youtube-MUSIC-DJ.md`](zBOT-Youtube-MUSIC-DJ.md) for limits and acceptance checks.

## 10. Web Status

Status: login flow mapped; the status-update endpoint was captured from a logged-in `profiles.camfrog.com/home.php` session (POST `{status, csrf}` to `/ajax/update_status.php`) and stays gated. CLI live sends need `--confirm-update`; GUI live Start asks for confirmation.

- No args opens the GUI; any args use the CLI (`web-status.exe --login Seaza --status "..."`). Dry-run is default — no network at all.
- The resizable window starts at 400×300 and can shrink to 300×220. A title screen shows the Idol artwork with the Zttato logo and **Loading pages...**, then counts down from five and closes itself to reveal Setup. Click the **Z** logo for full screen; **800x600** restores that window size. **Tray** hides the window in the Windows notification area; click its icon to reopen or right-click it to exit.
- In Setup, enter up to ten statuses in the numbered single-line fields. **Save to webtext.db** saves each slot beside the app; **Start** also saves the current values and rotates through non-empty slots every 60 seconds by default until you press **Stop**.
- In **Account & Session**, press **Open login page**, sign in in your browser, then copy `PHPSESSID=...` from the `profiles.camfrog.com/home.php` request in DevTools and paste it into the session field. Press **Login** to verify the current cookie; a signed-in result arms automation. For local development, `WEBAUTH_USER=PHPSESSID=REPLACE_ME` in the ignored `.env` file pre-fills the masked field; it does not verify or arm the session automatically. The cookie is never sent to the ZChat server.
- With **Send live** enabled, **Start** verifies the current cookie if Login has not already armed the session, asks for confirmation, and begins rotation. **Stop** cancels the loop and disarms the session, so the next Start verifies again. Dry-run remains the default.
- The GUI live rotation loop spaces Random submissions at least 10 seconds apart. Marquee frames use the configured Step (minimum 0.3 seconds), and the first status change waits 10 seconds after startup; HTTP 429 retries also wait at least 10 seconds. Separate Web Status instances and changes made directly on Camfrog are not coordinated. Dry-run preview timing is unchanged.
- Before each live POST, Web Status checks that the signed-in profile nickname matches the selected nickname and that the page provides a provider user ID. A mismatch or missing identity blocks the POST. The ID is not yet bound to a ZChat account registry.
- A non-empty successful HTTP response means Camfrog acknowledged the request; Web Status does not independently read the profile back to prove the value changed. After a timeout, server error, or unrecognized response, the outcome is unknown. Check the profile before retrying.
- The separate **Bot Manager** tab accepts a `cloudbot=...` parameter, verifies it against `www.camfrog.com`, and loads **Room**, **Profile**, and **Plugins**. For local development, `WEBAUTH_BOT_USER=cloudbot=REPLACE_ME` in the ignored `.env` file pre-fills the masked field; click **Connect** to verify it. The cookie is sent only to Camfrog. **Disconnect** clears this app's session; **Log out** ends the remote session.
- **Room** edits the room name, room password, bot language, and pause state. **Profile** edits the account password and email. **Stop bot** sends Camfrog's stop command after confirmation.
- **Plugins** lists the installed plugins returned by Camfrog (the standard catalog uses IDs 1–9 and 11–16) with activation switches. Change switches and press **Apply changes** to submit them after confirmation; the app reloads the catalog so you can review Camfrog's returned states. Select a plugin card to load its current settings form. The form maps Camfrog's text fields, text areas, checkboxes, radio groups, single and multiple selections, and file uploads to editable controls. Saving asks for confirmation, submits the form, then reloads it so you can review the values Camfrog returned. Known plugin names, field labels, and choices follow the TH/EN language setting; submitted option values remain unchanged, and free-text messages stay as entered. Shared formatting supports font, size, color, bold, italic, underline, and strikeout.
- **Local vMix control** reads inputs from the vMix API on this PC. **Preview input** updates Preview; **Cut to output** sends the selected input directly to vMix output after a confirmation. This does not configure Camfrog's camera. Install the vMix virtual-camera device and select it manually in Camfrog, then verify it in a controlled call. The controls are limited to loopback addresses; they do not control a remote vMix host.

| Plugin | Settings exposed by Camfrog |
|---|---|
| 1. 8 ball | Bot name and response message |
| 2. IM control | Owner list |
| 3. Messenger | Message and display interval |
| 4. Greeter | Role greetings and role/gender matching switches |
| 5. Math trivia | Question duration, timeout, notifications, answer reveal, number range, operator level, and enabled operations |
| 6. Bad words | Rules, violation threshold, punishment reason, regular-expression mode, and kick/ban switch |
| 7. Trivia | Question duration, timeout, notifications, answer reveal, and quiz file upload |
| 8. Word trivia | Question duration, timeout, notifications, answer reveal, and quiz file upload |
| 9. Roulette | Message, start interval, unavailable-game notice, and timer |
| 11. Ghost | Question timing, notifications, answer reveal, and quiz file upload |
| 12. Who banned | Role message, room/moderator notifications, and log retention |
| 13. Talkback | Rule list |
| 14. Dice | Die type and number of dice |
| 15. DJ Manager | Microphone duration, moderator-only access, and notifications |
| 16. Auto Moderator | Nickname rules and reports before punishment |

This feature configures Camfrog's server-installed plugins; it does not create or run custom local plugin code. The available plugins and fields depend on what Camfrog returns for the connected account.
- CLI password login can be blocked by CAPTCHA; the tool does not bypass it. For CLI use, `--probe` is read-only and `--cookies-file` accepts a Netscape cookie export. Never paste passwords or cookie values into chat, issues, or files.

## 11. Background mode, logs, files

- Room/IM: GUI **Save & Start** / **Stop**, or CLI `start` / `state` / `stop` (hidden, no window). Status apps: own **Start**/**Stop** + bundled worker.
- Each app writes logs beside its own config (`camfrog_auto.log` for source CLI). Hot reload: editing `config.json` while running applies valid changes in ~3 s; invalid edits are rejected and logged.
- The combined Status Changer Activity Log includes GUI actions and worker output. Completed status text appears when `log_message_text` is enabled. Its activity and worker logs are cleared daily at 12:00 local time while the app is running; if closed then, cleanup runs on the next launch.
- Strict background submission remains unsupported and returns `BACKGROUND_UNSUPPORTED`. The opt-in `best_effort_background` mode posts Enter to the status combo but may activate or flash Camfrog; provider state remains unverified. Interactive paths wait for 2 seconds of user inactivity (`ZCHAT_USER_IDLE_MS` overrides this; 0 disables the wait) and defer after 120 seconds of continued activity. Status worker startup creates the data folder and waits for verified PID authorization before entering its loop.
- Other status sends may require Camfrog in the foreground. Camfrog must be open (minimized is OK only if controls are still reachable — verify with Discover).

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| `pywin32 ... no matching distribution` | Install CPython 3.12 64-bit, delete `.venv`, rerun `build.bat`. |
| Auto-detect finds nothing | Open a chat room (not tray-minimized) and retry; then Discover controls. |
| Old `requirements.txt (line 1)` pywinauto-only error | You run an older copy — extract the newest zip into a new empty folder. |
| Status worker refuses a live request | Strict mode is expected to return `BACKGROUND_UNSUPPORTED`. Choose `best_effort_background` only if possible Camfrog activation is acceptable; provider delivery remains unverified. Interactive mode waits for user inactivity but can still affect focus. |
| Error sound on every status change | Try **combo Enter (test)** in dry-run mode first. Review the activity log before enabling live status changes. |
| Wrong/old window driven | Fix **Window title regex**, re-run Auto-detect. |
| Explorer shows old icon after rebuild | Windows icon cache: rename/move the exe or restart Explorer. |

## 13. Safety

- `dry_run` is on by default; replies are rate-limited (per-sender cooldown, global gap, hourly cap).
- Lines not in `name: message` form are ignored; replies starting with `/` are blocked; there is no auto-kick.
- Never commit passwords, cookies, tokens, or personal data. Cookie exports stay outside the repo.
- Check Camfrog's terms and your room's rules before enabling automation. Report vulnerabilities privately per `SECURITY.md`, never in public issues.

## 14. ZChat Control Server

The control server is a separate local-first operator tool, not a Camfrog client. Its PostgreSQL database runs in a local Docker volume and the dashboard/API bind to loopback. Windows agents report only sanitized snapshots and execute supported official-client handoffs; the server does not receive Camfrog passwords, browser cookies, CSRF values, CAPTCHA responses, or payment credentials. Live authenticated profile fetching and Cloud Bot transport are not enabled. Follow [`control-server/README.md`](../control-server/README.md) for local setup and the current integration limits. This MVP is not production-ready.
